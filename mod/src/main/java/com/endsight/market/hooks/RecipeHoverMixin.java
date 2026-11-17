package com.endsight.market.hooks;

import com.endsight.market.MarketTooltip;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Pseudo;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Optional integration lives in DSM so the recipe browser needs no market dependency. */
@Pseudo
@Mixin(targets = "com.endsight.storage.Recipes", remap = false)
abstract class RecipeHoverMixin {
    @Shadow private static String tipFor;
    @Unique private static long dsm$nextRefresh;

    @Inject(method = "tooltip", at = @At("HEAD"))
    private static void dsm$refreshPrice(CallbackInfo ci) {
        long now = System.currentTimeMillis();
        if (!MarketTooltip.active() || now < dsm$nextRefresh) return;
        // Older Endsight builds keep these lines for the entire hover. An asynchronous
        // answer cannot enter that saved list until the tooltip is rebuilt.
        dsm$nextRefresh = now + 500;
        tipFor = null;
    }
}
