package com.endsight.market;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.authlib.exceptions.AuthenticationException;
import com.mojang.authlib.minecraft.MinecraftSessionService;
import net.fabricmc.loader.api.FabricLoader;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Properties;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

final class MarketApi {
    private MarketApi() {}

    private static final Logger LOG = LoggerFactory.getLogger("dragsim_market");

    private static final String BASE = "https://dsmnet.xyz/market/link";
    static final URI SITE = URI.create(BASE + "/");
    private static final Path DIR = FabricLoader.getInstance().getConfigDir().resolve("dragsim-market");
    private static final Path CREDENTIAL = DIR.resolve("account.properties");
    private static final HttpClient HTTP = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(6)).build();
    private static final ScheduledExecutorService WORKER = Executors.newSingleThreadScheduledExecutor(job -> {
        Thread thread = new Thread(job, "dragsim-market");
        thread.setDaemon(true);
        return thread;
    });

    private record Session(UUID uuid, String player, String token) {}
    private static volatile Session session;
    private static volatile UUID active;
    private static volatile String rejectedToken;

    static void init() {
        if (Files.isRegularFile(CREDENTIAL)) {
            try (var input = Files.newInputStream(CREDENTIAL)) {
                var properties = new Properties();
                properties.load(input);
                var uuid = UUID.fromString(properties.getProperty("uuid", ""));
                var player = properties.getProperty("player", "");
                var token = properties.getProperty("token", "");
                if (!player.isBlank() && !token.isBlank()) {
                    session = new Session(uuid, player, token);
                }
            } catch (IOException | IllegalArgumentException ignored) {
                // The client can request a new code if its previous link file is unreadable.
            }
        }
        WORKER.scheduleWithFixedDelay(MarketApi::drain, 8, 20, TimeUnit.SECONDS);
    }

    static void useAccount(UUID uuid) { active = uuid; }

    private static Session current() {
        Session known = session;
        return known != null && known.uuid.equals(active) && !known.token.equals(rejectedToken) ? known : null;
    }

    static boolean ready() { return current() != null; }
    // Trade uploads come with the link. The on/off switch was taken out on purpose (2026-11-16), so
    // every linked account builds its flip history; an old account.properties "tracking" line is ignored.
    static boolean tracking() { return current() != null; }

    static CompletableFuture<String> link(UUID uuid, String player, String minecraftToken, MinecraftSessionService mojang) {
        var start = new JsonObject();
        start.addProperty("player", player);
        return retryOnce(() -> call(postRequest("/api/start", start, null), null)).thenComposeAsync(reply -> {
            String challenge = reply.get("challenge").getAsString();
            if (!challenge.matches("[0-9a-f]{40}")) throw new Failure(502);
            try {
                mojang.joinServer(uuid, minecraftToken, challenge);
            } catch (AuthenticationException e) {
                throw new Failure(403);
            }
            var verify = new JsonObject();
            verify.addProperty("challenge", challenge);
            return call(postRequest("/api/verify", verify, null), null);
        }, WORKER).thenApplyAsync(reply -> {
            String code = reply.get("code").getAsString();
            String verifiedPlayer = reply.get("player").getAsString();
            String marketToken = reply.get("token").getAsString();
            if (!code.matches("[0-9A-F]{8}") || !player.equalsIgnoreCase(verifiedPlayer) || marketToken.isBlank()) {
                throw new Failure(502);
            }
            var linked = new Session(uuid, verifiedPlayer, marketToken);
            try {
                save(linked);
            } catch (IOException e) {
                throw new Failure(507);
            }
            publish(linked);
            return code;
        }, WORKER);
    }

    private static void save(Session linked) throws IOException {
        Files.createDirectories(DIR);
        var properties = new Properties();
        properties.setProperty("uuid", linked.uuid.toString());
        properties.setProperty("player", linked.player);
        properties.setProperty("token", linked.token);
        // createTempFile uses restrictive permissions where the filesystem supports them.
        Path temp = Files.createTempFile(DIR, "account-", ".tmp");
        try {
            try (var output = Files.newOutputStream(temp)) {
                properties.store(output, "DSM client credential");
            }
            Files.move(temp, CREDENTIAL, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } finally {
            Files.deleteIfExists(temp);
        }
    }

    static CompletableFuture<JsonObject> get(String path) {
        Session known = current();
        if (known == null) return CompletableFuture.failedFuture(new Failure(401));
        return retryOnce(() -> call(request(path, known).GET().build(), known));
    }

    static CompletableFuture<JsonObject> catalog() {
        // Reuse the profile browser's catalog when present, including its in-flight request.
        try {
            @SuppressWarnings("unchecked")
            var shared = (CompletableFuture<JsonObject>)Class.forName("com.endsight.profile.ProfileClient")
                    .getMethod("catalog").invoke(null);
            return shared;
        } catch (ReflectiveOperationException | LinkageError ignored) {
            HttpRequest request = HttpRequest.newBuilder(URI.create("https://dragsim.co/data/catalog.json"))
                    .timeout(Duration.ofSeconds(25)).GET().build();
            return HTTP.sendAsync(HttpCompression.request(request), HttpCompression.handler()).thenApply(response -> {
                if (response.statusCode() != 200) throw new Failure(response.statusCode());
                return JsonParser.parseString(response.body()).getAsJsonObject();
            });
        }
    }

    static CompletableFuture<List<JsonObject>> quotes(List<JsonObject> items) {
        Session known = current();
        if (known == null) return CompletableFuture.failedFuture(new Failure(401));
        JsonObject body = new JsonObject();
        JsonArray array = new JsonArray();
        items.forEach(array::add);
        body.add("items", array);
        return retryOnce(() -> call(postRequest("/api/quote", body, known), known)).thenApply(reply -> {
            List<JsonObject> estimates = new ArrayList<>();
            for (JsonElement e : reply.getAsJsonArray("estimates")) estimates.add(e.getAsJsonObject());
            return estimates;
        });
    }

    static void send(String path, JsonObject body) {
        Session known = current();
        if (known == null) return;
        var row = new JsonObject();
        row.addProperty("path", path);
        body.addProperty("player", known.player);
        row.add("body", body);
        WORKER.execute(() -> {
            try {
                Files.createDirectories(DIR);
                Files.writeString(outbox(known), row + "\n", StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE, StandardOpenOption.APPEND);
                drain();
            } catch (IOException ignored) {
                // A locked local config directory must not interrupt play.
            }
        });
    }

    private static Path outbox(Session known) { return DIR.resolve("outbox-" + known.uuid + ".jsonl"); }

    private static void drain() {
        Session known = current();
        if (known == null || !Files.isRegularFile(outbox(known))) return;
        try {
            List<String> lines = Files.readAllLines(outbox(known), StandardCharsets.UTF_8);
            int sent = 0;
            while (sent < lines.size() && current() == known) {
                HttpRequest queued = queuedRequest(lines.get(sent), known);
                if (queued == null) { sent++; continue; }
                int status;
                try {
                    status = HTTP.send(queued, HttpResponse.BodyHandlers.discarding()).statusCode();
                } catch (IOException e) {
                    break;
                }
                if (status == 401) reject(known);
                if (status < 200 || (status >= 300 && status < 400) || status == 401 || status == 429 || status >= 500) break;
                sent++;
            }
            if (sent > 0) {
                Path temp = outbox(known).resolveSibling("outbox-" + known.uuid + ".tmp");
                Files.write(temp, lines.subList(sent, lines.size()), StandardCharsets.UTF_8);
                Files.move(temp, outbox(known), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
            }
        } catch (IOException | RuntimeException ignored) {
            // Keep the queue on disk for the next attempt.
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static HttpRequest queuedRequest(String line, Session known) {
        try {
            JsonObject row = JsonParser.parseString(line).getAsJsonObject();
            String path = row.get("path").getAsString();
            if (path.equals("/api/trade") || path.equals("/api/event")) return postRequest(path, row.getAsJsonObject("body"), known);
        } catch (RuntimeException ignored) {
            // One interrupted disk write should not hold up the receipts after it.
        }
        return null;
    }

    private static HttpRequest.Builder request(String path, Session known) {
        var request = HttpRequest.newBuilder(URI.create(BASE + path)).timeout(Duration.ofSeconds(12));
        if (known != null) request.header("Authorization", "Bearer " + known.token);
        return request;
    }

    private static HttpRequest postRequest(String path, JsonObject body, Session known) {
        return request(path, known).header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body.toString())).build();
    }

    /** One more try when the request died on the connection rather than getting an answer.
     *  Since the site went behind Cloudflare, the first /dsm link of a session could fail in under
     *  a second with no status: the client reuses a pooled connection the edge had already closed
     *  while idle, and a POST is not resent on its own. Only reads, quotes and /api/start use
     *  this retry: they are safe to repeat. Trade/event uploads keep their own receipt queue. */
    private static CompletableFuture<JsonObject> retryOnce(Supplier<CompletableFuture<JsonObject>> attempt) {
        return attempt.get().handle((reply, error) -> {
            if (error == null) return CompletableFuture.completedFuture(reply);
            if (unwrap(error) instanceof Failure) return CompletableFuture.<JsonObject>failedFuture(error);
            LOG.info("DSM request dropped ({}), retrying once", unwrap(error).getClass().getSimpleName());
            return attempt.get();
        }).thenCompose(next -> next);
    }

    private static Throwable unwrap(Throwable error) {
        while (error.getCause() != null && !(error instanceof Failure)) error = error.getCause();
        return error;
    }

    private static CompletableFuture<JsonObject> call(HttpRequest request, Session known) {
        return HTTP.sendAsync(HttpCompression.request(request), HttpCompression.handler()).thenApply(reply -> {
            if (reply.statusCode() == 401) reject(known);
            if (reply.statusCode() != 200) throw new Failure(reply.statusCode());
            return JsonParser.parseString(reply.body()).getAsJsonObject();
        });
    }

    private static synchronized void publish(Session linked) {
        session = linked;
        rejectedToken = null;
    }

    private static synchronized void reject(Session known) {
        if (known != null && session != null && known.token.equals(session.token)) rejectedToken = known.token;
    }

    static String linkError(Throwable error) {
        error = unwrap(error);
        if (!(error instanceof Failure failure)) {
            // Chat only says "couldn't reach"; the log keeps what actually went wrong.
            LOG.warn("DSM link failed", error);
            return "Couldn't reach DSM. Try again shortly.";
        }
        return switch (failure.status) {
            case 401, 403 -> "Minecraft couldn't verify your account. Refresh your launcher login and try again.";
            case 429 -> "Too many link attempts. Wait a minute and try again.";
            case 507 -> "Couldn't save the link locally. Check that Minecraft can write its config folder.";
            default -> "DSM couldn't finish linking. Try again shortly.";
        };
    }

    private static final class Failure extends RuntimeException {
        final int status;
        Failure(int status) { this.status = status; }
    }
}
