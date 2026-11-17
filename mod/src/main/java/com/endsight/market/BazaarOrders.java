package com.endsight.market;

import com.google.gson.JsonElement;
import net.minecraft.client.Minecraft;
import net.minecraft.core.component.DataComponents;
import net.minecraft.network.chat.Component;
import net.minecraft.world.item.ItemStack;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/** Snapshot comparisons only; orders are never cancelled, placed or repriced here. */
final class BazaarOrders {
    private BazaarOrders() {}
    private record Product(double bid, double ask) {}
    private record Snapshot(Map<String, Product> products, long sourceAt, long fetchedAt) {}
    private static Snapshot snapshot;
    private static long nextRequest;
    private static boolean busy;
    private static final long REFRESH = 15_000, STALE = 90_000;

    private static boolean open(Minecraft mc) {
        return mc.screen != null && BazaarOrder.nameKey(mc.screen.getTitle().getString()).equals("bazaar orders");
    }

    static void tick(Minecraft mc) {
        long now = System.currentTimeMillis();
        if (!open(mc) || !MarketTooltip.active() || busy || now < nextRequest) return;
        busy = true;
        nextRequest = now + REFRESH;
        // Status supplies the collector's timestamp, not just when our HTTP request succeeded.
        MarketApi.get("/api/bazaar").thenCombine(MarketApi.get("/api/status"), (book, status) -> {
            Map<String, Product> products = new HashMap<>();
            var rows = book.getAsJsonArray("products");
            if (rows == null || !status.has("last_poll")) throw new IllegalArgumentException("missing order book");
            for (JsonElement element : rows) {
                var row = element.getAsJsonObject();
                String name = BazaarOrder.nameKey(row.get("name").getAsString());
                products.put(name, new Product(row.get("bid").getAsDouble(), row.get("ask").getAsDouble()));
            }
            return new Snapshot(Map.copyOf(products), status.get("last_poll").getAsLong(), System.currentTimeMillis());
        }).whenComplete((answer, error) -> mc.execute(() -> {
            busy = false;
            if (error == null) snapshot = answer;
        }));
    }

    static boolean add(ItemStack stack, List<Component> lines) {
        if (!open(Minecraft.getInstance())) return false;
        var lore = stack.get(DataComponents.LORE);
        if (lore == null) return false;
        BazaarOrder order = BazaarOrder.parse(stack.getHoverName().getString(), lore.lines().stream().map(Component::getString).toList());
        if (order == null) return false;
        lines.add(Component.empty());
        Snapshot current = snapshot;
        long now = System.currentTimeMillis();
        if (current == null) {
            lines.add(line("Loading current Bazaar prices…", 0x999999));
            return true;
        }
        long age = now - current.sourceAt;
        boolean stale = age < -300_000 || age > STALE || now - current.fetchedAt > STALE;
        Product product = current.products.get(BazaarOrder.nameKey(order.name()));
        if (product == null) {
            lines.add(line("No Bazaar snapshot for this product", 0x999999));
            return true;
        }
        lines.add(line((stale ? "Last known buy order: " : "Highest buy order: ") + price(product.bid), 0xE6B45A));
        lines.add(line((stale ? "Last known sell offer: " : "Lowest sell offer: ") + price(product.ask), 0xE6B45A));
        if (stale) lines.add(line("Snapshot stale · waiting for an update", 0xEC7A70));
        else {
            switch (order.position(product.bid, product.ask)) {
                case BEHIND -> {
                    double gap = order.buy() ? product.bid - order.price() : order.price() - product.ask;
                    lines.add(line((order.buy() ? "Outbid" : "Undercut") + " by " + exact(gap) + " each", 0xEC7A70));
                }
                case AT_BEST -> lines.add(line("At best price · queue position unknown", 0x6FCE8B));
                case AHEAD -> lines.add(line("Your price is ahead of this snapshot", 0x999999));
                case NO_BOOK -> lines.add(line("No " + (order.buy() ? "buy orders" : "sell offers") + " in snapshot", 0x999999));
            }
            lines.add(line("Market snapshot " + Math.max(0, age / 1000) + "s ago", 0x888888));
        }
        return true;
    }
    private static Component line(String text, int color) { return Component.literal(text).withColor(color); }
    private static String price(double value) { return Double.isFinite(value) && value > 0 ? exact(value) + " each" : "none"; }
    private static String exact(double value) { return String.format(Locale.US, value == Math.rint(value) ? "%,.0f" : "%,.2f", value); }
}
