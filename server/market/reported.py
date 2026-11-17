"""Prices for what never reaches the auction house, read off trade receipts players post.

Skins, runes and most dyes change hands in trades. Of the 33 items on the first 15 reports, 12 had
ever sold on the AH, and most of those only once, so the engine had little or nothing to go on and
a player holding 100b of them scanned as nearly empty. The published catalog leaves most of them
out too, which is why tools/harvest_items.py exists. Without real ids a receipt's "Aurora Dye" looks
like it has never sold, when the AH has nine sales of it under dye_aurora.

A receipt gives one number for everything in it, so a price comes from:

  said     someone stated it outright, like "40b for twarden".
  alone    the item went for coins by itself, or everything else in the trade had a price.

A bundle of several unpriced items never sets a price on its own (Fear, 2026-11-16: wait for single
sales). Nobody can tell how the traders valued each skin, and splitting by a guess put 12b on a
Baby Skin and a Glacial Divan skin from one trade each. Once every item in a bundle has a single
price, the bundle is one more vote for each, its money split in proportion to those single prices.

None of this is a sale on the open market, so it stays low confidence. Nothing here can raise a
deal alert, and it never shows a win rate, because those were measured on AH sales.

Everything was dearer in October. A trader said prices had dropped hard on Nov 2 and the two trades
since went under what the same items fetched before, so the price shown is October's times that
ratio. It is two trades, one of them called a bad trade, which is why the page shows both numbers.
"""
import collections
import datetime
import json
import math
import os

from .items import from_receipt

DROP = "2026-11-01"
PRIOR = (("Rune", 10e9), ("Dye", 3e9), ("Skin", 10e9), ("Sound", 8e9))     # only says what is a collectible
RATIO_LIMITS = (0.4, 1.2)


def ms(text):
    when = datetime.datetime.fromisoformat(text).replace(tzinfo=datetime.timezone.utc)
    return int(when.timestamp() * 1000)


def geomean(values):
    return math.exp(sum(math.log(v) for v in values) / len(values))


class Reports:
    def __init__(self, rows, catalog):
        self.catalog = catalog
        self.rows = sorted((self._read(row) for row in rows), key=lambda r: r["at"])
        self.cut = ms(DROP)
        self.ratio = 1.0
        self.since = 0          # trades the ratio rests on

    @classmethod
    def load(cls, path, catalog):
        if not os.path.exists(path):
            return cls([], catalog)
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f), catalog)

    def _read(self, row):
        got, gave = collections.Counter(), collections.Counter()
        paid = 0
        for side, lines in ((got, row.get("got") or ()), (gave, row.get("gave") or ())):
            for line in lines:
                kind, amount, item = from_receipt(line, self.catalog)
                if kind == "coins":
                    paid += amount if side is gave else -amount
                elif kind == "item":
                    side[item.entry] += amount
        stated = {self.catalog.names.get(name, name): coins for name, coins in (row.get("stated") or {}).items()}
        return dict(at=ms(row["at"]), by=row.get("by"), other=row.get("with"), post=row.get("post"), note=row.get("note"), got=got, gave=gave,
                    coins=paid, overpaid=row.get("overpaid") or 0, stated=stated, lines=dict(got=row.get("got") or [], gave=row.get("gave") or []))

    def _name(self, entry):
        return self.catalog.name(entry).lstrip("◆✦ ")

    def _prior(self, entry):
        name = self._name(entry)
        return next((coins for word, coins in PRIOR if word in name), 0)

    def _sides(self, row, price):
        """What is still unpriced in a trade and what it must add up to, or None if it cannot say."""
        paid = row["coins"] - row["overpaid"]
        loose_got = [(e, n) for e, n in row["got"].items() if e not in price]
        loose_gave = [(e, n) for e, n in row["gave"].items() if e not in price]
        if bool(loose_got) == bool(loose_gave):
            return None     # nothing to learn, or unknowns on both sides
        loose = loose_got or loose_gave
        if any(not self._prior(e) for e, _ in loose):
            return None     # a sword with no price, which this is not here to guess
        fixed_got = sum(n * price[e] for e, n in row["got"].items() if e in price)
        fixed_gave = sum(n * price[e] for e, n in row["gave"].items() if e in price)
        target = paid + fixed_gave - fixed_got if loose_got else fixed_got - paid - fixed_gave
        return (loose, target) if target > 0 else None

    def solve(self, market=None):
        """{entry: price row}. `market` gives the AH price of what does trade there, so a bundle with
        a Pew Pew Bow Sound in it can use what that really sells for. Those prices are today's
        being used on October's bundles, which leaves the rest of a bundle a touch high. Scaling
        them by the ratio looks tidier and runs away: the ratio lowers the rest of the bundle,
        which lowers the ratio, until a 10b skin comes out at 388m."""
        price, how = self._october(market or (lambda entry: 0))
        self.ratio = self._since_drop(price, how)
        self._after_drop_alone(price, how)
        return self._rows(price, how)

    def _after_drop_alone(self, price, how):
        """An item first seen in a trade after the drop, alone or with everything else priced.
        Those trades only ever fed the ratio, so an Ocean Dye bought for 6b on Nov 16 priced
        nothing (Fear, 2026-11-16). Such a trade is today's price; it is stored divided by the
        ratio because every receipt price is kept in October terms and shown times the ratio."""
        after = [r for r in self.rows if r["at"] >= self.cut]
        changed = True
        while changed:
            now = {e: (v if how[e] == "ah" else v * self.ratio) for e, v in price.items()}
            found = collections.defaultdict(list)
            for row in after:
                sides = self._sides(row, now)
                if sides and len(sides[0]) == 1:
                    entry, amount = sides[0][0]
                    found[entry].append(sides[1] / amount / self.ratio)
            changed = bool(found)
            for entry, values in found.items():
                price[entry], how[entry] = geomean(values), "alone"

    def _october(self, market):
        price, how, alone = {}, {}, {}
        before = [r for r in self.rows if r["at"] < self.cut]
        for row in self.rows:
            for entry in list(row["got"]) + list(row["gave"]):
                value = market(entry)
                if value:
                    price[entry], how[entry] = value, "ah"
        for row in before:
            for entry, coins in row["stated"].items():
                price[entry], how[entry] = coins, "said"
        # One unknown left in a trade is a real price, and it can free the next trade up.
        changed = True
        while changed:
            found = collections.defaultdict(list)
            for row in before:
                sides = self._sides(row, price)
                if sides and len(sides[0]) == 1:
                    entry, amount = sides[0][0]
                    found[entry].append(sides[1] / amount)
            changed = bool(found)
            for entry, values in found.items():
                price[entry], how[entry] = geomean(values), "alone"
                alone[entry] = list(values)
        # A bundle whose items all have single prices now adds a vote for each. Items seen only
        # in bundles stay unpriced.
        votes = self._bundle_votes(before, price, how)
        for entry, extra in votes.items():
            price[entry] = geomean(alone[entry] + extra)
        return price, how

    def _bundle_votes(self, rows, price, how):
        """{entry: [unit votes]} from trades with two or more single-priced kinds on one side.
        Market and stated prices are facts, not votes: they only fix what the rest must add to."""
        votes = collections.defaultdict(list)
        paid = lambda row: row["coins"] - row["overpaid"]
        for row in rows:
            got_soft = [(e, n) for e, n in row["got"].items() if how.get(e) == "alone"]
            gave_soft = [(e, n) for e, n in row["gave"].items() if how.get(e) == "alone"]
            if got_soft and gave_soft:
                continue        # bundles on both sides: no way to tell which side the money moved
            soft = got_soft or gave_soft
            if len(soft) < 2:
                continue        # a lone item is already its own single price
            if any(e not in price for e in list(row["got"]) + list(row["gave"])):
                continue        # still waiting on a single sale of something in it
            side, other = ("got", "gave") if got_soft else ("gave", "got")
            fixed_side = sum(n * price[e] for e, n in row[side].items() if how.get(e) != "alone")
            fixed_other = sum(n * price[e] for e, n in row[other].items())
            target = paid(row) + fixed_other - fixed_side if side == "got" else fixed_other - paid(row) - fixed_side
            weight = sum(n * price[e] for e, n in soft)
            if target <= 0 or weight <= 0:
                continue
            for entry, _n in soft:
                votes[entry].append(target * price[entry] / weight)
        return votes

    def _since_drop(self, price, how):
        """How the trades since the drop compare with what those items fetched before it."""
        coins = worth = used = 0
        for row in self.rows:
            if row["at"] < self.cut or bool(row["got"]) == bool(row["gave"]) or not row["coins"]:
                continue
            rows = list(row["got"].items()) + list(row["gave"].items())
            if all(entry in price for entry, _n in rows):
                coins += abs(row["coins"])
                worth += sum(n * price[entry] for entry, n in rows)
                used += 1
        self.since = used
        low, high = RATIO_LIMITS
        return min(max(coins / worth, low), high) if worth else 1.0

    def _rows(self, price, how):
        out = {}
        for entry, value in price.items():
            name = self._name(entry)
            seen = self.about(entry)
            row = dict(entry=entry, name=name, value=value if how[entry] == "ah" else value * self.ratio, october=0 if how[entry] == "ah" else value,
                       how=how[entry], trades=len(seen), at=seen[-1]["at"] if seen else 0, collectible=bool(self._prior(entry)))
            # Three armours each have a "Baby Skin" and a receipt only shows the words.
            for twin in self.catalog.named.get(name) or ():
                out[twin] = row
            out[entry] = row
        return out

    def about(self, entry):
        names = set(self.catalog.named.get(self._name(entry)) or ()) | {entry}
        return [row for row in self.rows if names & (set(row["got"]) | set(row["gave"]) | set(row["stated"]))]

    @staticmethod
    def view(row):
        return dict(at=row["at"], by=row["by"], other=row["other"], post=row["post"], note=row["note"], coins=row["coins"], got=row["lines"]["got"], gave=row["lines"]["gave"])
