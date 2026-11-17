package com.endsight.market;

import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;

/**
 * What each side of a trade is worth, beside the window. Replacing the whole trade screen would need
 * scissor clipping this render path does not have, and taking over a menu that moves real items is
 * the riskiest thing a mod can do. A panel next to the vanilla window shows the same
 * answer and cannot get in the way of a click.
 *
 * Dragsim labels the left side "You" and the other player's side on the right.
 */
final class TradeTotals {
    private TradeTotals() {}

    private static final int WIDTH = 118;

    static void draw(GuiGraphicsExtractor g, Minecraft mc, AbstractContainerScreen<?> screen, double[] sides) {
        Font font = mc.font;
        int x = Math.max(4, screen.width / 2 - 176 / 2 - WIDTH - 8);
        int y = screen.height / 2 - 60;
        g.fill(x, y, x + WIDTH, y + 58, 0xE0101116);
        if (sides == null) {
            g.text(font, "Pricing...", x + 8, y + 8, 0xFF9A9AA0, false);
            return;
        }
        double give = sides[0], take = sides[1], net = take - give;
        g.text(font, "You give " + MarketTooltip.coins(give), x + 8, y + 8, 0xFFE9E7E1, false);
        g.text(font, "You get " + MarketTooltip.coins(take), x + 8, y + 22, 0xFFE9E7E1, false);
        g.text(font, (net >= 0 ? "Profit " : "Loss ") + MarketTooltip.coins(Math.abs(net)), x + 8, y + 40, net >= 0 ? 0xFF6FCE8B : 0xFFEC7A70, false);
    }
}
