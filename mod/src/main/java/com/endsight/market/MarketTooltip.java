package com.endsight.market;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.ChatFormatting;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;
import net.minecraft.core.component.DataComponents;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.HoverEvent;
import net.minecraft.network.chat.Style;
import net.minecraft.network.chat.MutableComponent;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.TooltipFlag;
import net.minecraft.world.item.component.CustomData;

import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import org.slf4j.LoggerFactory;

/**
 * Price lines on any item, from the same numbers the site shows: lbin with the bazaar's Buy
 * and Sell, then Med and Vol, then Full Craft Cost. The server writes the lines (market/lore.py), so
 * the wording can change without a new jar; this only shows them, appended at the foot.
 *
 * A tooltip is built every frame, and Endsight builds more of them for its HUDs (equipment,
 * cooldowns, powder, storage), so the per-frame path here is two map lookups and a list copy.
 * Turning an item into JSON is the one costly step and happens once per stack: the stack is
 * remembered by identity, with its custom data by identity too, which the game replaces rather
 * than edits whenever the server changes the item. Each answer is parsed into styled lines once,
 * off the render thread, when it arrives.
 *
 * Nothing here assumes one item at a time. Every item whose tooltip is asked for is collected,
 * and a short pause after it was first seen the tick prices it, up to 64 to a request. The first
 * version kept a single "hovered" item and restarted its pause whenever another tooltip was built,
 * so with a HUD reading other items it priced almost nothing. The second also dropped an item not
 * asked for again within 250 ms, which never priced a panel that builds a tooltip once and keeps
 * it, like a recipe browser (Fear, 2026-11-16).
 */
public final class MarketTooltip {
    private MarketTooltip() {}

    private static final long FRESH = 60_000, PAUSE = 300, RETRY = 20_000, FORGET = 5_000;
    private static final int BATCH = 64;          // the server's cap per request
    private static final long SCAN = 500;
    private static long scanAt;

    /** Lines ready to add, and when they were fetched. */
    private record Answer(long at, List<Component> lines) {}

    /** What one stack is to the market, worked out once. A null key is not a Skyblock item. */
    private static final class Ref {
        final CustomData custom;
        final int count;
        final JsonObject raw;
        final String key;
        long used;

        Ref(CustomData custom, int count, JsonObject raw) {
            this.custom = custom;
            this.count = count;
            this.raw = raw;
            this.key = raw == null ? null : raw.toString();
        }
    }

    private static final class Seen {
        final JsonObject raw;
        final long first;
        long last;
        Seen(JsonObject raw, long now) { this.raw = raw; this.first = now; this.last = now; }
    }

    private static final Map<ItemStack, Ref> REFS = new IdentityHashMap<>();
    private static final Map<String, Answer> CACHE = new LinkedHashMap<>(256, .75f, true) {
        @Override protected boolean removeEldestEntry(Map.Entry<String, Answer> e) { return size() > 512; }
    };
    private static final Map<String, Seen> SEEN = new LinkedHashMap<>();
    private static long retryAt, forgetAt;
    private static boolean busy;

    static void add(ItemStack stack, Item.TooltipContext context, TooltipFlag flag, List<Component> lines) {
        if (stack.isEmpty()) return;
        if (Minecraft.getInstance().isSameThread() && Market.tooltipOn() && MarketApi.ready()
                && BazaarOrders.add(stack, lines)) return;
        CustomData custom = stack.get(DataComponents.CUSTOM_DATA);
        // Vanilla items have nothing to price, and the creative search index builds tooltips off the
        // render thread, where none of these maps may be touched.
        if (custom == null || !Minecraft.getInstance().isSameThread() || !Market.tooltipOn() || !MarketApi.ready()) return;
        long now = System.currentTimeMillis();
        Ref ref = ref(stack, custom, now);
        if (ref.key == null) return;
        lines.addAll(priceLines(ref.key, ref.raw));
    }

    static List<Component> priceLines(String key, JsonObject raw) {
        long now = System.currentTimeMillis();
        Answer found = CACHE.get(key);
        // An old answer still shows while a fresh one is fetched, so a price never blinks out.
        if (found != null) {
            if (now - found.at < FRESH) return found.lines;
        }
        Seen seen = SEEN.get(key);
        if (seen == null) SEEN.put(key, new Seen(raw, now));
        else seen.last = now;
        return found != null ? found.lines : List.of(Component.literal(now < retryAt ? "Market prices temporarily unavailable" : "Loading market prices…")
                .withStyle(ChatFormatting.DARK_GRAY));
    }

    /**
     * Prewarm real item links and DragSim's text-only /show links. Text links do not fire
     * ItemTooltipCallback at all; their price lines are appended in componentHoverEffect.
     */
    static void queueChat(Component message) {
        if (!Market.tooltipOn() || !MarketApi.ready()) return;
        long now = System.currentTimeMillis();
        message.visit((style, text) -> {
            if (style.getHoverEvent() instanceof HoverEvent.ShowItem show) {
                JsonObject raw = Market.itemJson(show.item().create());
                if (raw != null) {
                    String key = raw.toString();
                    Answer found = CACHE.get(key);
                    if ((found == null || now - found.at >= FRESH) && !SEEN.containsKey(key)) SEEN.put(key, new Seen(raw, now - PAUSE));
                }
            } else if (style.getHoverEvent() instanceof HoverEvent.ShowText show) {
                SharedChatTooltip.queue(show.value());
            }
            return Optional.empty();
        }, Style.EMPTY);
    }

    private static Ref ref(ItemStack stack, CustomData custom, long now) {
        int count = stack.getCount();
        Ref ref = REFS.get(stack);
        if (ref == null || ref.custom != custom || ref.count != count) {
            ref = new Ref(custom, count, Market.itemJson(stack));
            REFS.put(stack, ref);
        }
        ref.used = now;
        return ref;
    }

    /**
     * Prices a whole container while it is open, so a hover shows its price at once instead of
     * after the pause and a round trip. Every slot without a fresh answer is queued as if it had
     * already waited out the pause; tick then sends them, up to 64 to a request. Rescanned twice a
     * second because a menu's items arrive a few ticks after it opens, and pages change in place.
     */
    static void prefetch(Minecraft mc, long now) {
        if (now < scanAt || !(mc.screen instanceof AbstractContainerScreen<?> screen)) return;
        scanAt = now + SCAN;
        for (Slot slot : screen.getMenu().slots) {
            ItemStack stack = slot.getItem();
            if (stack.isEmpty()) continue;
            CustomData custom = stack.get(DataComponents.CUSTOM_DATA);
            if (custom == null) continue;
            Ref ref = ref(stack, custom, now);
            if (ref.key == null) continue;
            Answer found = CACHE.get(ref.key);
            if (found != null && now - found.at < FRESH) continue;
            Seen seen = SEEN.get(ref.key);
            if (seen == null) {
                // Counted as seen a pause ago, so tick sends it now.
                SEEN.put(ref.key, new Seen(ref.raw, now - PAUSE));
            } else {
                seen.last = now;
            }
        }
    }

    static void tick(Minecraft mc) {
        long now = System.currentTimeMillis();
        if (now >= forgetAt) {
            forgetAt = now + 1_000;
            REFS.values().removeIf(ref -> now - ref.used > FORGET);
        }
        if (SEEN.isEmpty()) return;
        if (!Market.tooltipOn() || !MarketApi.ready()) { SEEN.clear(); return; }
        // Only a long-stale entry goes: a request that keeps failing should not grow this forever.
        SEEN.values().removeIf(seen -> now - seen.last > FORGET);
        if (busy || now < retryAt) return;
        List<String> keys = new ArrayList<>();
        List<JsonObject> items = new ArrayList<>();
        for (var entry : SEEN.entrySet()) {
            if (now - entry.getValue().first < PAUSE) continue;
            keys.add(entry.getKey());
            items.add(entry.getValue().raw);
            if (items.size() == BATCH) break;
        }
        if (items.isEmpty()) return;
        keys.forEach(SEEN::remove);
        busy = true;
        // Parsed on the thread the reply lands on, so a frame never formats anything.
        MarketApi.quotes(items).thenApply(rows -> {
            List<List<Component>> parsed = new ArrayList<>(rows.size());
            for (JsonObject row : rows) parsed.add(lore(row));
            return parsed;
        }).whenComplete((parsed, error) -> mc.execute(() -> {
            busy = false;
            if (error != null || parsed.size() != keys.size()) {
                retryAt = System.currentTimeMillis() + RETRY;
                // Log only the type: request URLs/credentials must never appear here.
                Throwable cause = error;
                while (cause != null && cause.getCause() != null) cause = cause.getCause();
                LoggerFactory.getLogger("dragsim_market").warn("Price lookup failed ({})", cause == null ? "response size" : cause.getClass().getSimpleName());
                return;
            }
            long at = System.currentTimeMillis();
            for (int i = 0; i < keys.size(); i++) CACHE.put(keys.get(i), new Answer(at, parsed.get(i)));
        }));
    }

    /** The server's lines as styled text, so a mod reading the tooltip sees "lbin: 1,234", not codes. */
    static List<Component> lore(JsonObject est) {
        JsonElement lore = est.get("lore");
        if (lore == null || !lore.isJsonArray()) return List.of();
        List<Component> out = new ArrayList<>(lore.getAsJsonArray().size());
        for (JsonElement line : lore.getAsJsonArray()) out.add(styled(line.getAsString()));
        return List.copyOf(out);
    }

    static Component styled(String line) {
        MutableComponent out = Component.empty();
        ChatFormatting colour = null;
        int start = 0;
        for (int i = 0; i + 1 < line.length(); i++) {
            if (line.charAt(i) != '§') continue;
            if (i > start) out.append(part(line.substring(start, i), colour));
            ChatFormatting code = ChatFormatting.getByCode(line.charAt(i + 1));
            if (code != null && code.isColor()) colour = code;
            start = ++i + 1;
        }
        if (start < line.length()) out.append(part(line.substring(start), colour));
        return out;
    }

    private static Component part(String text, ChatFormatting colour) {
        return colour == null ? Component.literal(text) : Component.literal(text).withStyle(colour);
    }

    static String coins(double n) {
        double a = Math.abs(n);
        if (a >= 1e9) return trim(a / 1e9, 2) + "b";
        if (a >= 1e6) return trim(a / 1e6, 1) + "m";
        if (a >= 1e3) return trim(a / 1e3, 0) + "k";
        return String.valueOf(Math.round(a));
    }

    public static boolean active() { return Market.tooltipOn() && MarketApi.ready(); }

    private static String trim(double value, int places) {
        String text = String.format(Locale.ROOT, "%." + places + "f", value);
        return text.contains(".") ? text.replaceAll("\\.?0+$", "") : text;
    }
}
