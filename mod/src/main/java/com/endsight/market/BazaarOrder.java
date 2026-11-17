package com.endsight.market;

import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

record BazaarOrder(boolean buy, String name, double price) {
    private static final Pattern TITLE = Pattern.compile("^(BUY|SELL)\\s+(.+)$");
    private static final Pattern PRICE = Pattern.compile("^Price each:\\s*([0-9][0-9,]*(?:\\.[0-9]+)?)\\s*(?:coins)?$", Pattern.CASE_INSENSITIVE);
    static String clean(String text) { return text.replaceAll("§.", "").strip(); }
    static String nameKey(String text) { return clean(text).replaceAll("\\s+", " ").toLowerCase(Locale.ROOT); }
    static BazaarOrder parse(String title, List<String> lore) {
        var match = TITLE.matcher(clean(title));
        if (!match.matches()) return null;
        for (String line : lore) {
            var each = PRICE.matcher(clean(line));
            if (!each.matches()) continue;
            try {
                double price = Double.parseDouble(each.group(1).replace(",", ""));
                return Double.isFinite(price) && price > 0 ? new BazaarOrder(match.group(1).equals("BUY"), match.group(2), price) : null;
            } catch (NumberFormatException ignored) { return null; }
        }
        return null;
    }
    enum Position { BEHIND, AT_BEST, AHEAD, NO_BOOK }
    Position position(double bid, double ask) {
        double best = buy ? bid : ask;
        if (!Double.isFinite(best) || best <= 0) return Position.NO_BOOK;
        double difference = buy ? best - price : price - best;
        if (Math.abs(difference) < .001) return Position.AT_BEST;
        return difference > 0 ? Position.BEHIND : Position.AHEAD;
    }
}
