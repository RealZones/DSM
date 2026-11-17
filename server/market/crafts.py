"""Craft flips: what a thing really costs to make right now, and what it clears after the fee.

Unit prices cannot just be added up. A recipe has to be walked through real supply, cheapest first, because the cheap copy
runs out: one listing at 2m beside the next at 9m makes the second one 9m, and 50 fragments from a
book holding 12 at the good price is a very different number from 50 x the best offer. That gap is
most of the phantom profit in a plain list.

What is different here, because Dragsim is:

  - The bazaar has 31 products, so most parts come off the auction house. Listings go in as supply
    beside the book, cheapest first, not as a fallback.
  - Nobody publishes bazaar sales, so a buy order's supply is read from the tape. A bid that no one
    sells into adds nothing, which is what stops a 1 coin bid beside 700m offers pricing a craft.
  - Effort is not added to the cost. Marking every step up by 1% would leave rows that do not add
    up to the total. Here the cost is the coins you spend, and effort only decides whether a part is
    worth making rather than buying.
  - What it sells for is the tuned value capped by the BIN you have to beat, less a fee Dragsim
    takes on listing rather than on collecting.
"""
import math

from . import fees
from .items import Item, RANK

MAX_DEPTH = 12
CRAFT_MARGIN = 1.03     # making a part has to clearly beat buying it
FORGE_MARGIN = 1.20     # forge slots are time gated, so a saving has to be worth the wait
ORDER_HOURS = 0.5       # how long a buy order is given to fill
ORDER_MARKUP = 1.20     # the money sits idle meanwhile and the price can walk away
ORDER_MAX = 71_000      # one order's cap, assumed; Dragsim's own limit has not been seen
BAZAAR_FEE = 0.01       # assumed 1%. Dragsim's has not been seen, so this errs against the flip
REPEAT_DISCOUNT = 1.05  # each extra copy competes with the last
SALES_DAYS = 14         # how far back "sells this often" looks
GIFT_SHARE = 0.2        # a BIN this far under value is a friend's listing, not a price to beat
WAIT_DAYS = 7           # how far back a part's sale rate looks
WAIT_SHARE = 0.5        # of one day's sales, what a patient buyer can expect to catch
MAX_COUNT = 64
UNLIMITED = 10 ** 12


class Got:
    """How `count` of one thing would be obtained, and what that spends."""
    __slots__ = ("entry", "count", "cost", "enough", "method", "fills", "parts", "hours", "buy_cost", "coins", "recipe", "made")

    def __init__(self, entry, count, cost, enough, method, fills=(), parts=(), hours=0.0, buy_cost=0, coins=0, recipe=None, made=None):
        self.entry = entry
        self.count = count
        self.cost = cost
        self.enough = enough
        self.method = method
        self.fills = fills          # what is bought; a part made only in part has some as well
        self.parts = parts
        self.hours = hours
        self.buy_cost = buy_cost
        self.coins = coins
        self.recipe = recipe
        self.made = count if made is None and method == "craft" else made   # how many are made, not bought


class Pool:
    """What supply is left for one plan. Every option tried gets its own copy, so two parts of a
    recipe cannot both claim the same cheap listing."""
    __slots__ = ("load", "rows")

    def __init__(self, load, rows=None):
        self.load = load
        self.rows = rows if rows is not None else {}

    def fork(self):
        return Pool(self.load, {entry: [row[:] for row in rows] for entry, rows in self.rows.items()})

    def take(self, entry, amount, under=None):
        """SmartBuyer.Fill. (fills, units nobody is selling). With `under`, only copies that cost less
        than that each, and never a last price, which is a guess and not a copy anyone is selling."""
        rows = self.rows.get(entry)
        if rows is None:
            rows = self.rows[entry] = self.load(entry)
        left = amount
        fills = []
        for row in rows:
            if left <= 0:
                break
            if under is not None and (row[0] >= under or row[2] == "estimate"):
                continue
            have = min(left, row[1])
            if have <= 0:
                continue
            row[1] -= have
            left -= have
            fills.append((row[2], have, row[0], row[3]))
        return fills, left


class Crafts:
    def __init__(self, engine, recipes, shop=None, own=()):
        self.engine = engine
        self.recipes = recipes
        self.shop = shop or {}
        self.own = set(own)     # a player cannot buy their own listing

    def usable(self, entry):
        return [r for r in self.recipes.get(entry) or () if r.get("need") and not r.get("missing")]

    def supply(self, entry):
        """Every place one can be bought, cheapest first."""
        rows = []
        offer = self.shop.get(entry)
        if offer:
            rows.append([offer, UNLIMITED, "shop", None])
        bazaar = self.engine.bazaar
        book = bazaar.books.get(entry) if bazaar else None
        if book:
            if book.last:
                rows.extend([price, amount, "bazaar", None] for price, amount in book.last[3].items() if amount > 0)
            # Priced at what sellers really took. A 5 coin bid beside 20k offers looks like a way
            # to buy for 7 coins, but the people selling were selling at 20k, and a Refined End
            # Stone made that way came out at 115 coins to sell for 14m.
            flow = min(bazaar.sold_per_hour(entry) * ORDER_HOURS, ORDER_MAX)
            if book.bid and flow >= 1:
                rows.append([max(book.bid + 1, bazaar.sold_price(entry)) * ORDER_MARKUP, flow, "order", None])
        found = self.engine.lookups.get(entry)
        if found:
            now = self.engine.now
            for bucket in found.buckets.values():
                rows.extend([live.price, live.count, "ah", live.seller] for live in bucket.lbins
                            if live.end > now and live.seller not in self.own)
            # What is listed is only what nobody wanted yet. Frozen Fragments sell at 70m, and the
            # ones at 70m go as soon as they appear, so the listings standing at any moment start
            # at 74m and run to 90m. Walking those priced 100 of them at 85m each, when 140 sell
            # a day at 70m. A part with real flow can be bought at its price by waiting a day.
            value = self.engine.market_price(entry)
            flow = self.units_per_day(found) * WAIT_SHARE
            if value and flow >= 1:
                rows.append([value, flow, "wait", None])
        rows.sort(key=lambda r: r[0])
        return rows

    def units_per_day(self, found):
        since = self.engine.today() - WAIT_DAYS
        return sum(r.count for bucket in found.buckets.values() for r in bucket.refs if r.day >= since) / WAIT_DAYS

    def guess(self, entry):
        """Supply, plus anything nobody is selling this minute at what it last went for. 132 of
        283 recipes have some part with no listing at any given moment, usually one fragment, and
        without this every one of them vanished from the board, Draconic Talisman included."""
        rows = self.supply(entry)
        value = self.last_price(entry)
        if value:
            rows.append([value, UNLIMITED, "estimate", None])
            rows.sort(key=lambda r: r[0])
        return rows

    def last_price(self, entry):
        value = self.engine.price_of(entry)[0]
        if value:
            return value
        found = self.engine.lookups.get(entry)
        refs = [r for bucket in found.buckets.values() for r in bucket.refs] if found else ()
        return max(refs, key=lambda r: r.at).price if refs else 0

    def obtain(self, entry, count, pool, depth=0, stack=frozenset()):
        """Buy it or make it, whichever costs less at this quantity. The pool is left
        holding what the chosen way used."""
        # No positive-cost craft should consume its own output via a conversion cycle.
        # It also makes 'craft Hyperion' avoid buying Hyperion to turn it away and back.
        if entry in stack:
            return Got(entry, count, 0, False, "buy")
        buying = pool.fork()
        fills, unmet = buying.take(entry, count)
        cost = sum(n * unit for _, n, unit, _ in fills)
        best = Got(entry, count, cost, not unmet, "buy", fills, buy_cost=0 if unmet else cost)
        chosen = buying
        # A last price is a guess at something nobody has for sale, so it never beats a way to
        # really get one. "Buy a Wilted Rose Ring" is useless when none exist on the AH.
        for_sale = not any(fill[0] == "estimate" for fill in fills)
        crafted = None
        if depth < MAX_DEPTH and entry not in stack:
            for recipe in self.usable(entry):
                made, used = self.make(entry, recipe, count, pool, depth, stack)
                if not made.enough:
                    continue
                if crafted is None or made.cost < crafted.cost:
                    crafted = made
                margin = FORGE_MARGIN if made.hours else CRAFT_MARGIN
                if not best.enough or not for_sale or made.cost * margin < best.cost:
                    made.buy_cost = best.buy_cost if for_sale else 0
                    best, chosen, for_sale = made, used, True
            if crafted is not None and count > 1:
                best, chosen = self.hybrid(entry, count, pool, depth, stack, crafted, best, chosen)
        pool.rows = chosen.rows
        return best

    def hybrid(self, entry, count, pool, depth, stack, crafted, best, chosen):
        """RealisticCraft's hybrid: buy the copies that cost less than making one, make the rest.
        Choosing all-buy or all-make missed it on 2026-11-09: Obsidian Drill OD-555 needs 23 Refined
        Crying Obsidian, the bazaar had one at 60m, and forging one takes 160m of Enchanted Crying
        Obsidian, so the plan forged all 23. Kept only when it scores better with the same margins
        the plain choice uses."""
        trial = pool.fork()
        cheap, left = trial.take(entry, count, under=crafted.cost / count)
        if not cheap or not left:
            return best, chosen
        rest, used = self.make(entry, crafted.recipe, left, trial, depth, stack)
        if not rest.enough:
            return best, chosen
        bought = sum(n * unit for _, n, unit, _ in cheap)
        score = lambda got: got.cost * (FORGE_MARGIN if got.hours else CRAFT_MARGIN) if got.method == "craft" else got.cost
        if bought + score(rest) >= score(best):
            return best, chosen
        mixed = Got(entry, count, bought + rest.cost, True, "craft", cheap, rest.parts, rest.hours, buy_cost=best.buy_cost,
                    coins=rest.coins, recipe=crafted.recipe, made=left)
        return mixed, used

    def make(self, entry, recipe, count, pool, depth=0, stack=frozenset()):
        """One recipe, run as many times as `count` needs. Returns the plan and the pool it used."""
        batches = math.ceil(count / (recipe.get("yield") or 1))
        trial = pool.fork()
        coins = (recipe.get("coins") or 0) * batches
        cost = coins
        hours = (recipe.get("hours") or 0) * batches
        enough = True
        parts = []
        for need, amount in recipe["need"].items():
            got = self.obtain(need, amount * batches, trial, depth + 1, stack | {entry})
            parts.append(got)
            cost += got.cost
            hours += got.hours
            enough = enough and got.enough
        return Got(entry, count, cost, enough, "craft", (), parts, hours, coins=coins, recipe=recipe), trial

    def cheapest(self, entry, count=1, load=None):
        best = None
        for recipe in self.usable(entry):
            made, _ = self.make(entry, recipe, count, Pool(load or self.supply))
            if made.enough and (best is None or made.cost < best.cost):
                best = made
        return best

    def sale(self, entry):
        """Where one sells best and for how much. The bazaar's figure is what really traded,
        never its offers: an offer nobody takes is not a price.

        Capping this at the median plus 10% does not work. The median is already capped at the craft
        cost, and here that made the sale depend on the cost it was being compared
        with: Personal Compactor 4000 sold at 69m all day and the board said 19m. The BIN to beat
        is the real ceiling, so that is what limits it. There is no markup on top: a crafted copy
        is clean, so its price is what a clean one fetches, the value and no more."""
        out = []
        engine = self.engine
        bazaar = engine.bazaar
        book = bazaar.books.get(entry) if bazaar else None
        if book and book.ask:
            real = bazaar.price(entry)
            if real:
                out.append(dict(via="bazaar", price=min(real, book.ask - 1), median=real, lbin=book.ask, volume=bazaar.volume(entry),
                                time_to_sell=0, confidence="high", exact=True))
        if entry in engine.lookups:     # the auction house has to have seen one to have a price for it
            info = engine.catalog.items.get(entry) or {}
            probe = Item(entry)
            probe.rarity = RANK.get(info.get("tier") or "NONE", 0)
            probe.name = engine.catalog.name(entry)
            est = engine.estimate(probe)
            if est["value"]:
                target = est["value"]
                lbin = est["lbin"]
                if lbin and lbin < est["value"] * GIFT_SHARE:
                    lbin = est["slbin"]     # a 1 coin listing read as selling a Void Talisman for nothing
                if lbin:
                    target = min(target, lbin - 1)
                out.append(dict(via="ah", price=target, median=est["median"], lbin=lbin, volume=self.sold_per_day(probe),
                                time_to_sell=est["time_to_sell"], confidence=est["confidence"], exact=est["exact"]))
        return max(out, key=lambda s: proceeds(s, 1)) if out else None

    def sold_per_day(self, item):
        """Sales a day of this exact copy lately. A bucket's own volume is only worked out when a
        sale lands, so two sales a day apart last month still read as 1 a day. That made dead
        items look busier than live ones, and the thin crafts that lost money all read 1.00."""
        engine = self.engine
        found = engine.lookups.get(item.entry)
        bucket = found.buckets.get(engine.detailed_key(item).key) if found else None
        if not bucket:
            return 0.0
        since = engine.today() - SALES_DAYS
        return sum(1 for r in bucket.refs if r.day >= since) / SALES_DAYS

    def flip(self, entry, count=1):
        """The whole picture for one output, profitable or not."""
        plan = self.cheapest(entry, count)
        estimated = plan is None
        if estimated:
            # A last price stands in only for what nothing real covers. Giving every item one let the
            # planner build the OD-555 inside OD-655 from 614,400 Obsidian at 400 coins that nobody
            # sells: 2.85b for a drill whose own page said 6.9b, shown at +2.1b profit. Only Amethyst
            # Mixture was missing, and with just that guessed it costs 11.5b to make.
            missing = set(self.short(entry, count))
            if missing:
                plan = self.cheapest(entry, count, lambda e: self.guess(e) if e in missing else self.supply(e))
            if plan is None:
                plan = self.cheapest(entry, count, self.guess)
        if plan is None:
            return None
        sale = self.sale(entry)
        out = dict(entry=entry, count=count, plan=plan, cost=plan.cost, hours=plan.hours, kind=plan.recipe.get("kind") or "craft", estimated=estimated,
                   multi_step=any(part.method == "craft" for part in plan.parts), sale=sale, net=0, profit=0, roi=0, per_hour=0)
        if sale:
            out["net"] = proceeds(sale, count)
            out["profit"] = out["net"] - plan.cost
            out["roi"] = out["profit"] / plan.cost if plan.cost else 0
            out["per_hour"] = out["profit"] / plan.hours if plan.hours else 0
        return out

    def board(self):
        return [row for row in (self.flip(entry) for entry in self.recipes) if row]

    def short(self, entry, count=1):
        """What stops it being made right now, as entries nobody has for sale."""
        for recipe in self.usable(entry):
            made, _ = self.make(entry, recipe, count, Pool(self.supply))
            return gaps(made)
        return []


def gaps(got):
    if got.enough:
        return []
    return [entry for part in got.parts for entry in gaps(part)] if got.parts else [got.entry]


def tested(row):
    """What the backtest could stand behind. 8 of 12 of these made money, median +5%, which came to
    about break-even overall. Of the 170 rough ones under 1 in 3 made money and most never sold.
    An earlier run said 18 of 19, which was listings that had already sold counted as cheap parts."""
    sale = row["sale"]
    return bool(sale) and sale["confidence"] != "low" and sale["volume"] > 0 and not row["estimated"]


def proceeds(sale, count):
    """What selling `count` clears. Copies compete on the AH, so each goes a little lower."""
    if sale["via"] == "bazaar":
        return sale["price"] * count * (1 - BAZAAR_FEE)
    return sum(fees.net_of_fee(sale["price"] / REPEAT_DISCOUNT ** rank) for rank in range(count))
