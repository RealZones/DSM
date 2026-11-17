package com.endsight.market;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.mojang.serialization.JsonOps;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.item.v1.ItemTooltipCallback;
import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
import net.fabricmc.fabric.api.client.screen.v1.ScreenEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.ChatFormatting;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.core.component.DataComponents;
import net.minecraft.nbt.NbtOps;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.HoverEvent;
import net.minecraft.network.chat.Style;
import net.minecraft.network.chat.TextColor;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.ItemStack;

import java.net.URI;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static net.fabricmc.fabric.api.client.command.v2.ClientCommands.literal;

public final class Market implements ClientModInitializer {
    private static final int GOLD = 0xE6B45A, WHITE = 0xE9E7E1, GRAY = 0x9A9AA0, GREEN = 0x6FCE8B;
    private static final Pattern PLAYER = Pattern.compile("[A-Za-z0-9_]{1,16}");
    private static final Pattern TRADE_TITLE = Pattern.compile("^You\\s{2,}([A-Za-z0-9_]{1,16})$");
    private static final Pattern DONE = Pattern.compile("^(?:§.)*Trade successful with ([A-Za-z0-9_]{1,16})!");
    private static final Pattern SIDE = Pattern.compile("^§[ac]\\s+([+\\-])\\s(.*)$");
    private static final String COIN_AMOUNT = "([\\d,]+(?:\\.\\d+)?)\\s*([kmb]?)";
    private static final Pattern COINS_PREFIX = Pattern.compile("coins?\\s*:\\s*" + COIN_AMOUNT, Pattern.CASE_INSENSITIVE);
    private static final Pattern COINS_SUFFIX = Pattern.compile(COIN_AMOUNT + "\\s*coins?", Pattern.CASE_INSENSITIVE);
    private static final Pattern TRACKER_CHAT = Pattern.compile(
            "^(Purchased for |Claimed auction item|Price set to |Auction created successfully|Auction cancelled|Collected .* from your auction"
                    + "|Collected .* from the Forge|You applied the .* reforge|Applied a .*Book|You recombobulated|Upgraded your item to|Enriched your"
                    + "|You sold |You bought |Supercrafted |Bazaar order submitted|You don't have enough coins for the listing fee)");

    private static final LinkedHashSet<String> SEEN = new LinkedHashSet<>();
    private static UUID account;
    private static boolean linking, alerts = true, endsightQuiet, updateChecked;
    private static long nextDeals, nextFlips, lastFlip = -1;
    private static Receipt receipt;
    private static TradeWindow window;
    private static AbstractContainerScreen<?> tradeScreen;
    private static double[] tradeTotals;
    private static String quoteKey = "";
    private static long nextTradeQuote;

    private record TradeWindow(String other, long at, JsonObject detail) {}

    private static final class Receipt {
        final String other;
        final long at;
        final JsonArray got = new JsonArray(), gave = new JsonArray();
        final JsonObject detail;
        long lastLine;

        Receipt(String other, long at, JsonObject detail) {
            this.other = other;
            this.at = at;
            this.detail = detail;
            this.lastLine = at;
        }
    }

    @Override
    public void onInitializeClient() {
        MarketApi.init();
        ClientCommandRegistrationCallback.EVENT.register((dispatcher, context) -> dispatcher.register(literal("dsm")
                .executes(c -> status())
                .then(literal("link").executes(c -> link(c.getSource().getClient())))
                .then(literal("site").executes(c -> { show(site()); return 1; }))
                .then(literal("alerts").then(literal("on").executes(c -> alerts(true)))
                        .then(literal("off").executes(c -> alerts(false))))));
        ClientTickEvents.END_CLIENT_TICK.register(Market::tick);
        ClientTickEvents.END_CLIENT_TICK.register(MarketTooltip::tick);
        ClientTickEvents.END_CLIENT_TICK.register(BazaarOrders::tick);
        ItemTooltipCallback.EVENT.register(MarketTooltip::add);
        ClientReceiveMessageEvents.GAME.register((message, overlay) -> { if (!overlay) { chat(message); MarketTooltip.queueChat(message); } });
        ClientReceiveMessageEvents.CHAT.register((message, signed, sender, params, at) -> MarketTooltip.queueChat(message));
        ClientReceiveMessageEvents.GAME_CANCELED.register((message, overlay) -> { if (!overlay) chat(message); });
        ScreenEvents.AFTER_INIT.register((mc, screen, width, height) -> {
            if (!(screen instanceof AbstractContainerScreen<?> menu) || !onDragsim(mc)
                    || !TRADE_TITLE.matcher(menu.getTitle().getString()).matches()) return;
            tradeScreen = menu;
            tradeTotals = null;
            quoteKey = "";
            ScreenEvents.afterExtract(screen).register((s, graphics, x, y, delta) -> {
                if (s == tradeScreen && MarketApi.ready()) TradeTotals.draw(graphics, mc, menu, tradeTotals);
            });
            ScreenEvents.remove(screen).register(s -> {
                if (s == tradeScreen) tradeScreen = null;
            });
        });
    }

    private static int status() {
        show(Component.literal(MarketApi.ready() ? "Linked. " : "Use /dsm link to link your Minecraft account. ")
                .append(site()));
        return 1;
    }

    private static int link(Minecraft mc) {
        if (linking) {
            show(Component.literal("A link request is already running."));
            return 1;
        }
        var user = mc.getUser();
        if (user.getProfileId() == null || user.getAccessToken().isBlank()) {
            show(Component.literal("Linking requires a signed-in Minecraft account."));
            return 1;
        }
        linking = true;
        MarketApi.useAccount(user.getProfileId());
        show(Component.literal("Verifying your Minecraft account…"));
        MarketApi.link(user.getProfileId(), user.getName(), user.getAccessToken(), mc.services().sessionService())
                .whenComplete((code, error) -> mc.execute(() -> {
                    linking = false;
                    if (error != null) {
                        show(Component.literal(MarketApi.linkError(error)));
                        return;
                    }
                    mc.setScreen(new LinkScreen(mc.screen, code));
                }));
        return 1;
    }

    private static Component site() {
        return Component.literal("Open DSM").withColor(GREEN)
                .withStyle(Style.EMPTY.withClickEvent(new ClickEvent.OpenUrl(MarketApi.SITE)));
    }

    /** Once a game session: the site serves the newest jar and says its version, so a player on an
     *  older one hears about it in chat instead of never knowing an update exists. */
    private static void updateNotice(JsonObject status) {
        JsonElement latest = status.get("mod_version");
        if (latest == null || !latest.isJsonPrimitive()) return;
        String mine = FabricLoader.getInstance().getModContainer("dragsim_market")
                .map(c -> c.getMetadata().getVersion().getFriendlyString()).orElse("0");
        if (newer(latest.getAsString(), mine) <= 0) return;
        show(Component.literal("A new DSM mod is out (" + latest.getAsString() + ", you have " + mine + "). ")
                .append(Component.literal("Download it").withColor(GREEN)
                        .withStyle(Style.EMPTY.withClickEvent(new ClickEvent.OpenUrl(URI.create(MarketApi.SITE + "#/mod")))))
                .append(Component.literal(" and replace the old file in your mods folder.")));
    }

    /** Above 0 when a is the later version, comparing the dotted numbers in order. */
    static int newer(String a, String b) {
        String[] x = a.split("[.+-]"), y = b.split("[.+-]");
        for (int i = 0; i < Math.max(x.length, y.length); i++) {
            int p = i < x.length ? number(x[i]) : 0, q = i < y.length ? number(y[i]) : 0;
            if (p != q) return Integer.compare(p, q);
        }
        return 0;
    }

    private static int number(String part) {
        try { return Integer.parseInt(part); } catch (NumberFormatException e) { return 0; }
    }

    private static int alerts(boolean enabled) {
        alerts = enabled;
        show(Component.literal("Deal, Notifier and flip alerts " + (enabled ? "on." : "off.")));
        return 1;
    }


    private static ServerData checkedServer;
    private static boolean checkedDragsim;

    /** Asked for every tooltip and every tick, so the address is only parsed when the server changes. */
    static boolean tooltipOn() {
        Minecraft mc = Minecraft.getInstance();
        ServerData server = mc.getCurrentServer();
        if (server != checkedServer) {
            checkedServer = server;
            checkedDragsim = onDragsim(mc);
        }
        return checkedDragsim;
    }

    /**
     * Endsight draws its own price line on items. Two different numbers on one item would be worse
     * than either, and this one is tuned on real sales, so Endsight's goes quiet once this one is
     * live. Through reflection, because Endsight is optional and this mod has to load without it.
     */
    private static boolean quietEndsightPrices() {
        try {
            Class.forName("com.endsight.profile.TooltipPrices").getMethod("enabled", boolean.class).invoke(null, false);
        } catch (ReflectiveOperationException | LinkageError ignored) {
            // Endsight is not installed, or its tooltip moved. Nothing to switch off.
        }
        return true;
    }

    private static boolean onDragsim(Minecraft mc) {
        if (mc.getCurrentServer() == null) return false;
        String host = mc.getCurrentServer().ip.toLowerCase(Locale.ROOT).split(":", 2)[0].trim();
        return host.equals("dragsim.co") || host.equals("play.dragsim.co");
    }

    private static void tick(Minecraft mc) {
        UUID current = mc.getUser().getProfileId();
        if (!Objects.equals(current, account)) {
            account = current;
            MarketApi.useAccount(current);
            SEEN.clear();
            lastFlip = -1;
            nextDeals = nextFlips = 0;
            window = null;
            receipt = null;
        }
        long now = System.currentTimeMillis();
        if (receipt != null && now - receipt.lastLine > 400) flush();
        if (!MarketApi.ready() || mc.player == null || !tooltipOn()) return;
        if (!endsightQuiet) endsightQuiet = quietEndsightPrices();
        if (!updateChecked && onDragsim(mc)) {
            updateChecked = true;
            MarketApi.get("/api/status").whenComplete((reply, error) -> mc.execute(() -> {
                if (error == null) updateNotice(reply);
            }));
        }
        MarketTooltip.prefetch(mc, now);
        if (tradeScreen != null && mc.screen == tradeScreen) readTrade(mc, tradeScreen, now);
        if (alerts && now >= nextDeals) {
            nextDeals = now + 30_000;
            MarketApi.get("/api/deals").whenComplete((reply, error) -> mc.execute(() -> {
                if (error == null && alerts && Objects.equals(current, account) && onDragsim(mc)) deals(reply);
            }));
        }
        if (alerts && now >= nextFlips) {
            nextFlips = now + 45_000;
            MarketApi.get("/api/flips?limit=4&sort=time").whenComplete((reply, error) -> mc.execute(() -> {
                if (error == null && alerts && Objects.equals(current, account) && onDragsim(mc)) flips(reply);
            }));
        }
    }

    private static void deals(JsonObject reply) {
        for (JsonElement e : array(reply, "deals")) {
            JsonObject d = e.getAsJsonObject();
            double profit = d.get("profit").getAsDouble();
            if (profit < 50_000_000 || !unseen("deal:" + d.get("id").getAsString())) continue;
            var hover = Component.empty();
            JsonElement volume = d.get("units_sold_24h");
            if (volume != null && !volume.isJsonNull()) {
                hover.append(Component.literal(String.format(Locale.US, "AH volume (24h): %,d sold\n", volume.getAsLong())));
            }
            show(openAuction(Component.literal("Deal ").withColor(GOLD)
                    .append(Component.literal(d.get("name").getAsString()).withColor(WHITE))
                    .append(Component.literal(" " + MarketTooltip.coins(d.get("unit").getAsDouble())).withColor(GRAY))
                    .append(Component.literal(" +" + MarketTooltip.coins(profit)).withColor(GREEN)), d, hover));
        }
        for (JsonElement e : array(reply, "watch")) {
            JsonObject d = e.getAsJsonObject();
            if (!unseen("watch:" + d.get("id").getAsString())) continue;
            show(openAuction(Component.literal("Notifier ").withColor(GOLD)
                    .append(Component.literal(d.get("name").getAsString()).withColor(WHITE))
                    .append(Component.literal(" " + MarketTooltip.coins(d.get("unit").getAsDouble())).withColor(GREEN))
                    .append(Component.literal(" your price " + MarketTooltip.coins(d.get("price").getAsDouble())).withColor(GRAY)),
                    d, Component.empty()));
        }
    }

    private static Component openAuction(net.minecraft.network.chat.MutableComponent message, JsonObject d,
                                         net.minecraft.network.chat.MutableComponent hover) {
        JsonElement seller = d.get("seller");
        if (seller == null || seller.isJsonNull() || !PLAYER.matcher(seller.getAsString()).matches()) return message;
        String name = seller.getAsString();
        return message.withStyle(Style.EMPTY.withClickEvent(new ClickEvent.RunCommand("/ah " + name))
                .withHoverEvent(new HoverEvent.ShowText(hover.append(Component.literal("Open " + name + "'s auctions")))));
    }

    private static boolean unseen(String id) {
        if (!SEEN.add(id)) return false;
        if (SEEN.size() > 4096) SEEN.removeFirst();
        return true;
    }

    private static void flips(JsonObject reply) {
        JsonArray rows = array(reply, "flips");
        long top = lastFlip;
        for (int i = rows.size() - 1; i >= 0; i--) {
            JsonObject row = rows.get(i).getAsJsonObject();
            long id = row.get("id").getAsLong();
            top = Math.max(top, id);
            if (lastFlip < 0 || id <= lastFlip) continue;
            double profit = row.get("profit").getAsDouble();
            show(Component.literal("Flip closed ").withColor(GOLD)
                    .append(Component.literal(row.get("name").getAsString()).withColor(WHITE))
                    .append(Component.literal((profit >= 0 ? " +" : " -") + MarketTooltip.coins(profit))
                            .withColor(profit >= 0 ? GREEN : 0xEC7A70)));
        }
        lastFlip = top;
    }

    private static JsonArray array(JsonObject reply, String field) {
        JsonElement value = reply.get(field);
        return value != null && value.isJsonArray() ? value.getAsJsonArray() : new JsonArray();
    }

    private static void show(Component message) { Minecraft.getInstance().gui.getChat().addClientSystemMessage(message); }

    private static void chat(Component message) {
        if (!MarketApi.tracking() || !onDragsim(Minecraft.getInstance())) return;
        String text = legacy(message);
        Matcher done = DONE.matcher(text);
        long now = System.currentTimeMillis();
        if (done.find()) {
            flush();
            String other = done.group(1);
            JsonObject detail = window != null && window.other.equalsIgnoreCase(other) && now - window.at < 30_000
                    ? window.detail : null;
            receipt = new Receipt(other, now, detail);
            window = null;
            return;
        }
        Matcher side = SIDE.matcher(text);
        if (receipt != null && side.find()) {
            (side.group(1).equals("+") ? receipt.got : receipt.gave).add(side.group(2));
            receipt.lastLine = now;
            return;
        }
        if (TRACKER_CHAT.matcher(ChatFormatting.stripFormatting(text)).find()) {
            var data = new JsonObject();
            data.addProperty("text", text);
            var event = new JsonObject();
            event.addProperty("at", now);
            event.addProperty("kind", "chat");
            event.add("data", data);
            MarketApi.send("/api/event", event);
        }
    }

    private static void flush() {
        Receipt ready = receipt;
        receipt = null;
        if (ready == null || (ready.got.isEmpty() && ready.gave.isEmpty())) return;
        var trade = new JsonObject();
        trade.addProperty("other", ready.other);
        trade.addProperty("at", ready.at);
        trade.add("got", ready.got);
        trade.add("gave", ready.gave);
        if (ready.detail != null) trade.add("detail", ready.detail);
        MarketApi.send("/api/trade", trade);
    }

    static JsonObject itemJson(ItemStack stack) {
        var custom = stack.get(DataComponents.CUSTOM_DATA);
        if (custom == null) return null;
        JsonElement converted = NbtOps.INSTANCE.convertTo(JsonOps.INSTANCE, custom.copyTag());
        if (!converted.isJsonObject() || !converted.getAsJsonObject().has("item_data")) return null;
        return ItemPayload.normalize(converted.getAsJsonObject(), stack.getCount());
    }

    private static void readTrade(Minecraft mc, AbstractContainerScreen<?> screen, long now) {
        Matcher title = TRADE_TITLE.matcher(screen.getTitle().getString());
        if (!title.matches()) return;
        var items = new JsonArray();
        var priced = new ArrayList<JsonObject>();
        var sides = new ArrayList<Integer>();
        double[] totals = new double[2];
        for (Slot slot : screen.getMenu().slots) {
            ItemStack stack = slot.getItem();
            if (slot.container instanceof Inventory || stack.isEmpty()) continue;
            int column = slot.getContainerSlot() % 9;
            int side = column < 4 ? 0 : column > 4 ? 1 : -1;
            if (side < 0) continue;
            JsonObject raw = itemJson(stack);
            String name = legacy(stack.getHoverName());
            double coins = coinAmount(name);
            if (raw == null && coins <= 0) continue;
            var row = new JsonObject();
            row.addProperty("name", name);
            row.addProperty("count", stack.getCount());
            row.addProperty("slot", slot.index);
            row.addProperty("x", slot.x);
            if (raw != null) {
                row.add("item", raw);
                priced.add(raw);
                sides.add(side);
            } else {
                totals[side] += coins;
            }
            items.add(row);
        }
        if (MarketApi.tracking() && !items.isEmpty()) {
            var detail = new JsonObject();
            detail.addProperty("title", screen.getTitle().getString());
            detail.addProperty("at", now);
            detail.add("items", items);
            window = new TradeWindow(title.group(1), now, detail);
        }
        String key = items.toString();
        if (now < nextTradeQuote || key.equals(quoteKey)) return;
        quoteKey = key;
        nextTradeQuote = now + 750;
        if (priced.isEmpty()) { tradeTotals = totals; return; }
        MarketApi.quotes(priced).whenComplete((rows, error) -> mc.execute(() -> {
            if (screen != tradeScreen || !key.equals(quoteKey)) return;
            if (error != null) { quoteKey = ""; nextTradeQuote = System.currentTimeMillis() + 5000; return; }
            for (int i = 0; i < Math.min(rows.size(), sides.size()); i++) {
                JsonObject estimate = rows.get(i);
                totals[sides.get(i)] += estimate.get("value").getAsDouble() * estimate.get("count").getAsDouble();
            }
            tradeTotals = totals;
        }));
    }

    static double coinAmount(String name) {
        String text = ChatFormatting.stripFormatting(name).trim();
        // The offer nugget has the label first. The separate "Coins" control has no amount.
        Matcher amount = COINS_PREFIX.matcher(text);
        if (!amount.matches()) {
            amount = COINS_SUFFIX.matcher(text);
            if (!amount.matches()) return 0;
        }
        try {
            double value = Double.parseDouble(amount.group(1).replace(",", ""));
            return switch (amount.group(2).toLowerCase(Locale.ROOT)) {
                case "k" -> value * 1e3;
                case "m" -> value * 1e6;
                case "b" -> value * 1e9;
                default -> value;
            };
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    private static String legacy(Component message) {
        String plain = message.getString();
        if (plain.indexOf('§') >= 0) return plain;
        var out = new StringBuilder();
        for (Component part : message.toFlatList()) {
            TextColor color = part.getStyle().getColor();
            if (color != null) {
                for (ChatFormatting f : ChatFormatting.values()) {
                    if (f.isColor() && f.getColor() != null && f.getColor() == color.getValue()) {
                        out.append('§').append(f.getChar());
                        break;
                    }
                }
            }
            out.append(part.getString());
        }
        return out.toString();
    }
}
