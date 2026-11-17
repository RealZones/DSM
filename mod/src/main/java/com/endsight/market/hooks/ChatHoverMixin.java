package com.endsight.market.hooks;

import com.endsight.market.SharedChatTooltip;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.network.chat.Style;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.ModifyVariable;

@Mixin(GuiGraphicsExtractor.class)
abstract class ChatHoverMixin {
    @ModifyVariable(method="componentHoverEffect",at=@At("HEAD"),argsOnly=true)
    private Style dsm$textItemPrice(Style style) {return SharedChatTooltip.decorate(style);}
}
