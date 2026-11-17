package com.endsight.market;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.List;
import java.util.Set;

/** One identity for a saved recipe icon and the same stack sent in a chat link. */
final class ItemPayload {
    private ItemPayload() {}
    static JsonObject normalize(JsonObject input, int count) {
        if (!input.has("item_data") || !input.get("item_data").isJsonObject()) return null;
        JsonObject raw = input.deepCopy();
        Set<String> fields = Set.of("entry", "count", "item_data", "item_modifiers", "extra", "abilities");
        raw.keySet().removeIf(key -> !fields.contains(key));
        JsonObject data = raw.getAsJsonObject("item_data");
        // The server supplies the stable catalog id inside item_data, including in /show.
        // Resolving only the name lost items whose catalog name changed or has a reforge.
        String entry = text(raw, "entry");
        if (entry.isEmpty()) entry = text(data, "item_id");
        if (entry.matches("[a-z0-9_]{1,100}")) raw.addProperty("entry", entry);
        raw.addProperty("count", count);
        JsonObject mods = object(raw, "item_modifiers");
        for (String key : List.of("recombobulated", "art_of_war", "enrichment")) flag(mods, key);
        flag(data, "skull");
        return raw;
    }
    private static void flag(JsonObject obj, String key) {
        JsonElement value = obj.get(key);
        if (value != null && value.isJsonPrimitive() && value.getAsJsonPrimitive().isNumber())
            obj.addProperty(key, value.getAsInt() != 0);
    }
    private static JsonObject object(JsonObject obj, String key) {
        JsonElement value = obj.get(key);
        return value != null && value.isJsonObject() ? value.getAsJsonObject() : new JsonObject();
    }
    private static String text(JsonObject obj, String key) {
        JsonElement value = obj.get(key);
        return value != null && value.isJsonPrimitive() && value.getAsJsonPrimitive().isString() ? value.getAsString() : "";
    }
}
