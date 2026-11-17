package com.endsight.market;

import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.core.component.DataComponents;
import net.minecraft.network.chat.*;
import net.minecraft.world.item.ItemStack;
import java.util.*;

public final class SharedChatTooltip {
    private SharedChatTooltip() {}
    private static SharedItemText catalog;
    private static boolean loading;
    private static long retryAt;
    private static final Map<Component,Ref> seen=new IdentityHashMap<>();
    private static final class Ref {
        final String text;
        SharedItemText.Parsed parsed;
        boolean resolved;
        JsonObject raw;
        String key;
        boolean partial=true;
        long used;
        List<Component> price=List.of();
        Component rendered;
        Ref(String text) {this.text=text;}
    }
    private static Ref prepare(Component tooltip) {
        long now=System.currentTimeMillis();
        Ref ref=seen.get(tooltip);
        if(ref==null) {
            String text=tooltip.getString();
            ref=new Ref(SharedItemText.candidate(text)?text:"");
            if(seen.size()>256)seen.values().removeIf(value->now-value.used>10_000);
            if(seen.size()>512)seen.clear();
            seen.put(tooltip,ref);
        }
        ref.used=now;
        if(ref.text.isEmpty())return null;
        if(catalog==null) {
            if(!loading && now>=retryAt) {
                loading=true;
                MarketApi.catalog().whenComplete((json,error)->Minecraft.getInstance().execute(()-> {
                    loading=false;
                    if(error!=null){retryAt=System.currentTimeMillis()+20_000;return;}
                    try{catalog=new SharedItemText(json);}catch(RuntimeException e){retryAt=System.currentTimeMillis()+20_000;}
                }));
            }
            return null;
        }
        if(ref.raw==null) {
            if(!ref.resolved){ref.parsed=catalog.parse(ref.text);ref.resolved=true;}
            if(ref.parsed==null)return null;
            ref.raw=ownedMatch(ref.parsed);
            ref.partial=ref.raw==null;
            if(ref.raw==null)ref.raw=ref.parsed.raw();
            ref.key=ref.raw.toString();
        }
        return ref;
    }
    static void queue(Component tooltip) {
        Ref ref=prepare(tooltip);
        if(ref!=null)MarketTooltip.priceLines(ref.key,ref.raw);
    }
    public static Style decorate(Style style) {
        if(!MarketTooltip.active() || !(style.getHoverEvent() instanceof HoverEvent.ShowText hover))return style;
        Ref ref=prepare(hover.value());
        if(ref==null)return style;
        List<Component> prices=MarketTooltip.priceLines(ref.key,ref.raw);
        if(prices.isEmpty())prices=List.of(Component.literal("No reliable market price").withColor(0x999999));
        if(ref.rendered==null || !ref.price.equals(prices)) {
            MutableComponent text=hover.value().copy();
            if(ref.partial)text.append(Component.literal("\nEstimate · shown upgrades only").withColor(0x999999)
                    .withStyle(s->s.withFont(FontDescription.DEFAULT)));
            for(Component line:prices)text.append(Component.literal("\n")).append(line.copy().withStyle(s->s.withFont(FontDescription.DEFAULT)));
            ref.rendered=text;ref.price=prices;
        }
        return style.withHoverEvent(new HoverEvent.ShowText(ref.rendered));
    }
    private static JsonObject ownedMatch(SharedItemText.Parsed shared) {
        var mc=Minecraft.getInstance();
        if(mc.player==null)return null;
        for(int i=0;i<mc.player.getInventory().getContainerSize();i++) {
            ItemStack stack=mc.player.getInventory().getItem(i);
            if(stack.isEmpty() || !SharedItemText.key(stack.getHoverName().getString()).equals(SharedItemText.key(shared.title())))continue;
            var lore=stack.get(DataComponents.LORE);
            if(lore==null)continue;
            String body=String.join("\n",lore.lines().stream().map(Component::getString).toList());
            if(!SharedItemText.key(body).equals(SharedItemText.key(shared.lore())))continue;
            JsonObject raw=Market.itemJson(stack);
            if(raw!=null){raw.addProperty("count",shared.raw().get("count").getAsInt());return raw;}
        }
        return null;
    }
}
