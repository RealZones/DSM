package com.endsight.market;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.*;
import java.util.regex.Pattern;

/** Only values visible in a text link are inferred; hidden modifiers stay unknown. */
final class SharedItemText {
    private static final List<String> RARITIES=List.of("COMMON","UNCOMMON","RARE","EPIC","LEGENDARY","MYTHIC","DIVINE","SPECIAL","VERY SPECIAL");
    private static final Pattern COUNT=Pattern.compile("^(\\d+)x\\s+(.+)$");
    record Parsed(JsonObject raw, String title, String lore) {}
    private final Map<String,String> names=new HashMap<>();
    private final Set<String> ambiguous=new HashSet<>();
    private final JsonObject definitions;
    SharedItemText(JsonObject catalog) {
        definitions=catalog.getAsJsonObject("items");
        if(definitions==null)throw new IllegalArgumentException("Missing catalog items");
        for(var pair:definitions.entrySet()) {
            if(!pair.getValue().isJsonObject())continue;
            String key=key(text(pair.getValue().getAsJsonObject(),"name"));
            if(names.putIfAbsent(key,pair.getKey())!=null)ambiguous.add(key);
        }
        ambiguous.forEach(names::remove);
    }
    static String clean(String text) {return text.replaceAll("§.","").replaceAll("\\p{Co}","").strip();}
    static String key(String text) {return clean(text).replaceAll("\\s+"," ").toLowerCase(Locale.ROOT);}
    static boolean candidate(String tooltip) {
        if(tooltip.length()>24_000 || !tooltip.contains("\n"))return false;
        String last=clean(tooltip.strip().substring(tooltip.strip().lastIndexOf('\n')+1));
        return RARITIES.stream().anyMatch(r->last.matches(".*\\b"+r+"\\b.*"));
    }
    Parsed parse(String tooltip) {
        if(!candidate(tooltip))return null;
        String title=clean(tooltip.substring(0,tooltip.indexOf('\n')));
        int count=1;
        var amount=COUNT.matcher(title);
        if(amount.matches()) {
            try {count=Integer.parseInt(amount.group(1));}catch(NumberFormatException e){return null;}
            title=amount.group(2);
        }
        if(count<=0)return null;
        int stars=(int)title.chars().filter(c->c=='✪').count();
        if(stars>100)return null;
        String bare=title.replace("✪","").strip(),reforge="",entry=names.get(key(bare));
        if(entry==null) {
            int space=bare.indexOf(' ');
            if(space<1)return null;
            entry=names.get(key(bare.substring(space+1)));
            if(entry==null)return null;
            reforge=bare.substring(0,space).toLowerCase(Locale.ROOT);
        }
        JsonObject definition=definitions.getAsJsonObject(entry);
        String last=clean(tooltip.strip().substring(tooltip.strip().lastIndexOf('\n')+1));
        String rarity=RARITIES.stream().filter(r->last.matches(".*\\b"+r+"\\b.*")).reduce((a,b)->b).orElse(text(definition,"tier"));
        JsonObject raw=new JsonObject(),data=new JsonObject(),mods=new JsonObject(),extra=new JsonObject();
        raw.addProperty("entry",entry);raw.addProperty("count",count);
        data.addProperty("item_name",text(definition,"name"));data.addProperty("rarity",rarity);data.addProperty("reforge",reforge);
        mods.addProperty("star_level",stars);
        int baseRank=RARITIES.indexOf(text(definition,"tier"));
        if(baseRank>=0 && RARITIES.indexOf(rarity)==baseRank+1)mods.addProperty("recombobulated",true);
        String body=tooltip.substring(tooltip.indexOf('\n')+1);
        List<String> lines=body.lines().map(SharedItemText::clean).toList();
        JsonObject drill=new JsonObject();
        for(String line:lines) {
            String part=names.get(key(line));
            if(part==null)continue;
            if(part.endsWith("_engine"))drill.addProperty("engine",part);
            else if(part.endsWith("_fuel_tank"))drill.addProperty("tank",part);
            else if(part.endsWith("_omelette"))drill.addProperty("module",part);
        }
        if(lines.stream().anyMatch(s->s.startsWith("Void Powder Coated")))drill.addProperty("coated",true);
        if(!drill.isEmpty())extra.add("drill",drill);
        if(Set.of("hyperion","necron_blade","scylla","valkyrie","astraea").contains(entry)
                && lines.stream().anyMatch(s->s.startsWith("Wither Impact"))) {
            var abilities=new com.google.gson.JsonArray();abilities.add("wither_impact");raw.add("abilities",abilities);
        }
        raw.add("item_data",data);raw.add("item_modifiers",mods);raw.add("extra",extra);
        return new Parsed(raw,title,body);
    }
    private static String text(JsonObject value,String field) {
        JsonElement e=value.get(field);return e!=null && e.isJsonPrimitive()?e.getAsString():"";
    }
}
