package com.endsight.market;

import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.ConfirmLinkScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;

final class LinkScreen extends Screen {
    private final Screen previous;
    private final String code;
    private boolean copied;

    LinkScreen(Screen previous, String code) {
        super(Component.literal("Link DSM"));
        this.previous = previous;
        this.code = code;
    }

    @Override
    protected void init() {
        int middle = width / 2;
        int row = height / 2 + 4;
        addRenderableWidget(Button.builder(Component.literal("Copy code"), button -> {
            minecraft.keyboardHandler.setClipboard(code);
            copied = true;
            button.setMessage(Component.literal("Copied"));
        }).bounds(middle - 101, row, 97, 20).build());
        addRenderableWidget(Button.builder(Component.literal("Open DSM site"), ConfirmLinkScreen.confirmLink(this, MarketApi.SITE))
                .bounds(middle + 4, row, 97, 20).build());
        addRenderableWidget(Button.builder(Component.literal("Done"), button -> onClose())
                .bounds(middle - 48, row + 28, 96, 20).build());
    }

    @Override
    public void extractRenderState(GuiGraphicsExtractor graphics, int mouseX, int mouseY, float tickDelta) {
        super.extractRenderState(graphics, mouseX, mouseY, tickDelta);
        int middle = width / 2;
        int row = height / 2;
        graphics.centeredText(font, title, middle, row - 54, 0xFFF2F1ED);
        graphics.centeredText(font, "Enter this one-time code on the DSM site", middle, row - 35, 0xFFD1CFCA);
        graphics.centeredText(font, code, middle, row - 18, 0xFF6FCE8B);
        if (copied) graphics.centeredText(font, "Code copied to clipboard", middle, row + 62, 0xFFD1CFCA);
    }

    @Override
    public void onClose() { minecraft.setScreen(previous); }

    @Override
    public boolean isPauseScreen() { return false; }
}
