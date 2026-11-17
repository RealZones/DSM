package com.endsight.market;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CompletionException;
import java.util.zip.GZIPInputStream;

/** Negotiate gzip explicitly: Java's HTTP client does not inflate it automatically. */
public final class HttpCompression {
    private HttpCompression() {}
    private static final int LIMIT = 12_000_000;
    public static HttpRequest request(HttpRequest request) {
        return HttpRequest.newBuilder(request, (name, value) -> !name.equalsIgnoreCase("Accept-Encoding"))
                .header("Accept-Encoding", "gzip").build();
    }
    public static HttpResponse.BodyHandler<String> handler() {
        return info -> HttpResponse.BodySubscribers.mapping(HttpResponse.BodySubscribers.ofByteArray(), bytes -> {
            try { return decode(bytes, info.headers().firstValue("Content-Encoding").orElse(""), LIMIT); }
            catch (IOException error) { throw new CompletionException(error); }
        });
    }
    static String decode(byte[] bytes, String encoding, int limit) throws IOException {
        if (bytes.length > limit) throw new IOException("Response too large");
        try (InputStream input = switch (encoding.strip().toLowerCase(java.util.Locale.ROOT)) {
            case "gzip" -> new GZIPInputStream(new ByteArrayInputStream(bytes));
            case "", "identity" -> new ByteArrayInputStream(bytes);
            default -> throw new IOException("Unsupported response encoding");
        }) {
            byte[] decoded = input.readNBytes(limit + 1);
            if (decoded.length > limit) throw new IOException("Response too large");
            return new String(decoded, StandardCharsets.UTF_8);
        }
    }
}
