"""Turns buys, sells and trades into flips, with where every coin went.

A sale is paired with its purchase, and the difference is explained as a list of labelled amounts.
Two things shape it, because of how Dragsim works:

  - No item has an id, and the feed strips the one thing that could serve as one. So a purchase
    is found by what the item is, and only a recent one counts (FLIP_WINDOW). Exact copies match
    first, then copies that only gained upgrades, then anything of that kind, oldest first, and
    the flip says how sure it is. A sale with no recent purchase is the player's own item.
  - A player's stock is modelled as lots with a cost basis, because trades, loans and gifts move
    items around without any auction. It is what lets a dye bought in a trade, applied to a
    helmet and sold on the AH land on one flip, at what the dye really cost.

Everything else on the site and in the mod reads the Flip records this produces.
"""
import collections
import itertools
import re

from . import fees
from . import engine as E
from .items import from_json, from_receipt, plain

DAY = 86_400_000
WINDOW_EXACT = 30 * DAY         # how far back a bought part or ingredient still sets a cost
WINDOW_CHANGED = 14 * DAY       # the same for a craft's ingredients and the lines saying it was made
# A sale is a flip only if the item went up for sale within this long of being bought. No item has
# an id, so an older purchase "matched" any later sale of that kind: a drop sold today was paired
# with a copy bought two weeks ago to use, and showed as a profit or a loss (Fear, 2026-11-16).
# Over every player's recorded sales, items listed within 3 days of the purchase netted +20b
# (1-3 days alone: +0.1b) and those listed later -67b, with twice the share losing over 30%:
# used gear and other copies, not flips. The windows were 30 and 14 days.
FLIP_WINDOW = 3 * DAY
TRANSFER_SHARE = 0.05           # under this share of value a sale is a gift, not a price
LOAN_RETURN = 14 * DAY
DEFAULT_GEM_COINS = 2_300_000   # the going rate in his two 1,000 gem trades, until a newer one


REFORGE_LINE = re.compile(r"You applied the ([A-Za-z' ]+) reforge to your item! \(-([\d,]+) coins\)")
MADE_LINE = re.compile(r"Supercrafted ([^.]+)\.|Collected (.+?) from the Forge")
OWNED_SHARE = 0.5               # how much of a recipe has to be bought before a sale looks like a craft
PROVEN_SHARE = 0.10             # the same once the game itself said it was made


class Lot:
    """Units of one item bought together, and what they cost each."""
    __slots__ = ("item", "count", "unit", "at", "via", "other", "ref", "flags", "value", "target", "finder")

    def __init__(self, item, count, unit, at, via, other, ref, flags=(), value=0, target=0, finder=None):
        self.item = item
        self.count = count
        self.unit = unit
        self.at = at
        self.via = via
        self.other = other
        self.ref = ref
        self.flags = set(flags)
        self.value = value
        self.target = target
        self.finder = finder


class Stock:
    def __init__(self):
        self.lots = collections.defaultdict(list)       # entry -> [Lot], oldest first
        self.failed = collections.defaultdict(list)     # identity -> [(fee, at, created)] listings that never sold
        self.lent = collections.defaultdict(list)       # partner -> [(item, count, lots, at)]
        self.reforges = []                              # [at, reforge, coins, used], from chat
        self.made = collections.defaultdict(list)       # entry -> [[at, used]], from chat


def window_items(detail):
    """What the mod saw in the trade window, by name. Empty for trades read back from logs."""
    out = collections.defaultdict(list)
    for row in (detail or {}).get("items") or ():
        if row.get("item"):
            out[plain(row.get("name"))].append(row["item"])
    return out


def same_copy(a, b):
    """Interchangeable? A receipt only shows the name, stars and colour, so a loose test there."""
    if a.applied_at and b.applied_at:
        return a.applied_at == b.applied_at     # the game's own stamp, as good as an id
    if a.partial or b.partial:
        return a.entry == b.entry and a.reforge == b.reforge and a.stars == b.stars
    return a.identity() == b.identity()


def only_gained(buy, sell):
    if buy.entry != sell.entry:
        return False
    if sell.stars < buy.stars or (buy.recomb and not sell.recomb):
        return False
    if buy.partial:
        return True
    for key in ("hpb", "ghpb", "sync", "aow"):
        if getattr(sell, key) < getattr(buy, key):
            return False
    for key in ("skin", "dye", "enrich"):
        if getattr(buy, key) and getattr(sell, key) != getattr(buy, key):
            return False
    return True


class Tracker:
    def __init__(self, engine, tracked=()):
        self.engine = engine
        self.tracked = set(tracked)
        self.stocks = collections.defaultdict(Stock)
        self.flips = []
        self.sales_without_buy = []
        self.trades = []
        self.gem_coins = DEFAULT_GEM_COINS
        self._ids = itertools.count(1)

    # ------------------------------------------------------------------ taking stock out

    def _take(self, stock, item, count, before, attempts=()):
        """Removes up to `count` units and returns [(lot, units, rank)]. The ranks are what the
        flip's flags are built from, so a guess never looks like a certainty. `attempts` are
        when earlier listings of this item that never sold were put up: a flip listed the day
        it was bought and relisted a week later is still a flip."""
        lots = stock.lots.get(item.entry)
        if not lots:
            return []
        wanted = count
        taken = []

        def recent(lot):
            listed = min((a for a in attempts if lot.at <= a <= before), default=before)
            return listed - lot.at <= FLIP_WINDOW

        ranks = (
            (0, lambda lot: same_copy(lot.item, item) and recent(lot)),
            (1, lambda lot: only_gained(lot.item, item) and recent(lot)),
            (2, recent),
        )
        for rank, test in ranks:
            for lot in lots:
                if wanted <= 0:
                    break
                if lot.count <= 0 or lot.at > before or not test(lot):
                    continue
                units = min(lot.count, wanted)
                lot.count -= units
                wanted -= units
                taken.append((lot, units, rank))
        stock.lots[item.entry] = [lot for lot in lots if lot.count > 0]
        return taken

    def _part(self, stock, entry, amount, at, label):
        """One part put on an item. If this player bought it, that is what it cost them, not
        today's price, and it leaves their stock. Without the stock model this could only use the
        market price."""
        if not entry or amount <= 0:
            return None
        left = amount
        paid = 0.0
        used = []
        for lot in stock.lots.get(entry, ()):
            if left <= 0:
                break
            if lot.count <= 0 or lot.at > at or lot.at < at - WINDOW_EXACT:
                continue
            units = min(lot.count, left)
            lot.count -= units
            left -= units
            paid += units * lot.unit
            used.append(lot)
        if entry in stock.lots:
            stock.lots[entry] = [lot for lot in stock.lots[entry] if lot.count > 0]
        market = self.engine.price_of(entry)[0]
        total = paid + left * market
        if not total:
            return None
        source = "bought" if used and not left else "fixed" if entry in self.engine.shop else "market"
        return {"label": label, "amount": -int(total), "at": at, "source": source}

    @staticmethod
    def _reforge_paid(stock, name, since, at):
        """What the anvil really charged, read from the line it prints. Each line pays for one
        reforge only, so two items reforged the same way cannot share it."""
        for row in stock.reforges:
            if not row[3] and row[1] == name and since <= row[0] <= at:
                row[3] = True
                return row[2]
        return 0

    def upgrade_changes(self, stock, buy, sell, at, since=0):
        """What changed on the item between buy and sale, with Dragsim's parts. A receipt cannot show books
        or dyes, so only what it can show is compared when the buy came from one."""
        changes = []

        def add(change):
            if change:
                changes.append(change)

        if sell.recomb and not buy.recomb:
            add(self._part(stock, "recombobulator_3000", 1, at, "Recombobulator"))
        if sell.stars > buy.stars:
            residue = E.star_residue(sell.stars) - E.star_residue(buy.stars)
            add(self._part(stock, "ritual_residue", residue, at, f"{sell.stars - buy.stars} stars ({residue} residue)"))
        if sell.reforge and sell.reforge != buy.reforge:
            stone = self.engine.catalog.reforge_stone.get(sell.reforge)
            add(self._part(stock, stone, 1, at, f"{sell.reforge.title()} stone"))
            paid = self._reforge_paid(stock, sell.reforge, since, at)
            changes.append({"label": f"{sell.reforge.title()} reforge fee", "amount": -(paid or E.reforge_coins(sell.rarity)), "at": at,
                            "source": "bought" if paid else "fixed"})
        if not buy.partial:
            catalog = self.engine.catalog
            if sell.hpb > buy.hpb:
                add(self._part(stock, "hot_potato_book", sell.hpb - buy.hpb, at, f"{sell.hpb - buy.hpb} Hot Potato Books"))
            if sell.ghpb > buy.ghpb:
                add(self._part(stock, "golden_hot_potato_book", sell.ghpb - buy.ghpb, at, f"{sell.ghpb - buy.ghpb} Golden Hot Potato Books"))
            if sell.aow > buy.aow:
                add(self._part(stock, "art_of_war", sell.aow - buy.aow, at, "Art of War"))
            if sell.sync > buy.sync:
                add(self._part(stock, "final_synchronizer", sell.sync - buy.sync, at, f"{sell.sync - buy.sync} Final Synchronizers"))
            if sell.enrich and sell.enrich != buy.enrich:
                add(self._part(stock, "magic_find_enrichment", 1, at, "Enrichment"))
            if sell.dye and sell.dye != buy.dye:
                entry = catalog.dye_entry(sell.dye)
                add(self._part(stock, entry, 1, at, catalog.name(entry)))
            if sell.skin and sell.skin != buy.skin:
                entry = catalog.skin_entry(sell.skin)
                add(self._part(stock, entry, 1, at, catalog.name(entry)))
            if sell.pet_skin and sell.pet_skin != buy.pet_skin:
                add(self._part(stock, sell.pet_skin, 1, at, catalog.name(sell.pet_skin)))
            for attr in ("pet_item", "engine", "tank", "module"):
                before, after = getattr(buy, attr), getattr(sell, attr)
                if after and after != before:
                    add(self._part(stock, after, 1, at, catalog.name(after)))
                if before and before != after:
                    # Taken off and kept, so it is worth something.
                    worth = self.engine.price_of(before)[0]
                    if worth:
                        changes.append({"label": f"Removed {catalog.name(before)}", "amount": int(worth), "at": at, "source": "market"})
        return changes

    def _craft(self, stock, item, count, before, player):
        """No direct purchase: was it made from things this player bought? A recipe counts only
        if bought ingredients carry at least half its value, or any one common material would
        turn every sale into a 'craft flip'. The game saying it was made lowers that bar, but not
        to nothing: most forge lines are bulk forging from farmed materials, and pricing those
        at market would invent a profit nobody paid for."""
        recipes = self.engine.recipes.get(item.entry) or ()
        lines = [r for r in stock.made.get(item.entry, ()) if not r[1] and before - WINDOW_CHANGED <= r[0] <= before]
        best = None
        for recipe in recipes:
            need = recipe.get("need") or {}
            if not need or recipe.get("missing"):
                continue
            total = owned = 0.0
            for entry, amount in need.items():
                price = self.engine.price_of(entry)[0]
                have = sum(l.count for l in stock.lots.get(entry, ()) if before - WINDOW_CHANGED <= l.at <= before)
                if lines and entry in self.engine.shop:
                    have = amount       # a shop line has no receipt to read, and costs what it says
                total += price * amount
                owned += price * min(have, amount)
            if total and owned / total >= (PROVEN_SHARE if lines else OWNED_SHARE) and (best is None or owned / total > best[1]):
                best = (recipe, owned / total)
        if best is None:
            return None
        for row in lines[-count:]:
            row[1] = True
        recipe = best[0]
        changes = []
        cost = 0.0
        sources = []
        for entry, amount in recipe["need"].items():
            amount = amount * count / (recipe.get("yield") or 1)
            change = self._part(stock, entry, amount, before, f"{amount:g}x {self.engine.catalog.name(entry)}")
            if change:
                changes.append(change)
                cost -= change["amount"]
                sources.append(change["source"])
        if recipe.get("coins"):
            changes.append({"label": "Craft coins", "amount": -int(recipe["coins"] * count), "at": before, "source": "fixed"})
        return changes, sources

    # ------------------------------------------------------------------ a disposal becomes a flip

    def _close(self, player, item, count, proceeds, at, via, other, ref, fee=0, created=None, flags=()):
        stock = self.stocks[player]
        before = created if created else at
        used = self._take(stock, item, count, before, [f[2] for f in stock.failed.get(item.identity(), ()) if f[2]])
        flags = set(flags)
        changes = []
        if not used:
            made = self._craft(stock, item, count, before, player)
            if made is None:
                self.sales_without_buy.append(dict(player=player, entry=item.entry, name=item.label(), count=count, at=at,
                                                   via=via, price=int(proceeds), other=other))
                return None
            changes, sources = made
            flags.add("craft")
            if "market" in sources:
                flags.add("uncertain_cost")
            cost = 0.0
            first = None
        else:
            matched = sum(u for _, u, _ in used)
            if matched < count:
                proceeds = proceeds * matched / count
                fee = fee * matched / count
                count = matched
            cost = sum(lot.unit * units for lot, units, _ in used)
            worst = max(rank for _, _, rank in used)
            if worst:
                flags.add("changed")
            for lot, units, _ in used:
                flags |= lot.flags
            seen = set()
            for lot, units, rank in used:
                if rank and id(lot.item) not in seen:
                    seen.add(id(lot.item))
                    changes.extend(self.upgrade_changes(stock, lot.item, item, before, lot.at))
            first = used[0][0]
            if first.via == "trade":
                flags.add("via_trade")
            if first.other and first.other == other:
                flags.add("same_partner")
        if fee:
            changes.append({"label": "Listing fee", "amount": -int(fee), "at": before, "source": "fixed"})
        failed = stock.failed.pop(item.identity(), None)
        if failed:
            spent = sum(f[0] for f in failed)
            changes.append({"label": f"{len(failed)} listing{'s' if len(failed) > 1 else ''} that did not sell", "amount": -int(spent),
                            "at": failed[-1][1], "source": "fixed"})
        merged = collections.OrderedDict()
        for c in changes:
            if c["label"] in merged:
                merged[c["label"]]["amount"] += c["amount"]
            else:
                merged[c["label"]] = dict(c)
        changes = list(merged.values())
        profit = proceeds - cost + sum(c["amount"] for c in changes)
        est = self.engine.lookups.get(item.entry)
        flip = {
            "id": next(self._ids), "player": player, "entry": item.entry, "name": item.label(), "count": count,
            "rarity": item.rarity,
            "buy": {"via": first.via if first else "craft", "price": int(cost), "at": first.at if first else None,
                    "from": first.other if first else None, "ref": first.ref if first else None,
                    "value": int(first.value) if first else 0, "target": int(first.target) if first else 0,
                    "finder": first.finder if first else None},
            "sell": {"via": via, "price": int(proceeds), "at": at, "to": other, "ref": ref},
            "changes": changes, "profit": int(profit), "flags": sorted(flags),
            "hold": at - first.at if first else None,
        }
        spent = cost - sum(c["amount"] for c in changes if c["amount"] < 0 and c["label"] != "Listing fee")
        flip["roi"] = round(profit / spent, 4) if spent > 0 else None
        self.flips.append(flip)
        return flip

    # ------------------------------------------------------------------ events

    def on_unlisted(self, player, item, price, at, end, created):
        """A listing that ended unsold still paid its fee, which belongs to the next sale of
        that item. Dragsim charges only up front."""
        hours = max((end - created) / 3_600_000, 1) if end and created else fees.DEFAULT_HOURS
        self.stocks[player].failed[item.identity()].append((fees.listing_fee(price, hours), at, created))

    def on_sale(self, sid, item, unit, count, seller, buyer, at, created, end, est):
        value = est["value"] if est else 0
        floor = max(value * TRANSFER_SHARE if est and est.get("confidence") != "low" else 0, item.npc or 0)
        transfer = bool(floor) and unit < floor
        price = unit * count
        hours = max((end - created) / 3_600_000, 1) if end and created else fees.DEFAULT_HOURS
        fee = fees.listing_fee(price, hours)
        flip = None
        if transfer:
            # A 1m Reaper Scythe is a gift through the AH. It ends the seller's stake and starts
            # the buyer's with no real cost, so neither side gets a profit out of it.
            self._take(self.stocks[seller], item, count, created or at)
        else:
            flip = self._close(seller, item, count, price, at, "ah", buyer, sid, fee, created)
        flags = ("unknown_cost",) if transfer else ()
        finder, target = None, 0
        if est and not transfer:
            found = E.deal(est, unit, count)
            if found:
                finder, target = "gap", found["target"]
            elif value:
                target = value
        self.stocks[buyer].lots[item.entry].append(
            Lot(item, count, unit, at, "ah", seller, sid, flags, value, target, finder))
        return flip

    def on_chat(self, player, at, text):
        """A line the mod kept. The anvil's receipt and the lines saying something was made change
        numbers today. The rest are stored so later rules can read them without needing them to
        happen again."""
        line = plain(text)
        m = REFORGE_LINE.search(line)
        if m:
            self.stocks[player].reforges.append([at, m.group(1).lower(), int(m.group(2).replace(",", "")), False])
        # One message can carry two of these, Soulflow Battery and Supercell in the same breath.
        for m in MADE_LINE.finditer(line):
            entry = self.engine.catalog.names.get((m.group(1) or m.group(2)).strip())
            if entry:
                self.stocks[player].made[entry].append([at, False])

    def on_trade(self, trade):
        """A receipt, from a log or live from the mod. Money and items are split out, then the
        shape of the deal decides what it was."""
        player, other, at = trade["player"], trade["other"], trade["at"]
        catalog = self.engine.catalog
        coins_in = coins_out = gems_in = gems_out = 0
        got, gave = [], []
        window = window_items(trade.get("detail"))
        for side, lines in (("got", trade.get("got") or ()), ("gave", trade.get("gave") or ())):
            for line in lines:
                kind, amount, item = from_receipt(line, catalog)
                # The window held the real item, books, dyes and all. A receipt only names it.
                seen = window.get(item.label()) if item else None
                if seen:
                    item = from_json(seen.pop(0), catalog)
                    amount = item.count
                if kind == "coins":
                    if side == "got":
                        coins_in += amount
                    else:
                        coins_out += amount
                elif kind == "gems":
                    if side == "got":
                        gems_in += amount
                    else:
                        gems_out += amount
                else:
                    (got if side == "got" else gave).append((item, amount))
        if gems_in and coins_out and not got and not gave:
            self.gem_coins = coins_out / gems_in
        if gems_out and coins_in and not got and not gave:
            self.gem_coins = coins_in / gems_out
        paid = coins_out + gems_out * self.gem_coins
        earned = coins_in + gems_in * self.gem_coins
        record = dict(player=player, other=other, at=at, coins_in=coins_in, coins_out=coins_out, gems_in=gems_in, gems_out=gems_out,
                      got=[(i.label(), n) for i, n in got], gave=[(i.label(), n) for i, n in gave], kind=None, flips=[])
        self.trades.append(record)
        stock = self.stocks[player]
        worth = lambda rows: [max(self.engine.estimate(i)["value"], 0) for i, _ in rows]

        # Everything moved one way, or no money moved at all. Nothing was priced, so nothing was
        # sold: on 10-07 he handed DontSkipBlaze a Golden Dragon and 15b, and 40 minutes later got
        # the pet and 14.7b back. A friend returning a Hollowjack set while taking the Giant's Sword is
        # the same thing, two loans in one window. Pricing either would invent a flip.
        one_way_out = gave and not got and not earned
        one_way_in = got and not gave and not paid
        if one_way_out or one_way_in or (got and gave and not paid and not earned):
            for item, count in gave:
                stock.lent[other].append((item, count, self._take(stock, item, count, at), at))
            back = self._returned(stock, other, got, at) if got else None
            record["kind"] = back or ("gift in" if got and not gave else "lent or gave" if gave and not got else "swapped")
            return record
        if got and not gave:
            record["kind"] = "bought"
            self._buy(stock, got, worth(got), paid, at, other, trade.get("ref"))
            return record
        if gave and not got:
            record["kind"] = "sold"
            record["flips"] = self._sell(player, gave, worth(gave), earned, at, other, trade.get("ref"))
            return record
        if got and gave:
            # Items both ways with money on top. No price was named for either side, so the market
            # values them and the coins settle the difference. Marked, because it is an estimate.
            record["kind"] = "part exchange"
            give_values, take_values = worth(gave), worth(got)
            given = sum(v * n for v, (_, n) in zip(give_values, gave))
            record["flips"] = self._sell(player, gave, give_values, given, at, other, trade.get("ref"), flags=("uncertain_cost",))
            self._buy(stock, got, take_values, max(given + paid - earned, 0), at, other, trade.get("ref"), flags=("uncertain_cost",))
            return record
        record["kind"] = "coins"
        return record

    def _returned(self, stock, partner, got, at):
        lent = stock.lent.get(partner)
        hit = False
        for item, count in got:
            match = next((row for row in (lent or ()) if same_copy(row[0], item) and at - row[3] <= LOAN_RETURN), None)
            if match:
                hit = True
                lent.remove(match)
                for lot, units, _ in match[2]:
                    lot.count = units
                    stock.lots[lot.item.entry].append(lot)
            else:
                stock.lots[item.entry].append(Lot(item, count, 0, at, "gift", partner, None, ("unknown_cost",)))
        return "returned" if hit else None

    def _shares(self, rows, values, total):
        """Cost share: split a trade's money by what each kind is worth. One kind needs no split.
        Several kinds where one has no price cannot be split at all (Fear, 2026-11-16: "we dont
        know how they valued each"), so the units are an even placeholder and the second value
        says the cost is unknown, which keeps those flips out of every total."""
        if len(rows) == 1:
            return [total / rows[0][1]], False
        weights = [v * n for v, (_, n) in zip(values, rows)]
        if sum(weights) <= 0 or not all(weights):
            even = total / sum(n for _, n in rows)
            return [even] * len(rows), True
        scale = total / sum(weights)
        return [v * scale for v in values], False

    def _flags(self, rows, flags, unknown):
        base = set(flags)
        if len(rows) > 1:
            base.add("multi_item_trade")
        if unknown:
            base.add("unknown_cost")
        return base

    def _buy(self, stock, rows, values, money, at, other, ref, flags=()):
        units, unknown = self._shares(rows, values, money)
        base = self._flags(rows, flags, unknown)
        for (item, count), unit, value in zip(rows, units, values):
            stock.lots[item.entry].append(Lot(item, count, unit, at, "trade", other, ref, base, value, value))

    def _sell(self, player, rows, values, money, at, other, ref, flags=()):
        units, unknown = self._shares(rows, values, money)
        base = self._flags(rows, flags, unknown)
        out = []
        for (item, count), unit in zip(rows, units):
            flip = self._close(player, item, count, unit * count, at, "trade", other, ref, flags=base)
            if flip:
                out.append(flip["id"])
        return out

    # ------------------------------------------------------------------ reading it back

    def positions(self, player):
        """What is still held, against what it is worth now."""
        out = []
        for entry, lots in self.stocks[player].lots.items():
            for lot in lots:
                if lot.count <= 0:
                    continue
                est = self.engine.estimate(lot.item)
                value = est["value"] or est["median"]
                out.append({"entry": entry, "name": lot.item.label(), "count": lot.count, "unit": int(lot.unit), "at": lot.at,
                            "via": lot.via, "from": lot.other, "value": int(value), "confidence": est["confidence"],
                            "gain": int((value - lot.unit) * lot.count) if value else None, "flags": sorted(lot.flags)})
        out.sort(key=lambda r: -r["unit"] * r["count"])
        return out

    @staticmethod
    def counted(flip):
        """Placeholder profits stay out of a total."""
        return "unknown_cost" not in flip["flags"]

    def summary(self, player=None, since=0):
        rows = [f for f in self.flips if (player is None or f["player"] == player) and f["sell"]["at"] >= since and self.counted(f)]
        wins = [f for f in rows if f["profit"] > 0]
        best = max(rows, key=lambda f: f["profit"], default=None)
        return {"flips": len(rows), "profit": sum(f["profit"] for f in rows), "wins": len(wins), "spent": sum(f["buy"]["price"] for f in rows),
                "volume": sum(f["sell"]["price"] for f in rows), "best": best}

    def leaderboard(self, since=0, limit=50):
        totals = collections.defaultdict(lambda: [0, 0, 0])
        for f in self.flips:
            if f["sell"]["at"] >= since and self.counted(f):
                row = totals[f["player"]]
                row[0] += f["profit"]
                row[1] += 1
                row[2] += f["sell"]["price"]
        ranked = sorted(totals.items(), key=lambda kv: -kv[1][0])[:limit]
        return [{"player": p, "profit": r[0], "flips": r[1], "volume": r[2]} for p, r in ranked]
