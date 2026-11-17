"""The price engine for Dragsim's items.

Most rules here exist to stop one specific way of being fooled: a troll listing, a lone gift, a
stack sold as one, an upgraded copy read as a plain one. What Dragsim needed of its own, and why:

  - Enchants, gems and attributes do not exist here. Their place is taken by what Dragsim
    players actually apply: reforges, recombs, books, stars, skins, dyes, drill parts.
  - A bucket's stack size is a band, not an exact count. Dragon Claw stacks sell at 0.45x the
    single price and Warden Catalyst stacks at 1.12x, so they cannot share a bucket, but there
    are too few sales to give every count its own.
  - An item with no sales yet takes its parts from craft cost and NPC prices, because 700 of
    the 940 catalog items have never traded.

Everything tuned for this market lives in `Params`, whose defaults are the plain untuned rules. The
tuned values are chosen by tools/backtest.py, so the difference is always measurable.
"""
import collections
import math

from . import fees
from .items import RANK, WITHER_SCROLLS

DAY = 86_400_000
MIN_TARGET = 200_000            # under this a flip is not worth a look
UNDER_MEDIAN = 0.05             # a deal must be at least this far under its median, see deal()
MAX_SEARCH_PARTS = 64
# Stand-in for a base nobody can price. It was 20b, with lore's "unavailable" line at 10b, which
# held for a market where 20b was out of reach. Here a Hyperion costs 133b to craft and had never
# sold alone, so min(craft, 20b) picked the stand-in and its hover said "Craft ingredients
# unavailable" (Fear's screenshot, 2026-11-16). Every real price must sit far below it.
UNAVAILABLE = 10 ** 18


class Params:
    """The plain untuned rules. tools/backtest.py compares this set against tuned ones."""
    size_to_keep = 80           # sales kept per bucket
    working_size = 60           # WorkingSize
    min_refs = 4                # below this a bucket has no median at all
    min_recent = 3              # sales needed in the recent window, unless the item is clean
    recent_days = 20
    short_n = 5                 # GetShortTermBatch
    short_n_fast = 6
    long_days = 30
    long_recent_days = 10
    median_skip = 2.0           # GetMedian's skipAdjust: 2 is the lower median
    risky_skip = 3.0            # 3 is roughly the 66th percentile
    use_long = True             # the second half of min(short, long)
    half_life_h = 0.0           # 0 keeps a flat vote. Above 0, older sales lose weight
    lbin_cap = False            # cap the median at the cheapest live BIN of the same bucket
    lbin_blend = 0.0            # how far to pull the median down toward a cheaper live BIN
    thin_fallback = False       # price thin buckets from what few sales they have
    keep_floor = 500_000        # CapKeyLength's lowest value worth a place in the key
    keep_clean_div = 20
    keep_sum_div = 22
    keep_max = 5
    read_addback = 1 / 3        # AssignMedian: share of dropped parts added back on reading
    closest_addback = 1 / 9     # GetEstimatedMedian: parts on top of the best smaller key
    craft_cap = 1.1             # cap at craft cost: parts never count for more than this
    # Share of a part's cost a buyer pays back, by kind. None leaves the plain bucket logic alone.
    retention = None


class Tuned(Params):
    """The same rules with Dragsim's numbers, each one measured by tools/ on real sales, chosen on
    10-23 to 11-03 and checked on the days after. See REPORT.md for the tables."""
    # Prices move too fast here for a week-old sale to vote like a fresh one, and min(short, long)
    # is what left the untuned rules 7-11% under real sales. Decay alone took 20.0% error to 16.7%.
    half_life_h = 36
    use_long = False
    thin_fallback = True
    # What buyers pay back, measured by stripping upgraded sales bare. 3+ part items: 22.7% to 11.5%.
    retention = {"reforge": 0.35, "skin": 0.30, "dye": 0.30, "rune": 0.30, "pet_item": 0.43, "other": 0.60}


class Ref:
    """ReferencePrice."""
    __slots__ = ("id", "price", "day", "at", "seller", "buyer", "sell_min", "count")

    def __init__(self, id, price, at, seller, buyer, sell_min=0, count=1):
        self.id = id
        self.price = price
        self.at = at
        self.day = at // DAY
        self.seller = seller
        self.buyer = buyer
        self.sell_min = sell_min
        self.count = count

    def halved(self):
        return Ref(self.id, self.price / 2, self.at, self.seller, self.buyer, self.sell_min, self.count)


class Live:
    """One live BIN, the entries of ReferenceAuctions.Lbins."""
    __slots__ = ("id", "price", "at", "end", "seller", "count")

    def __init__(self, id, price, at, end, seller, count=1):
        self.id = id
        self.price = price
        self.at = at
        self.end = end
        self.seller = seller
        self.count = count


class Bucket:
    """ReferenceAuctions."""
    __slots__ = ("price", "refs", "lbins", "volume", "volatility", "oldest", "hits", "risky", "time_to_sell",
                 "ded_count", "key")

    def __init__(self, key, keep):
        self.key = key
        self.price = 0
        self.refs = collections.deque(maxlen=keep)
        self.lbins = []
        self.volume = 0.0
        self.volatility = 0
        self.oldest = 0
        self.hits = 0
        self.risky = 0
        self.time_to_sell = 0
        self.ded_count = 0


class Lookup:
    """PriceLookup."""
    __slots__ = ("entry", "buckets", "volume", "clean_per_day", "clean_key", "clean_per_tier", "multi_rarity")

    def __init__(self, entry=""):
        self.entry = entry
        self.buckets = {}
        self.volume = 0.0
        self.clean_per_day = {}
        self.clean_key = None
        self.clean_per_tier = {}
        self.multi_rarity = False


class Elem:
    """RankElem: one part of an item, valued in coins."""
    __slots__ = ("kind", "value", "coins", "estimate", "label")

    def __init__(self, kind, value, coins, estimate=False, label=None):
        self.kind = kind
        self.value = value
        self.coins = coins
        self.estimate = estimate
        self.label = label or f"{kind} {value}"

    @property
    def key(self):
        return (self.kind, str(self.value))

    def counted(self):
        return self.coins / 20 if self.estimate else self.coins


class Key:
    """AuctionKey. Immutable and hashable, so it can index a bucket."""
    __slots__ = ("tier", "mods", "count", "_hash")

    def __init__(self, tier, mods, count):
        self.tier = tier
        self.mods = tuple(sorted(mods))
        self.count = count
        self._hash = hash((tier, self.mods, count))

    def __hash__(self):
        return self._hash

    def __eq__(self, other):
        return isinstance(other, Key) and self.tier == other.tier and self.mods == other.mods and self.count == other.count

    def is_clean(self):
        return not self.mods and self.count <= 1

    def __str__(self):
        return f"{self.tier}|{','.join(k + '=' + v for k, v in self.mods)}|x{self.count}"


class Detailed:
    """KeyWithValueBreakdown."""
    __slots__ = ("key", "breakdown", "subtract", "removed")

    def __init__(self, key, breakdown, subtract, removed):
        self.key = key
        self.breakdown = breakdown
        self.subtract = subtract
        self.removed = removed


def stack_band(count):
    return 1 if count <= 1 else 2 if count <= 16 else 3


def lower_median(values, skip=2.0):
    """GetMedian without the per-day adjustment: sort high to low and skip n/skip, so an even
    list lands on the lower middle. That lean is deliberate, it prices cautiously."""
    if not values:
        return 0
    ordered = sorted(values, reverse=True)
    return ordered[int(len(ordered) / skip)]


def amm(refs, working_size):
    """ApplyAntiMarketManipulation, from its readable reference form at line 3001."""
    n = len(refs)
    if not n:
        return []
    pairs = {}
    for r in refs:
        pairs.setdefault(frozenset((r.seller, r.buyer)), []).append(r)
    manipulated = set()
    for group in pairs.values():
        if len(group) > 1 and not all(x.seller == group[0].seller for x in group):
            manipulated.add(group[0].seller)
    # One seller holding a third of the top half of prices is walking the price up.
    top = sorted(refs, key=lambda r: -r.price)[: n // 2]
    counts = collections.Counter(r.seller for r in top)
    person = None
    for seller, count in counts.most_common():
        if count >= max(n // 3, 3):
            person = seller
            break
    stage = [r for r in reversed(refs) if r.seller not in manipulated and r.buyer not in manipulated]
    stage.sort(key=lambda r: -r.day)

    def one_each(rows, attr):
        groups = {}
        for r in rows:
            groups.setdefault(getattr(r, attr), []).append(r)
        out = []
        for group in groups.values():
            ranked = sorted(group, key=lambda r: r.price)
            out.append(ranked[len(ranked) // 3])
        return out

    picked = one_each(one_each(stage, "seller"), "buyer")[:working_size]
    if person is not None:
        picked = [r.halved() if r.seller == person else r for r in picked]
    return picked


def drop_underlistings(ded, scan=5):
    """DropUnderlistings: a seller who bought in this bucket and resold cheaply is often clearing
    a mistake, so that purchase stops setting the price, unless it would raise the median."""
    first_by_buyer = {}
    for r in ded:
        first_by_buyer.setdefault(r.buyer, r)
    remove = []
    for i, target in enumerate(ded):
        batch = ded[i:i + scan]
        if len(batch) < 3:
            break
        hit = first_by_buyer.get(target.seller)
        if hit is None:
            continue
        if i < 3:
            rest = [a.price for a in batch if a.id != hit.id]
            if rest and sum(rest) / len(rest) < hit.price:
                continue
        remove.append(hit)
    if not remove or len(ded) - len(remove) < 4:
        return ded
    gone = {r.id for r in remove}
    return [r for r in ded if r.id not in gone]


class Engine:
    def __init__(self, catalog, params=None, recipes=None, bazaar=None):
        self.catalog = catalog
        self.p = params or Tuned()
        self.recipes = recipes or {}
        self.bazaar = bazaar
        self.lookups = {}
        self.listings = {}          # id -> (bucket, Live)
        self.sold_ids = set()       # a listing can reach us after its own sale, see add_listing
        self.shop = {}              # entry -> coins, for what an NPC sells
        self.reported = {}          # entry -> price from receipts, see reported.py
        self.bot = {}               # entry -> the Discord bot's price, see Service.bot_prices
        self.now = 0
        self._part_cache = {}
        self._craft_cache = {}
        self._cache_day = None

    # ------------------------------------------------------------------ parts and their value

    def today(self):
        return self.now // DAY

    def lookup(self, entry):
        found = self.lookups.get(entry)
        if found is None:
            found = self.lookups[entry] = Lookup(entry)
        return found

    def market_price(self, entry):
        """What a part goes for on the AH: the cheapest bucket with a real price, as
        ModifierEstimate does, so a part is never valued off its fanciest copy."""
        found = self.lookups.get(entry)
        if not found:
            return 0
        best = 0
        for bucket in found.buckets.values():
            if bucket.price and bucket.key.count <= 1 and (not best or bucket.price < best):
                best = bucket.price
        if best:
            return best
        # Thin parts have sales but no median. Better a rough number than treating a 300m part
        # as free, and the caller marks it as an estimate.
        recent = [r.price for b in found.buckets.values() for r in b.refs if r.day >= self.today() - 14]
        return lower_median(recent) if recent else 0

    def price_of(self, entry):
        """(coins, is_estimate) for one unit of anything."""
        if not entry:
            return 0, True
        stamp = self.now // (5 * 60_000)
        cached = self._part_cache.get(entry)
        if cached is not None and cached[2] == stamp:
            return cached[0], cached[1]
        value, estimate = self._unit(entry, frozenset())
        # Unrecognized client-supplied parts can have arbitrary ids and no market evidence.
        if value or entry in self.catalog.items:
            self._part_cache[entry] = (value, estimate, stamp)
        return value, estimate

    def _unit(self, entry, seen):
        """Real evidence first: bazaar fills, then AH sales. What it costs to make comes after,
        and a bazaar offer last of all, since an offer is only what somebody hopes for."""
        if self.bazaar:
            value = self.bazaar.price(entry)
            if value:
                return value, False
        found = self.lookups.get(entry)
        value = self.market_price(entry)
        shop = self.shop.get(entry)
        if shop and (not value or shop < value):
            return shop, False      # nobody pays more than a shop that never runs out
        if value:
            return value, not (found and any(b.price for b in found.buckets.values()))
        row = self.reported.get(entry)
        if row:
            return row["value"], True
        # Nothing traded anywhere: the bot's price, which holds Fear's hand prices for what never
        # reaches the AH. Without it the catalog's NPC price stood in. Necron's Handle has never
        # once been listed and is 120b by hand, but it counted as its 100m NPC price, so the
        # Hyperion recipe's Necron's Blade came to 404m and Hyperion to 30b, not 150b (2026-11-15).
        value = self.bot.get(entry)
        if value:
            return value, True
        if entry in seen:
            # A recipe that leads back to the item being made. Without real evidence it is not
            # available here; falling through reached its NPC price. Wither Blades convert into
            # each other, so the loop always found a "100m Astraea" and priced every Hyperion,
            # Scylla and Valkyrie at 3-7b instead of ~133-144b (2026-11-16).
            return 0, True
        value = self.craft_cost(entry, seen)
        if value:
            return value, True
        if self.bazaar:
            value = self.bazaar.ask(entry)
            if value:
                return value, True
        return self.catalog.npc(entry), True

    def craft_cost(self, entry, seen=frozenset()):
        """Cheapest known way to make one. Ingredients use their own best price and recurse only
        when they have none."""
        recipes = self.recipes.get(entry)
        if not recipes or entry in seen:
            return 0
        stamp = self.now // (10 * 60_000)
        # Conversion recipes are cyclic. A cost found while another blade is forbidden
        # cannot be reused when that blade is available on a different path.
        cache_key = (entry, seen)
        cached = self._craft_cache.get(cache_key)
        if cached and cached[1] == stamp:
            return cached[0]
        seen = seen | {entry}
        best = 0
        for recipe in recipes:
            total = recipe.get("coins") or 0
            ok = not recipe.get("missing")
            for need, amount in (recipe.get("need") or {}).items():
                unit = self._unit(need, seen)[0] if ok else 0
                if ok and need == recipe.get("carrier"):
                    made = self.craft_cost(need, seen)
                    if made and (not unit or made < unit):
                        unit = made
                if not unit:
                    ok = False
                    break
                total += unit * amount
            if ok and total > 0:
                total /= (recipe.get("yield") or 1)
                if not best or total < best:
                    best = total
        self._craft_cache[cache_key] = (best, stamp)
        return best

    def select_valuable(self, item):
        """SelectValuable plus NormalizeData: what could move this item's price, as parts."""
        elems = []
        p = self.price_of
        if item.recomb:
            coins, est = p("recombobulator_3000")
            elems.append(Elem("recomb", 1, coins, est, "Recombobulator"))
        if item.stars:
            coins, est = p("ritual_residue")
            # Star cost is not published. 1+2+3+4+5 fits players asking for 15 residue, and it
            # stays an estimate until a real number replaces it.
            elems.append(Elem("stars", item.stars, coins * star_residue(item.stars), True, f"{item.stars} stars"))
        if item.hpb:
            coins, est = p("hot_potato_book")
            elems.append(Elem("hpb", book_band(item.hpb), coins * item.hpb, est, f"{item.hpb} Hot Potato Books"))
        if item.ghpb:
            coins, est = p("golden_hot_potato_book")
            elems.append(Elem("ghpb", item.ghpb, coins * item.ghpb, est, f"{item.ghpb} Golden Hot Potato Books"))
        if item.aow:
            coins, est = p("art_of_war")
            elems.append(Elem("aow", item.aow, coins * item.aow, est, "Art of War"))
        if item.sync:
            coins, est = p("final_synchronizer")
            elems.append(Elem("sync", item.sync, coins * item.sync, est, f"{item.sync} Final Synchronizers"))
        if item.enrich:
            coins, est = p("magic_find_enrichment")
            elems.append(Elem("enrich", item.enrich, coins, est, "Enrichment"))
        if item.skin:
            entry = self.catalog.skin_entry(item.skin)
            coins, est = p(entry)
            elems.append(Elem("skin", item.skin, coins, est, self.catalog.name(entry)))
        if item.pet_skin:
            coins, est = p(item.pet_skin)
            elems.append(Elem("skin", item.pet_skin, coins, est, self.catalog.name(item.pet_skin)))
        if item.dye:
            entry = self.catalog.dye_entry(item.dye)
            coins, est = p(entry)
            elems.append(Elem("dye", item.dye, coins, est, self.catalog.name(entry)))
        if item.rune:
            coins, est = p(item.rune)
            elems.append(Elem("rune", item.rune, coins, est, self.catalog.name(item.rune)))
        if item.scrolls:
            values = [p(entry) for entry in item.scrolls]
            # One aggregate kind preserves every scroll when similarity indexes kinds.
            # Three repeated "scroll" kinds would silently overwrite one another.
            elems.append(Elem("scrolls", "+".join(item.scrolls), sum(v for v, _ in values),
                              any(est for _, est in values), "Wither scrolls"))
        if item.coated:
            coins, est = p("void_powder_coating")
            elems.append(Elem("coated", 1, coins, est, "Coating"))
        # Pets are not keyed on their level. It would only thin the data: over 1,200 pet sales,
        # Lvl 1 and Lvl 100 go for the same (Enderman 60m and 60m, Ender Dragon 1b and 990m).
        if item.reforge:
            coins, est = self._reforge_price(item.reforge)
            elems.append(Elem("reforge", item.reforge, coins + reforge_coins(item.rarity), est,
                              f"{item.reforge.title()} reforge"))
        return elems

    def _reforge_price(self, name):
        """Several valid stones may apply one reforge; do not pick an unpriced/expensive alias."""
        entries = (getattr(self.catalog, "reforge_stones", {}) or {}).get(name)
        if not entries:
            single = self.catalog.reforge_stone.get(name)
            entries = (single,) if single else ()
        values = [self.price_of(entry) for entry in entries]
        return min((row for row in values if row[0] > 0), key=lambda row: row[0], default=(0, True))

    def removable_value(self, item):
        """GetRemovableItemValue: parts a player can take off again count in full and never
        split a bucket, since a buyer can always pull them and resell them."""
        total = 0.0
        for entry in (item.engine, item.tank, item.module, item.pet_item):
            if entry:
                total += self.price_of(entry)[0]
        return total

    def full_craft(self, item, value=None):
        """The full craft cost the hover lines show, for one item. The base is the
        cheaper of making it and buying it clean, then every part on this copy is added at what
        the part costs: in full, not at the share a buyer pays back, since this is what building
        it costs. Both are then capped at the exact item's median, which lore.py does because the
        estimate is needed for it. Returns (obtain, summary, craftable).

        "Clean" is this market's estimate of the bare item. clean_price() looked like the right
        number for it, but it is the bottom of every variant's sales, built to value
        parts, and live it read 45m for a Draconic Talisman selling at 177m and 1k for a Crypt
        Cache the bazaar sells at 80m. `value` is the item's own estimate when the caller has one,
        which spares a second estimate when there is nothing on the item to take off.

        A recipe whose ingredients cannot be priced gets an out-of-reach number (UNAVAILABLE),
        so it reads "Craft ingredients unavailable" rather than "not craftable"."""
        parts = self.select_valuable(item)
        extra = self.removable_value(item)
        craftable = bool(self.recipes.get(item.entry))
        craft = (self.craft_cost(item.entry) or UNAVAILABLE) if craftable else 0
        clean = value if value is not None and not parts and not extra else self.estimate(item.bare())["value"]
        # Math.Min(craftPrice ?? clean, clean == 0 ? 20_000_000_000 : clean)
        obtain = min(craft or clean, clean or UNAVAILABLE)
        return obtain, obtain + sum(e.coins for e in parts) + extra, craftable

    # ------------------------------------------------------------------ item to key

    def detailed_key(self, item, sale=0):
        """DetailedKeyFromSaveAuction and CapKeyLength."""
        p = self.p
        found = self.lookups.get(item.entry)
        clean = found.clean_per_tier.get(item.rarity, 0) if found else 0
        elems = self.select_valuable(item)
        threshold = max(p.keep_floor, clean / p.keep_clean_div if clean else 0)
        total = clean + sum(e.counted() for e in elems)
        threshold = max(threshold, total / p.keep_sum_div)
        share = 1.0 if not sale or not total or sale > total else sale / total
        ranked = sorted((e for e in elems if e.coins), key=lambda e: -e.coins)
        fixed = [e for e in elems if not e.coins]
        keep, removed = [], []
        for i, e in enumerate(ranked):
            drop = (i >= p.keep_max
                    or (1 <= i < p.keep_max and e.coins < threshold)
                    or (i < 1 and (e.coins < p.keep_floor or e.coins < threshold / 4)))
            # These describe the copy even when their price is absent or the value cap
            # is full. Dropping them would merge clean/scrolled or different-rune sales.
            if e.kind in ("scrolls", "rune"):
                drop = False
            (removed if drop else keep).append(e)
        subtract = 0.0
        for e in removed:
            subtract += e.coins * share / (10 if e.estimate else 1)
        kept = keep + fixed
        if item.recomb and not any(e.kind == "recomb" for e in kept):
            tier = max(item.rarity - 1, 0)      # a dropped recomb leaves the rarity it added behind
        else:
            tier = item.rarity
        key = Key(tier, [e.key for e in kept], stack_band(item.count))
        return Detailed(key, kept, subtract, removed)

    def clean_price(self, entry, tier, force=False):
        """GetCleanItemPrice: near the bottom of recent sales across every variant, which is
        what the bare item goes for once the upgrades are ignored."""
        found = self.lookups.get(entry)
        if not found:
            return 0
        if not force and tier in found.clean_per_tier:
            return found.clean_per_tier[tier]
        refs = []
        for key, bucket in found.buckets.items():
            if tier - 1 > key.tier:
                continue
            refs.extend(self._filtered_refs(found, key, bucket.refs))
        if not refs:
            return 0
        size = int(min(max(found.volume * 10, 50), len(refs)))
        refs.sort(key=lambda r: (-r.day, r.price))
        chosen = sorted(refs[:size], key=lambda r: r.price)
        index = min(size // 30 + 1, len(chosen) - 1)
        return chosen[index].price

    # ------------------------------------------------------------------ the median

    def _filtered_refs(self, found, key, refs):
        """Require independent confirmation before a large price rise can set a quote.

        A 400m Rift Goo sale outweighed older 1m sales after time decay, in a separate
        stack band. Compare unit prices across matching rarity and upgrades so changing
        the stack size cannot isolate the sale from the rest of that item's history.
        """
        since = self.today() - self.p.long_days
        npc = self.catalog.npc(found.entry)
        refs = [ref for ref in refs if ref.price > 0 and ref.price >= npc]
        peers = [ref for other, bucket in found.buckets.items() if other.tier == key.tier and other.mods == key.mods
                 for ref in bucket.refs if ref.day >= since and ref.price > 0 and ref.price >= npc
                 and ref.seller and ref.buyer and ref.seller.casefold() != ref.buyer.casefold()]
        independent = amm(peers, self.p.working_size)
        if len(independent) < SPIKE_SUPPORT:
            return list(refs)
        prices = sorted(ref.price for ref in independent)
        baseline = prices[(len(prices) - 1) // 4]
        accepted = []
        for ref in refs:
            if ref.price > baseline * SPIKE_RATIO:
                support = sum(ref.price / 2 <= other.price <= ref.price * 2 for other in independent)
                if support < SPIKE_SUPPORT:
                    continue
            accepted.append(ref)
        return accepted

    def _median(self, refs, clean_days, skip=None):
        """GetMedian with SelectAdjustedPrice: on an item with its own clean history, sales from
        days the clean price was higher are walked down to today's level first. It only ever
        lowers, so a rising market is left alone."""
        skip = skip or self.p.median_skip
        if not refs:
            return 0
        today = self.today()
        day_now = (clean_days.get(today) or clean_days.get(today - 1) or 0) if clean_days else 0

        def adjusted(r):
            if not day_now:
                return r.price
            clean = clean_days.get(r.day)
            if not clean:
                return r.price
            shift = (day_now - clean) / clean
            return r.price - (min(clean - day_now, r.price - shift * r.price) if clean > day_now else 0)

        if self.p.half_life_h:
            return self._weighted(refs, adjusted, skip)
        return lower_median([adjusted(r) for r in refs], skip)

    def _weighted(self, refs, adjusted, skip):
        """The tuned form of the same vote. Flat medians treat a week-old sale like a fresh one,
        which is what made the flat median lag Dragsim's fast prices."""
        half_life = self.p.half_life_h * 3_600_000
        pairs = sorted(((adjusted(r), 0.5 ** ((self.now - r.at) / half_life)) for r in refs), key=lambda x: -x[0])
        target = sum(w for _, w in pairs) * (1 / skip)
        seen = 0.0
        for price, weight in pairs:
            seen += weight
            if seen >= target:
                return price
        return pairs[-1][0]

    def _is_dropping(self, refs, short):
        relevant = list(reversed(refs))[:6]
        bought = {}
        for r in relevant:
            bought.setdefault(r.buyer, r)
        drops = []
        for item in relevant:
            purchase = bought.get(item.seller)
            if purchase and purchase.price > item.price * 0.97 and item.price < short:
                drops.append(item.price / max(purchase.price, short * 0.97) * 0.97)
        if len(drops) <= 1:
            return False, 1.0
        return True, sorted(drops, reverse=True)[1]

    def _volatility(self, found, bucket, short, long_):
        """GetVolatility, including its downward trend cut."""
        refs = list(bucket.refs)
        clean_days = found.clean_per_day if found else {}
        old = self._median(refs[:5], clean_days)
        second, third = 0, 0
        if len(refs) > 8:
            second = self._median(list(reversed(refs))[5:10], clean_days)
        if len(refs) > 11:
            third = self._median(list(reversed(refs))[9:13], clean_days)
        medians = [old, second, long_, short]
        mean = sum(medians) / 4 or 1
        scaled = [m / mean for m in medians]
        mean = sum(scaled) / 4
        spread = math.sqrt(sum((m - mean) ** 2 for m in scaled) / 4)
        volatility = int(min(spread * 100, 120))
        price = min(short, long_)
        if third > second and long_ > short and second > short and second:
            difference = min(second - short, long_ - short)
            share = difference / second / 2
            if difference > 0 and price > difference and share < 0.2:
                price -= price * share
        return volatility, price

    def _risky(self, month, clean_days, limited, median):
        """Get66thPercentile: the upper end, for items steady enough to risk it."""
        long_ = self._median([d for d in month if d.day >= self.today() - 10], clean_days, self.p.risky_skip)
        short = self._median([d for d in month if d.day >= self.today() - 2], clean_days, self.p.risky_skip)
        limit = limited * 10 / 9 + 1_000_000
        if median > limited:
            limit = limited * 11 / 10
        return min(short, long_, limit)

    def _cap_at_higher(self, found, bucket, key, price):
        """CapPriceAtHigherLevelKey: a better copy that recently sold for less caps this one."""
        best = 0
        for other_key, other in found.buckets.items():
            if (other.price and other.price < price and other_key != key and other.oldest >= bucket.oldest
                    and other.ded_count > 3 and other.volume * 5 >= bucket.volume
                    and is_higher_value(key, other_key) and (not best or other.price < best)):
                best = other.price
        return best or price

    def cap_at_craft(self, entry, price, det, current):
        """Cap at craft cost: nobody pays far over what it costs to make the same thing."""
        found = self.lookups.get(entry)
        key = det.key
        if not key.mods and not (found and found.multi_rarity):
            craft = self.craft_cost(entry)
            if craft:
                stack = 1 if key.count <= 1 else (8 if key.count == 2 else 32)
                cost = craft * stack * 1.08
                if cost < 500_000:
                    cost *= 2
                cost += 750_000     # effort: people pay to skip the work
                if cost < price:
                    return cost
        parts = [e for e in det.breakdown if e.coins]
        if not parts:
            return price
        base = self.clean_price(entry, key.tier)
        if not base or current == base:
            return price
        total = sum(e.coins for e in parts)
        if total > 0:
            capped = min(base + total * self.p.craft_cap, price)
            if capped > 0:
                return capped
        return price

    def update_median(self, bucket, entry, det):
        """UpdateMedianCore. Long, because each rule is a separate way of being fooled."""
        p = self.p
        found = self.lookup(entry)
        refs = self._filtered_refs(found, det.key, bucket.refs)
        if len(refs) < p.min_refs and not p.thin_fallback:
            bucket.price = 0
            return
        today = self.today()
        ded = drop_underlistings(amm(refs, p.working_size))
        size = len(ded)
        clean_key = det.key.is_clean()
        recent = sum(1 for d in ded if d.day >= today - p.recent_days)
        if not p.thin_fallback and (size <= 3 or (recent < p.min_recent and not clean_key)):
            bucket.price = 0
            return
        if not ded or (p.thin_fallback and size < 2):
            bucket.price = 0
            return
        days = max(today - min(d.day for d in ded) + 1, 1)
        bucket.volume = size / days
        ded.sort(key=lambda r: (-r.day, -r.at))
        short_list = ded[:p.short_n_fast if bucket.volume > 4 else p.short_n]

        if found.clean_key is None and len(found.buckets) >= 1:
            self._update_clean_key(found)
        is_clean = det.key == found.clean_key
        clean_days = {}
        if is_clean:
            meaningful = sum(1 for b in found.buckets.values() if len(b.refs) >= 4)
            if meaningful <= 1 and len(found.clean_per_day) >= 5:
                clean_days = found.clean_per_day

        short = self._median(short_list, clean_days)
        dropping, rate = self._is_dropping(refs, short)
        if dropping:
            floor = found.clean_per_tier.get(det.key.tier, 0) * 0.99
            short = max(short * max(rate, 0.82), floor)
        bucket.oldest = min(s.day for s in short_list[:4])
        if (len(short_list) >= 3 and bucket.oldest - short_list[0].day <= -5
                and short_list[0].id != max(short_list, key=lambda o: o.price).id
                and bucket.volume > 0.3):
            reduced = (min(s.price for s in short_list) + short * 2) / 3
            short = max(short * 0.8, reduced)

        month = [d for d in ded if d.day >= today - p.long_days]
        if len(month) > 24:
            fresh = [d for d in month if d.day >= today - p.long_recent_days]
            long_ = self._median(fresh if len(fresh) >= 5 else month, clean_days)
        elif len(month) > 5:
            long_ = self._median(month, clean_days)
        else:
            long_ = self._median(ded[:29], clean_days)
        if all(d.day >= today for d in ded):
            ordered = sorted(d.price for d in ded)
            long_ = ordered[max(int(len(ordered) * 0.25), 1) - 1]
        if not p.use_long:
            long_ = short
        uncapped = min(short, long_)
        median = uncapped
        longest = [l for l in bucket.lbins if l.price > median / 2 and l.end // DAY > today + 5]
        longest.sort(key=lambda l: l.price)
        if len(longest) > 2:
            median = min(median, longest[2].price)
            short = min(short, longest[2].price)
        bucket.volatility, median = self._volatility(found, bucket, short, long_)
        bucket.hits = 0
        bucket.ded_count = size
        found.volume = sum(len(b.refs) for b in found.buckets.values()) / 60
        relevant = [d.sell_min for d in ded if d.sell_min > 0 and median * 0.96 < d.price < median * 1.1]
        average = sum(relevant) / len(relevant) if relevant else 0
        bucket.time_to_sell = average * max(1, 2 / max(bucket.volume, 0.01))

        limited = self._cap_at_higher(found, bucket, det.key, median)
        cleaned = self.clean_price(entry, det.key.tier, force=True)
        if cleaned > 0:
            found.clean_per_tier[det.key.tier] = cleaned
        tier_value = found.clean_per_tier.get(det.key.tier)
        if tier_value and limited < tier_value / 1.2:
            limited = max(limited, tier_value)
        limited = self.cap_at_craft(entry, limited, det, bucket.price)
        if limited == 0:
            limited = median
        if size > 40 or (bucket.volatility <= 8 and size > 8):
            bucket.risky = self._risky(month, clean_days, limited, median)
        elif size > 10 and bucket.volatility < 18:
            bucket.risky = (limited + self._risky(month, clean_days, limited, median)) / 2
        else:
            bucket.risky = limited
        before = median
        median = limited or 11
        bucket.price = median

        recent_raw = list(reversed(refs))[:max(6 if bucket.volume < 0.5 else 12, int(bucket.volume))]
        recent_mark = self._median(recent_raw, clean_days, p.risky_skip)
        cheap = [l for l in bucket.lbins if l.price < median * 1.1][:4]
        listed_short = (sum((l.end // DAY) - today for l in cheap) / len(cheap) if cheap else 3) < 4
        if bucket.volume >= 4 and bucket.lbins and listed_short and recent_mark > median:
            capped = before * 12 / 10 if before == median else limited
            median = min(max(bucket.risky, median), min(capped, recent_mark))
            newest = sorted(sorted(ded, key=lambda r: -r.day)[:9], key=lambda r: r.price)
            if len(newest) > 6 and newest[6].price < median:
                median = newest[6].price
        elif len(det.key.mods) <= 1:
            median = min(median, recent_mark)
        found.multi_rarity = len({k.tier for k in found.buckets}) > 2
        if is_clean and median > 0:
            found.clean_per_day[short_list[0].day] = uncapped
        if p.lbin_cap and bucket.lbins:
            low = bucket.lbins[0].price
            if low < median:
                median = low if not p.lbin_blend else median + (low - median) * p.lbin_blend
        bucket.price = median

    def _update_clean_key(self, found):
        """UpdateCleanKey: the variant that sells most and carries least, a stand-in for bare."""
        best, score = None, None
        for key, bucket in found.buckets.items():
            value = bucket.volume - len(key.mods) * 5
            if score is None or value > score:
                best, score = key, value
        found.clean_key = best
        found.clean_per_day = {}

    # ------------------------------------------------------------------ events

    def add_listing(self, lid, item, unit_price, seller, at, end, count=1):
        """UpdateLbin. A cheap listing can sell before the collector's next poll, so its row turns
        up after the sale that ended it. Added anyway it would stay for good, and 138 did, one of
        them a 1 coin Void Talisman that made every Void Talisman look worth nothing."""
        self.now = max(self.now, at)
        if lid in self.sold_ids:
            return None
        det = self.detailed_key(item)
        found = self.lookup(item.entry)
        bucket = found.buckets.get(det.key)
        if bucket is None:
            bucket = found.buckets[det.key] = Bucket(det.key, self.p.size_to_keep)
        live = Live(lid, unit_price, at, end, seller, count)
        lbins = bucket.lbins
        index = 0
        while index < len(lbins) and lbins[index].price <= unit_price:
            index += 1
        lbins.insert(index, live)
        self.listings[lid] = (bucket, live)
        return bucket

    def remove_listing(self, lid):
        """RemoveActive."""
        found = self.listings.pop(lid, None)
        if found:
            bucket, live = found
            bucket.lbins = [l for l in bucket.lbins if l.id != lid]
            return live
        return None

    def add_sale(self, sid, item, unit_price, seller, buyer, at, created_at=None):
        """AddSoldItem and AddAuctionToBucket. Returns the stored reference."""
        self.now = max(self.now, at)
        found = self.lookup(item.entry)
        extra = self.removable_value(item)
        det = self.detailed_key(item, unit_price)
        bucket = found.buckets.get(det.key)
        if bucket is None:
            bucket = found.buckets[det.key] = Bucket(det.key, self.p.size_to_keep)
        subtract = det.subtract
        if unit_price < subtract:
            subtract = min(subtract, unit_price / 2)    # at most half a sale can be explained away
        if unit_price - subtract - extra < 0:
            extra = 0
        sold_after = int((at - created_at) / 60_000 + 1) if created_at else 0
        ref = Ref(sid, unit_price - subtract - extra, at, seller, buyer, sold_after, item.count or 1)
        bucket.refs.append(ref)
        self.sold_ids.add(sid)
        self.remove_listing(sid)
        self.update_median(bucket, item.entry, det)
        return ref

    # ------------------------------------------------------------------ reading a price

    def _lbin_of(self, bucket, exclude):
        return [l for l in bucket.lbins if l.id != exclude and l.end > self.now]

    def similarity(self, want, have, want_elems, have_elems):
        """SimilarityByMarketPrice: closeness measured in coins, not in counted differences."""
        score = 0.0
        have_by = {e.kind: e for e in have_elems}
        want_by = {e.kind: e for e in want_elems}
        for elems, other in ((want_elems, have_by), (have_elems, want_by)):
            for e in elems:
                match = other.get(e.kind)
                worth = e.coins or 1_000_000
                if match is None:
                    score -= worth
                elif str(match.value) == str(e.value):
                    score += worth
                else:
                    score -= worth / 2
        tier_gap = abs(want.tier - have.tier)
        score -= tier_gap * 11_000_000
        score -= abs(want.count - have.count) * 1_000_000
        return score

    def elems_of(self, key):
        return [Elem(k, v, self._kind_value(k, v)) for k, v in key.mods]

    def _kind_value(self, kind, value):
        p = lambda e: self.price_of(e)[0]
        try:
            if kind == "recomb":
                return p("recombobulator_3000")
            if kind == "stars":
                return p("ritual_residue") * star_residue(int(value))
            if kind == "hpb":
                return p("hot_potato_book") * int(value)
            if kind == "ghpb":
                return p("golden_hot_potato_book") * int(value)
            if kind == "aow":
                return p("art_of_war") * int(value)
            if kind == "sync":
                return p("final_synchronizer") * int(value)
            if kind == "enrich":
                return p("magic_find_enrichment")
            if kind == "skin":
                return p(self.catalog.skin_entry(value))
            if kind == "dye":
                return p(self.catalog.dye_entry(value))
            if kind == "rune":
                return p(value)
            if kind == "scrolls":
                return sum(p(entry) for entry in str(value).split("+") if entry in WITHER_SCROLLS.values())
            if kind == "coated":
                return p("void_powder_coating")
            if kind == "reforge":
                return self._reforge_price(value)[0]
        except ValueError:
            pass
        return 0

    def estimate(self, item, exclude=None):
        """GetPriceInternal. `exclude` hides one listing, so the backtest can price a sale without
        seeing the very listing that sold, which is what made a first test look perfect."""
        out = {"value": 0, "median": 0, "median_key": None, "item_key": None, "volume": 0.0, "volatility": 0, "time_to_sell": 0,
               "lbin": 0, "slbin": 0, "lbin_key": None, "lbin_live": 0, "last_sale": None, "exact": False,
               "parts": [], "extra": 0, "count": item.count, "confidence": "low"}
        count = item.count or 1
        bazaar = self.bazaar.quote(item.entry) if self.bazaar else None
        found = self.lookups.get(item.entry)
        if bazaar:
            out["bz_buy"], out["bz_sell"], out["bz_price"] = bazaar["ask"], bazaar["bid"], self.bazaar.price(item.entry)
        det = self.detailed_key(item)
        extra = self.removable_value(item)
        out["item_key"] = str(det.key)
        out["extra"] = extra
        out["parts"] = [{"label": e.label, "coins": int(e.coins), "estimate": e.estimate}
                        for e in sorted(det.breakdown + det.removed, key=lambda e: -e.coins)]
        if not found:
            if bazaar and out.get("bz_price"):
                out["median"] = out["value"] = out["bz_price"]
                out["median_key"] = out["item_key"]
                out["exact"] = True
            elif item.entry in self.reported:
                out["median"] = out["value"] = self.reported[item.entry]["value"] + extra
                out["median_key"] = "reported"
            return out
        bucket = found.buckets.get(det.key)
        if bucket:
            rows = self._lbin_of(bucket, exclude)
            if rows:
                out["lbin"] = rows[0].price + extra
                out["lbin_key"] = out["item_key"]
                out["lbin_live"] = len(rows)
                if len(rows) > 1:
                    out["slbin"] = rows[1].price + extra
            if bucket.price:
                self._assign(out, det, bucket, extra, found)
                out["exact"] = True
        if not out["median"]:
            self._closest_median(out, item, found, det, extra)
        if not out["lbin"]:
            self._closest_lbin(out, item, found, det, extra, exclude)
        cap = self._lbin_cap(found, det, exclude)
        if cap and out["lbin"] and out["lbin"] > cap + extra:
            out["lbin"] = cap + extra
            out["lbin_key"] = (out["lbin_key"] or "") + "+HV"
        if not out["median"] and bazaar and out.get("bz_price"):
            out["median"] = out["bz_price"]
            out["median_key"] = out["item_key"]
        if not out["median"]:
            self._thin(out, item, found, det, extra)
        if not out["exact"]:
            self._cap_at_better(out, found, det)
        self._value(out, item, det, extra, exclude)
        return out

    def built_value(self, item, exclude=None):
        """Bare item plus each part at the share of its cost the market really pays. Almost no
        two upgraded items match, so buckets cannot price them: 223 sales in a fortnight, spread
        over as many combinations. Returns (coins, bare, parts) with each part's share."""
        keep = self.p.retention
        if not keep:
            return 0, 0, []
        elems = self.select_valuable(item)
        for attr, kind in (("pet_item", "pet_item"), ("engine", "drill"), ("tank", "drill"), ("module", "drill")):
            entry = getattr(item, attr)
            if entry:
                elems.append(Elem(kind, entry, self.price_of(entry)[0], False, self.catalog.name(entry)))
        parts = [e for e in elems if e.coins]
        if not parts:
            return 0, 0, []
        bare = self.estimate(item.bare(), exclude)["value"]
        if not bare:
            return 0, 0, []
        total = bare
        shares = []
        for e in parts:
            share = keep.get(e.kind, keep.get("other", 0.5))
            total += e.coins * share
            shares.append({"label": e.label, "coins": int(e.coins), "share": share, "estimate": e.estimate})
        return total, bare, shares

    def _value(self, out, item, det, extra, exclude):
        """The number to show and to act on. The plain median stays beside it, because it is the
        better guide to where a price settles and its caution is useful when buying."""
        last = (out["last_sale"] or {}).get("price", 0)
        if out["exact"]:
            own = last + det.subtract * self.p.read_addback + extra if last else 0
            out["value"] = middle(out["median"], own, out["lbin"])
        elif out["lbin"] and out["lbin_key"] == out["item_key"]:
            # A fuzzy median can be far out, but this exact item being listed is hard evidence.
            out["value"] = min(out["median"], out["lbin"]) if out["median"] else out["lbin"]
        else:
            out["value"] = out["median"]
        built, bare, shares = self.built_value(item, exclude)
        if built:
            out["built"], out["bare"], out["shares"] = built, bare, shares
            # Parts beat the bucket unless this exact combination has really traded.
            found = self.lookups.get(item.entry)
            bucket = found.buckets.get(det.key) if found else None
            solid = out["exact"] and bucket is not None and bucket.ded_count >= self.p.min_refs
            if not solid:
                out["value"] = built
        out["confidence"] = self._confidence(out, item.entry, det)

    def _confidence(self, out, entry, det):
        """How far to trust it. Offers and alerts stay quiet on anything but the top two."""
        found = self.lookups.get(entry)
        bucket = found.buckets.get(det.key) if found else None
        if out["median_key"] in ("thin", "craft", "reported") or not out["value"]:
            return "low"
        if bucket is not None and out["exact"] and bucket.ded_count >= 8 and bucket.volume >= 0.3:
            return "high"
        if out.get("built") or (out["exact"] and bucket is not None and bucket.ded_count >= 4):
            return "medium"
        return "low"

    def _assign(self, out, det, bucket, extra, found):
        """AssignMedian: dropped parts come back at a third, since buyers rarely pay full for
        the small stuff, removable parts at full value."""
        out["median"] = bucket.price + extra + det.subtract * self.p.read_addback
        out["volume"] = bucket.volume
        out["median_key"] = str(bucket.key)
        out["volatility"] = bucket.volatility
        out["time_to_sell"] = bucket.time_to_sell
        refs = self._filtered_refs(found, bucket.key, bucket.refs)
        if refs:
            last = refs[-1]
            out["last_sale"] = {"price": last.price, "at": last.at}

    def _closest_median(self, out, item, found, det, extra):
        """GetEstimatedMedian."""
        want = det.key
        want_elems = det.breakdown
        best, best_score = None, None
        for key, bucket in found.buckets.items():
            if not bucket.price:
                continue
            score = self.similarity(want, key, want_elems, self.elems_of(key))
            if best_score is None or score > best_score:
                best, best_score = bucket, score
        if best is None:
            return
        self._assign(out, det, best, extra, found)
        wanted = {e.key for e in want_elems}
        missing = [m for m in best.key.mods if m not in wanted]
        # At the share buyers pay back, the same as _thin. Full price took a 7b Necron Leggings with
        # a recomb, books and Ancient down to 4.2b clean while the boots beside it read 3.9b off 5.5b.
        keep = self.p.retention or {}
        change = sum(self._kind_value(k, v) * keep.get(k, keep.get("other", 1)) for k, v in missing)
        if change:
            base = self.clean_price(item.entry, want.tier)
            if change > out["median"] / 4 * 3:
                ours = base + sum(e.coins for e in want_elems)
                theirs = base + sum(self._kind_value(k, v) for k, v in best.key.mods)
                ratio = min(ours / (theirs + 1), 1.5)
                out["median"] *= ratio
            else:
                out["median"] -= change
        # A cheaper key the item fully contains sets a floor, and parts on top add a little.
        top = None
        for key, bucket in found.buckets.items():
            if bucket.price and is_higher_value(key, want) and (top is None or bucket.price > top.price):
                top = bucket
        if top is not None and top.price > out["median"]:
            out["median"] = top.price
            out["median_key"] = str(top.key)
            have = set(top.key.mods)
            add = sum(e.counted() for e in want_elems if e.key not in have)
            out["median"] += add * self.p.closest_addback

    def _closest_lbin(self, out, item, found, det, extra, exclude):
        """ClosestLbin: nearest key with something listed, adjusted by the difference."""
        best, best_score, rows_of = None, None, None
        for key, bucket in found.buckets.items():
            rows = self._lbin_of(bucket, exclude)
            if not rows:
                continue
            score = self.similarity(det.key, key, det.breakdown, self.elems_of(key)) + min(bucket.volume, 2)
            if best_score is None or score > best_score:
                best, best_score, rows_of = bucket, score, rows
        if best is None:
            return
        price = rows_of[0].price
        wanted = {e.key for e in det.breakdown}
        change = sum(self._kind_value(k, v) for k, v in best.key.mods if (k, v) not in wanted)
        if change and price - change > 0:
            price -= change
        out["lbin"] = price + extra
        out["lbin_key"] = str(best.key)
        out["lbin_live"] = len(rows_of)

    def _lbin_cap(self, found, det, exclude):
        """GetLbinCap: the cheapest listed copy that is at least as good caps the LBIN."""
        best = 0
        for key, bucket in found.buckets.items():
            if key == det.key or not is_higher_value(det.key, key):
                continue
            rows = self._lbin_of(bucket, exclude)
            if rows and (not best or rows[0].price < best):
                best = rows[0].price
        return best

    def _kept(self, key):
        """What buyers pay for the parts on a copy with this key, at the share they really pay back."""
        keep = self.p.retention
        if not keep:
            return 0
        return sum(self._kind_value(kind, value) * keep.get(kind, keep.get("other", 0.5)) for kind, value in key.mods)

    def _cap_at_better(self, out, found, det):
        """A copy at least as good as this one that sold lately for less caps it, once whatever it
        had extra is taken off. A clean Sorrow Helmet read 3.96b when a 3 star one had just gone for
        2.4b, because the sales around it were dressed up. Nobody pays more for less."""
        since = self.today() - CAP_DAYS
        own = self._kept(det.key)
        rows = []
        for key, bucket in found.buckets.items():
            if is_higher_value(det.key, key):
                extra = max(self._kept(key) - own, 0)
                rows.extend(max(r.price - extra, r.price * THIN_FLOOR)
                            for r in self._filtered_refs(found, key, bucket.refs) if r.day >= since)
        if not rows:
            return
        floor = lower_median(rows) * CAP_FLOOR
        cap = min((price for price in rows if price >= floor), default=0)
        if cap and cap < out["median"]:
            out["median"] = cap
            out["stripped"] = True

    def _thin(self, out, item, found, det, extra):
        """Nothing the plain rules would price. Rather than show nothing, take what few sales there
        are, then the parts, and say it is rough. A big market can afford silence here and Dragsim cannot,
        since most items sell only a few times a month."""
        rows = []
        for key, bucket in found.buckets.items():
            kept = self._kept(key)
            refs = amm(self._filtered_refs(found, key, bucket.refs), self.p.working_size)
            rows.extend((r.at, r.price, max(r.price - kept, r.price * THIN_FLOOR))
                        for r in refs if r.day >= self.today() - 30)
        if rows:
            rows.sort(key=lambda r: -r[0])
            # Each sale with what was on it taken off, or a copy that sold for 5.5b with a recomb,
            # books and an Ancient reforge reads as a clean one worth 5.5b. built_value then put
            # the same parts back on top, so they were counted twice.
            out["median"] = lower_median([r[2] for r in rows[:5]]) + extra
            out["median_key"] = "thin"
            out["last_sale"] = {"price": rows[0][1], "at": rows[0][0]}
            out["stripped"] = any(r[1] != r[2] for r in rows[:5])
            return
        if item.entry in self.reported:
            out["median"] = self.reported[item.entry]["value"] + extra
            out["median_key"] = "reported"
            return
        craft = self.craft_cost(item.entry)
        if craft:
            out["median"] = craft + extra
            out["median_key"] = "craft"


THIN_FLOOR = 0.25       # parts can be priced above what a copy sold for, and a sale is never worth nothing
CAP_DAYS = 14           # how far back a better copy's sale still says something about today
CAP_FLOOR = 0.4         # under this share of the usual price it was a favour to a friend, not a price
SPIKE_RATIO = 10        # a jump this large needs confirmation beyond the latest auction
SPIKE_SUPPORT = 3       # amm leaves at most one vote per seller and buyer


def middle(*signals):
    """The middle of what is present, the lower of two. Each signal fails in its own way, a stale
    median, a lone gift, a troll listing, and this needs two of them to agree. On sales the tuning
    never saw it was off by 11.1%, against 17.5-20% for the plain median alone."""
    present = sorted(x for x in signals if x)
    if not present:
        return 0
    return min(present) if len(present) == 2 else present[len(present) // 2]


def is_higher_value(base, check):
    """IsHigherValue: is `check` at least everything `base` is? Used in both directions, to cap
    a price by a better copy and to floor it by a lesser one."""
    if base.tier > check.tier or base.count > check.count:
        return False
    have = dict(check.mods)
    for kind, value in base.mods:
        other = have.get(kind)
        if other is None:
            return False
        if other == value:
            continue
        if kind == "scrolls":
            if set(value.split("+")) <= set(other.split("+")):
                continue
            return False
        try:
            if float(other) > float(value):
                continue
        except ValueError:
            pass
        return False
    return True


def star_residue(stars):
    return sum(range(1, stars + 1))


def book_band(books):
    """NormalizeGroupNumber. Exact counts would scatter too few sales across eleven buckets."""
    return 10 if books >= 10 else 5 if books >= 5 else 1


REFORGE_COINS = {"COMMON": 250_000, "UNCOMMON": 500_000, "RARE": 1_000_000, "EPIC": 4_000_000, "LEGENDARY": 7_500_000,
                 "MYTHIC": 10_000_000}


def reforge_coins(rarity):
    """What the anvil charges. The chat shows -4m, -5m, -7.5m and -10m, and the steps fit rarity
    rather than the reforge's name, since Jaded and Spiritual cost the same on the same tier."""
    names = {RANK[k]: v for k, v in REFORGE_COINS.items()}
    return names.get(rarity, 7_500_000)


def insta_sell(est):
    """SniperClient.InstaSellPrice: what an item goes for today if you want it gone."""
    median, lbin, volume = est["value"] or est["median"], est["lbin"], est["volume"]
    deduct = 0.12
    if median < 15_000_000:
        deduct = 0.18
    if median > 150_000_000:
        deduct = 0.10
    if volume < 1:
        deduct += 0.05
    if volume < 0.15:
        deduct += 0.05
    from_median = median * (1 - deduct)
    target = max(from_median, min(lbin * (1 - deduct - 0.08), from_median * 1.2))
    if est.get("item_key") != est.get("lbin_key"):
        if est.get("median_key") == est.get("item_key") or not lbin:
            target = from_median
        else:
            target = min(from_median, lbin * (1 - deduct - 0.08))
    return target


def deal(est, unit, count=1, min_gap=0.10):
    """Finding a flip in this market. The usual rule (median over bid x 1.05) was a coin flip here,
    the next sale cleared +5% only 36-53% of the time, so the signal is the gap to the next BIN,
    the price you really have to beat. A gap over 4x is a troll listing or a different item.
    Returns None, or what the listing should make after the fee."""
    value, next_bin = est["value"], est["lbin"]
    if not value or not next_bin or unit <= 0 or est["confidence"] == "low":
        return None
    # Under what it sells for too, not only under the next BIN. 18 Warden Catalysts at 111.1m each,
    # their median 111.5m, were flagged +528m (2026-11-15): a 32-stack listed at 156m made the gap,
    # and one stack that sold at 137m made the value. 5% leaves room for the 2.5% fee. Replayed over
    # Nov 4-14 this cut 28% of the alerts, and the share that made money went from 86% to 89%.
    median = est.get("median")
    if median and unit > median * (1 - UNDER_MEDIAN):
        return None
    gap = next_bin / unit - 1
    if gap < min_gap or gap > 3 or value < unit * 1.03:
        return None
    target = min(next_bin - 1, value * 1.05)
    net = fees.net_of_fee(target * count)
    profit = net - unit * count
    if target * count < MIN_TARGET or profit <= 0:
        return None
    return {"target": target, "gap": gap, "profit": profit, "roi": profit / (unit * count), "next_bin": next_bin, "value": value}


def offers(est):
    """LowballCommand's three tiers, measured from what you would really clear: you must undercut
    the cheapest BIN to resell, and the listing fee comes out of that."""
    value = est["value"] or est["median"]
    if not value:
        return None
    resale = value
    if est["lbin"]:
        resale = min(resale, est["lbin"] - 1)
    net = fees.net_of_fee(resale)
    return {"resale": resale, "net": net, "high": net * 0.91, "mid": net * 0.82, "low": net * 0.70}
