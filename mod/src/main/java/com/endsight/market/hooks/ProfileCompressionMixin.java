package com.endsight.market.hooks;

import com.endsight.market.HttpCompression;
import java.net.http.HttpRequest;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Pseudo;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.ModifyArgs;
import org.spongepowered.asm.mixin.injection.invoke.arg.Args;

@Pseudo
@Mixin(targets = "com.endsight.profile.ProfileClient", remap = false)
abstract class ProfileCompressionMixin {
    @ModifyArgs(method = {"get", "prices"}, at = @At(value = "INVOKE", target = "Ljava/net/http/HttpClient;sendAsync(Ljava/net/http/HttpRequest;Ljava/net/http/HttpResponse$BodyHandler;)Ljava/util/concurrent/CompletableFuture;"))
    private static void dsm$compressedProfiles(Args args) {
        args.set(0, HttpCompression.request((HttpRequest)args.get(0)));
        args.set(1, HttpCompression.handler());
    }
}
