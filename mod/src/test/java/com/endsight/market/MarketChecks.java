package com.endsight.market;

import com.google.gson.JsonParser;
import java.util.List;

public final class MarketChecks {
    private static int checks;
    private static void check(boolean pass, String why) {
        checks++;
        if (!pass) throw new AssertionError(why);
    }
    public static void main(String[] args) throws Exception {
        BazaarOrder sell = BazaarOrder.parse("§aSELL Refined End Stone", List.of("§7Remaining: §a129", "§7Price each: §616,999,984", "Click to cancel and return unfilled funds."));
        check(sell != null && !sell.buy() && sell.name().equals("Refined End Stone") && sell.price() == 16_999_984, "real order screenshot");
        check(sell.position(7_000_000, 12_000_000) == BazaarOrder.Position.BEHIND, "lower sell offer undercuts the user's sale");
        check(sell.position(30_000_000, 16_999_984) == BazaarOrder.Position.AT_BEST, "sell compares ask, not bid");
        check(sell.position(7_000_000, 17_000_000) == BazaarOrder.Position.AHEAD, "own new order can be ahead of delayed snapshot");
        check(sell.position(7_000_000, 0) == BazaarOrder.Position.NO_BOOK, "empty side cannot claim best price");
        BazaarOrder buy = BazaarOrder.parse("BUY Refined End Stone", List.of("Price each: 7,000,000 coins"));
        check(buy != null && buy.buy(), "buy order parsed");
        check(buy.position(7_000_001, 12_000_000) == BazaarOrder.Position.BEHIND, "one coin outbid is visible");
        check(buy.position(7_000_000, 1) == BazaarOrder.Position.AT_BEST, "buy compares bid, not ask");
        check(buy.position(6_999_999, 12_000_000) == BazaarOrder.Position.AHEAD, "above current bid");
        check(buy.position(Double.NaN, 12_000_000) == BazaarOrder.Position.NO_BOOK, "invalid quote rejected");
        check(BazaarOrder.parse("Refined End Stone", List.of("Price each: 1")) == null, "normal items are not orders");
        check(BazaarOrder.parse("SELL Refined End Stone", List.of("Remaining: 129")) == null, "missing order price");
        check(BazaarOrder.parse("SELL Refined End Stone", List.of("Price each: -1")) == null, "negative price rejected");
        check(BazaarOrder.parse("SELL Refined End Stone", List.of("Price each: 0")) == null, "zero price rejected");
        check(BazaarOrder.nameKey("§aRefined  End Stone ").equals("refined end stone"), "formatting does not affect product lookup");
        var raw = JsonParser.parseString("{\"extra\":{},\"item_skin\":{\"value\":\"texture\"},\"bookkeeping\":1,\"item_modifiers\":{\"recombobulated\":0,\"star_level\":6},\"item_data\":{\"item_name\":\"Arcane Hyperion\",\"item_id\":\"hyperion\",\"skull\":0}}").getAsJsonObject();
        var normalized = ItemPayload.normalize(raw, 3);
        check(normalized.get("entry").getAsString().equals("hyperion"), "nested server ID wins over altered display name");
        check(normalized.get("count").getAsInt() == 3, "stack count retained");
        check(!normalized.getAsJsonObject("item_modifiers").get("recombobulated").getAsBoolean(), "NBT byte flags normalized");
        check(normalized.getAsJsonObject("item_modifiers").get("star_level").getAsInt() == 6, "numeric upgrades preserved");
        check(!normalized.has("bookkeeping") && !normalized.has("item_skin"), "irrelevant transport data cannot split price keys");
        check(!raw.has("entry") && raw.has("bookkeeping"), "normalization never edits live item data");
        check(ItemPayload.normalize(JsonParser.parseString("{}").getAsJsonObject(), 1) == null, "ordinary vanilla data ignored");
        var catalog=JsonParser.parseString("{\"items\":{\"hyperion\":{\"name\":\"Hyperion\",\"tier\":\"LEGENDARY\"},\"obsidian_drill_od_655\":{\"name\":\"Obsidian Drill OD-655\",\"tier\":\"LEGENDARY\"},\"void_polished_engine\":{\"name\":\"Void-Polished Engine\"},\"perfectly_cut_fuel_tank\":{\"name\":\"Perfectly-Cut Fuel Tank\"},\"blazing_omelette\":{\"name\":\"Blazing Omelette\"},\"refined_obsidian\":{\"name\":\"Refined Obsidian\",\"tier\":\"RARE\"}}}").getAsJsonObject();
        var shared=new SharedItemText(catalog);
        var hyperion=shared.parse("§dArcane Hyperion §6✪§6✪§6✪§6✪§6✪\nWither Impact\n§d\ue400MYTHIC\ue401 DUNGEON SWORD");
        check(hyperion!=null && hyperion.raw().get("entry").getAsString().equals("hyperion"),"text-only chat item resolved");
        check(hyperion.raw().getAsJsonObject("item_modifiers").get("star_level").getAsInt()==5,"chat stars counted");
        check(hyperion.raw().getAsJsonObject("item_modifiers").get("recombobulated").getAsBoolean(),"chat rarity infers recomb");
        check(hyperion.raw().getAsJsonObject("item_data").get("reforge").getAsString().equals("arcane"),"chat reforge retained");
        check(hyperion.raw().getAsJsonArray("abilities").get(0).getAsString().equals("wither_impact"),"shown Wither Impact retains scrolls");
        var plainBlade=shared.parse("Hyperion\nSome stats\nLEGENDARY DUNGEON SWORD");
        check(!plainBlade.raw().has("abilities"),"unshown scrolls are never invented");
        var drill=shared.parse("Stellar Obsidian Drill OD-655\nVoid-Polished Engine\nPerfectly-Cut Fuel Tank\nBlazing Omelette\nVoid Powder Coated (+500)\nMYTHIC DRILL");
        check(drill.raw().getAsJsonObject("extra").getAsJsonObject("drill").get("engine").getAsString().equals("void_polished_engine"),"visible drill engine");
        check(drill.raw().getAsJsonObject("extra").getAsJsonObject("drill").get("tank").getAsString().equals("perfectly_cut_fuel_tank"),"visible drill tank");
        check(drill.raw().getAsJsonObject("extra").getAsJsonObject("drill").get("module").getAsString().equals("blazing_omelette"),"visible drill module");
        check(drill.raw().getAsJsonObject("extra").getAsJsonObject("drill").get("coated").getAsBoolean(),"visible coating");
        var stack=shared.parse("59x §9Refined Obsidian\nRight-click to view recipes!\n\ue400RARE\ue401");
        check(stack.raw().get("count").getAsInt()==59,"shared stack count");
        check(!stack.raw().getAsJsonObject("item_modifiers").has("recombobulated"),"plain materials are not recombed");
        check(shared.parse("SampleOwner\nLEGENDARY Rank")==null,"player hover untouched");
        check(shared.parse("Hyperion")==null,"single-line text is not an item hover");
        check(shared.parse("Unknown Item\nRARE")==null,"unknown item not guessed");
        try(var input=MarketChecks.class.getResourceAsStream("/shared-hyperion.txt")) {
            var real=shared.parse(new String(input.readAllBytes(),java.nio.charset.StandardCharsets.UTF_8));
            check(real!=null && real.raw().get("entry").getAsString().equals("hyperion"),"actual text hover captured from game resolves Hyperion");
            check(real.raw().getAsJsonArray("abilities").get(0).getAsString().equals("wither_impact"),"actual shared Hyperion carries shown scroll ability");
        }
        try(var input=MarketChecks.class.getResourceAsStream("/shared-obsidian.txt")) {
            var real=shared.parse(new String(input.readAllBytes(),java.nio.charset.StandardCharsets.UTF_8));
            check(real!=null && real.raw().get("count").getAsInt()==59,"actual shared material stack parses through resource-pack font glyphs");
        }
        catalog.getAsJsonObject("items").add("duplicate",catalog.getAsJsonObject("items").get("refined_obsidian"));
        check(new SharedItemText(catalog).parse("Refined Obsidian\nRARE")==null,"ambiguous catalog names rejected");
        String unicode="{\"name\":\"Arcane Hyperion ✪\"}";
        var zipped=new java.io.ByteArrayOutputStream();
        try(var gzip=new java.util.zip.GZIPOutputStream(zipped)){gzip.write(unicode.getBytes(java.nio.charset.StandardCharsets.UTF_8));}
        check(HttpCompression.decode(zipped.toByteArray(),"gzip",1024).equals(unicode),"gzip JSON decoded with Unicode intact");
        check(HttpCompression.decode(unicode.getBytes(java.nio.charset.StandardCharsets.UTF_8),"",1024).equals(unicode),"identity response supported");
        boolean bounded=false;
        try{HttpCompression.decode(zipped.toByteArray(),"gzip",unicode.getBytes(java.nio.charset.StandardCharsets.UTF_8).length-1);}catch(java.io.IOException e){bounded=true;}
        check(bounded,"inflated response bound enforced");
        var expansion=new java.io.ByteArrayOutputStream();
        try(var gzip=new java.util.zip.GZIPOutputStream(expansion)){gzip.write("a".repeat(10000).getBytes(java.nio.charset.StandardCharsets.UTF_8));}
        boolean expansionBound=false;
        try{HttpCompression.decode(expansion.toByteArray(),"gzip",1000);}catch(java.io.IOException e){expansionBound=true;}
        check(expansionBound && expansion.size()<1000,"small compressed body cannot exceed decompressed limit");
        boolean corrupt=false;
        try{HttpCompression.decode(new byte[]{1,2,3},"gzip",1024);}catch(java.io.IOException e){corrupt=true;}
        check(corrupt,"corrupt gzip rejected");
        boolean unsupported=false;
        try{HttpCompression.decode(new byte[]{1},"br",1024);}catch(java.io.IOException e){unsupported=true;}
        check(unsupported,"unrequested encoding rejected");
        var post=java.net.http.HttpRequest.newBuilder(java.net.URI.create("https://example.invalid/prices"))
                .header("Content-Type","application/json").POST(java.net.http.HttpRequest.BodyPublishers.ofString("{}")).build();
        var compressed=HttpCompression.request(post);
        check(compressed.headers().firstValue("Accept-Encoding").orElse("").equals("gzip"),"gzip negotiated explicitly");
        check(compressed.method().equals("POST") && compressed.bodyPublisher().orElseThrow()==post.bodyPublisher().orElseThrow(),"POST data unchanged by compression negotiation");
        check(compressed.headers().firstValue("Content-Type").orElse("").equals("application/json"),"other headers retained");
        System.out.println("Market checks: " + checks + " passed");
    }
}
