"""The private site and the mod's API, on the Python standard library alone.

nginx terminates TLS and forwards /market/ here. A long random key sits in the path, the same way
the census page works, so this needs no accounts. It answers 404 to anything else, including a
wrong key, so a stranger cannot tell the service exists.
"""
import base64
import collections
import gzip
import json
import mimetypes
import os
import re
import statistics
import time
import urllib.parse
from contextlib import closing
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import crafts as C
from . import engine as E
from . import fees
from . import replay
from .items import FILTERS, RANK, RARITIES, Item, field, from_json, passes
from .link import BROWSER_SECONDS, COOKIE, LinkError, Pairing

DAY = 86_400_000
HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "..", "site")
MOD_JAR = os.path.join(SITE, "dragsim-market-client.jar")
_mod_version = (None, None)


def mod_version():
    """The version of the jar the site hands out, read from the jar itself so a new upload is the
    whole release: the mod compares it with its own and says once when a newer one is out."""
    global _mod_version
    import zipfile
    try:
        stamp = os.path.getmtime(MOD_JAR)
        if _mod_version[0] != stamp:
            with zipfile.ZipFile(MOD_JAR) as jar:
                _mod_version = (stamp, json.loads(jar.read("fabric.mod.json")).get("version"))
        return _mod_version[1]
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
SORTS = {"time": lambda f: -f["sell"]["at"], "profit": lambda f: -f["profit"], "loss": lambda f: f["profit"],
         "price": lambda f: -f["sell"]["price"], "percent": lambda f: -(f["roi"] or 0)}


def texture(item):
    """The hash of a skull's skin, so a head can be drawn. Nothing else in the base64 is used."""
    value = ((item or {}).get("skin") or {}).get("value")
    if not value:
        return None
    try:
        data = json.loads(base64.b64decode(value + "=" * (-len(value) % 4)))
        return data["textures"]["SKIN"]["url"].rsplit("/", 1)[1]
    except (ValueError, KeyError, IndexError):
        return None


class Api:
    def __init__(self, service, key):
        self.s = service
        self.key = key

    def icon(self, entry):
        item = self.s.catalog.items.get(entry) or {}
        if item.get("icon"):
            return {"url": "https://dragsim.co" + item["icon"]}
        if entry in self.s.pictures:
            # Dragsim's own model from its pack (market/pictures.py). Ahead of the head, since in game
            # the pack's model is what shows; the head is only for players without the pack.
            return {"url": f"pictures/{entry}.png"}
        tex = texture(item)
        if not tex and "_pet_" in entry:
            # Ids drop the underscore in places: the market says ender_dragon, the pet list enderdragon.
            wanted = entry.split("_pet_")[0].replace("_", "")
            for key, pet in self.s.catalog.pets.items():
                if key.replace("_", "").lower() == wanted:
                    tex = texture(pet)
                    break
        if tex:
            return {"head": tex}
        picture = self.s.catalog.vanilla_picture(entry)
        if not picture:
            return None
        path, alt = picture
        found = {"url": "https://dragsim.co" + path}
        if alt:
            found["alt"] = "https://dragsim.co" + alt      # the page tries it when the first is missing
        return found

    def tag(self, row):
        row["icon"] = self.icon(row.get("entry"))
        return row

    def status(self, q):
        s = self.s
        return {"built_at": s.built_at, "last_poll": s.last_poll, "listings": len(s.listing), "flips": len(s.tracker.flips),
                "deals": len(s.deals), "now": int(time.time() * 1000), "market_time": s.engine.now, "tracked": list(s.tracked),
                "alerts": sum(1 for w in s.watches() if s.watch_matches(w)), "mod_version": mod_version()}

    def home(self, q):
        s, t = self.s, self.s.tracker
        player = q.get("player", [s.tracked[0]])[0]
        now = s.engine.now
        return {"player": player, "all": self._brief(t.summary(player)), "week": self._brief(t.summary(player, now - 7 * DAY)),
                "day": self._brief(t.summary(player, now - DAY)),
                "deals": self.deals(q)["deals"][:8], "flips": [self.tag(f) for f in self._flips(player, 0)[:8]],
                "market": self.market_note()}

    def market_note(self):
        s = self.s
        flips = s.tracker.flips[-400:]
        day = s.engine.now - DAY
        return {"sales_today": sum(1 for f in s.tracker.flips if f["sell"]["at"] >= day), "listings": len(s.listing)}

    @staticmethod
    def _brief(summary):
        best = summary.pop("best", None)
        summary["best"] = {"name": best["name"], "profit": best["profit"]} if best else None
        return summary

    def _flips(self, player, since, item=None):
        rows = [f for f in self.s.tracker.flips if (player is None or f["player"] == player) and f["sell"]["at"] >= since
                and (item is None or item in f["entry"] or item in f["name"].lower())]
        rows.sort(key=SORTS["time"])
        return rows

    def flips(self, q):
        player = q.get("player", [None])[0] or None
        days = int(q.get("days", ["0"])[0] or 0)
        since = self.s.engine.now - days * DAY if days else 0
        item = (q.get("item", [""])[0] or "").lower() or None
        rows = self._flips(player, since, item)
        flag = q.get("flag", [""])[0]
        if flag == "trades":
            rows = [f for f in rows if f["sell"]["via"] == "trade" or f["buy"]["via"] == "trade"]
        elif flag == "crafts":
            rows = [f for f in rows if "craft" in f["flags"]]
        elif flag == "changed":
            rows = [f for f in rows if "changed" in f["flags"] or "craft" in f["flags"]]
        elif flag == "sure":
            rows = [f for f in rows if not {"uncertain_cost", "unknown_cost"} & set(f["flags"])]
        for name, test in (("min", lambda f, v: f["profit"] >= v), ("max", lambda f, v: f["profit"] <= v)):
            raw = q.get(name, [""])[0]
            if raw:
                value = float(raw)
                rows = [f for f in rows if test(f, value)]
        rows.sort(key=SORTS.get(q.get("sort", ["time"])[0], SORTS["time"]))
        counted = [f for f in rows if self.s.tracker.counted(f)]
        limit = min(int(q.get("limit", ["100"])[0]), 500)
        unmatched = sum(1 for x in self.s.tracker.sales_without_buy if x["player"] == player and x["at"] >= since)
        return {"total": len(rows), "profit": sum(f["profit"] for f in counted), "volume": sum(f["sell"]["price"] for f in counted),
                "wins": sum(1 for f in counted if f["profit"] > 0), "counted": len(counted), "unmatched": unmatched,
                "flips": [self.tag(f) for f in rows[:limit]]}

    def recent(self, q):
        return {"flips": [self.tag(f) for f in sorted(self.s.tracker.flips, key=SORTS["time"])[:60] if self.s.tracker.counted(f)]}

    def positions(self, q):
        player = q.get("player", [self.s.tracked[0]])[0]
        return {"positions": [self.tag(p) for p in self.s.tracker.positions(player)]}

    def trades(self, q):
        player = q.get("player", [self.s.tracked[0]])[0]
        rows = [t for t in self.s.tracker.trades if t["player"] == player]
        rows.sort(key=lambda t: -t["at"])
        return {"trades": rows[:300]}

    def deals(self, q):
        rows = sorted(self.s.deals.values(), key=lambda d: -d["profit"])[:100]
        entries = sorted({row["entry"] for row in rows})
        sold = {}
        if entries:
            # The pricing model's volume is an estimate for a pricing bucket. This is the
            # observed number of units sold across the item's variants over a rolling day.
            now = int(time.time() * 1000)
            marks = ",".join("?" for _ in entries)
            with closing(replay.connect(self.s.source)) as db:
                sold = dict(db.execute(f"SELECT entry, SUM(COALESCE(NULLIF(count, 0), 1)) FROM sales "
                                       f"WHERE entry IN ({marks}) AND sold_at > ? AND sold_at <= ? "
                                       "AND unit_price > 0 GROUP BY entry", (*entries, now - DAY, now)))
        return {"deals": [self.tag(dict(row, units_sold_24h=sold.get(row["entry"], 0))) for row in rows],
                "as_of": self.s.last_poll or self.s.built_at, "watch": self.s.recent_hits()}

    def leaderboard(self, q):
        days = int(q.get("days", ["0"])[0] or 0)
        since = self.s.engine.now - days * DAY if days else 0
        return {"rows": self.s.tracker.leaderboard(since, 100)}

    def search(self, q):
        text = (q.get("q", [""])[0] or "").strip().lower()
        if len(text) < 2:
            return {"items": [], "players": []}
        catalog = self.s.catalog
        lookups = self.s.engine.lookups
        sales = lambda entry: sum(len(b.refs) for b in lookups[entry].buckets.values()) if entry in lookups else 0
        hits = {}
        for entry, item in catalog.items.items():
            name = catalog.name(entry)
            if text not in name.lower():
                continue
            # A pet is one result. Which rarity is a filter on its page, so it opens on the one
            # that actually trades.
            litter = catalog.litter(entry)
            key = litter[0] if litter else entry
            if key not in hits:
                best = max(litter, key=sales) if litter else entry
                # Eight different items are all called Dragon Fragments, so say which.
                label = f"{name} ({entry.split('_')[0]})" if name in catalog.ambiguous and not litter else name
                hits[key] = (not name.lower().startswith(text), -sum(map(sales, litter or [entry])), label, best)
        ranked = sorted(hits.values())
        # Everyone who has bought, sold or listed on the AH. The tracker's own list only holds the
        # players a view tracks, which for a signed-out visitor is nobody.
        names = [name for name in self.s.players if text in name.lower()]
        names.sort(key=lambda n: (not n.lower().startswith(text), n.lower()))
        return {"items": [self.tag({"entry": h[3], "name": h[2], "tier": catalog.tier(h[3])}) for h in ranked[:12]],
                "players": names[:6]}

    def _probe(self, entry):
        info = self.s.catalog.items.get(entry) or {}
        probe = Item(entry)
        probe.rarity = RANK.get(info.get("tier") or "NONE", 0)
        probe.name = self.s.catalog.name(entry)
        return probe

    def item(self, entry, q):
        s = self.s
        entry = entry.lower()
        info = s.catalog.items.get(entry) or {}
        probe = self._probe(entry)
        with s.lock:
            est = dict(s.engine.estimate(probe))
            est["offers"] = E.offers(est)
            est["insta_sell"] = E.insta_sell(est) if est["value"] else 0
            craft = s.engine.craft_cost(entry)
            bazaar = s.engine.bazaar.quote(entry) if s.engine.bazaar else None
            found = s.engine.lookups.get(entry)
            variants = []
            if found:
                for key, bucket in found.buckets.items():
                    variants.append({"key": [f"{k} {v}" for k, v in key.mods], "tier": RARITIES[key.tier] if key.tier < len(RARITIES) else "?",
                                     "count": key.count, "price": int(bucket.price), "sales": len(bucket.refs), "volume": round(bucket.volume, 2),
                                     "lbin": int(bucket.lbins[0].price) if bucket.lbins else 0, "live": len(bucket.lbins)})
                variants.sort(key=lambda v: -v["sales"])
            live = sorted(((lid, row) for lid, row in s.listing.items() if row[0].entry == entry), key=lambda kv: kv[1][1])
        db = replay.connect(s.source)
        rows = db.execute("SELECT sold_at, unit_price, count, seller, buyer, item FROM sales WHERE entry = ? ORDER BY sold_at DESC LIMIT 1500", (entry,)).fetchall()
        seen = [from_json(row[5]) for row in rows] + [row[0] for _, row in live]
        history = []
        if bazaar:
            history = db.execute("SELECT ts, best_bid, best_ask FROM quotes WHERE product = ? AND ts > ? ORDER BY ts", (entry, s.engine.now - 30 * DAY)).fetchall()
        db.close()
        days = collections.defaultdict(list)
        for at, unit, count, seller, buyer, raw in rows:
            days[at // DAY].append(unit)
        chart = [{"day": d * DAY, "low": min(v), "median": statistics.median(v), "high": max(v), "sold": len(v)} for d, v in sorted(days.items())]
        if bazaar and not chart:
            buckets = collections.defaultdict(list)
            for ts, bid, ask in history:
                buckets[ts // DAY].append(ask or bid or 0)
            chart = [{"day": d * DAY, "low": min(v), "median": statistics.median(v), "high": max(v), "sold": 0} for d, v in sorted(buckets.items())]
        recent = []
        for at, unit, count, seller, buyer, raw in rows[:40]:
            item = from_json(raw)
            recent.append({"at": at, "unit": unit, "count": count, "seller": seller, "buyer": buyer, "label": item.label(), "rarity": item.rarity,
                           "parts": describe(item)})
        return self.tag({"entry": entry, "name": s.catalog.name(entry), "tier": info.get("tier"), "category": info.get("category"),
                         "npc": s.catalog.npc(entry), "craft": craft, "bazaar": bazaar, "estimate": est, "chart": chart[-45:], "recent": recent,
                         "variants": variants[:24], "fee_rate": fees.rate(est["value"] or 0),
                         "recipes": s.recipes.get(entry) or [],
                         "reported": s.engine.reported.get(entry), "reports": [s.reports.view(r) for r in reversed(s.reports.about(entry))],
                         "filters": self._filters(entry, info, seen), "litter": self._litter(entry),
                         "live": [{"id": lid, "unit": row[1], "count": row[4], "seller": row[5], "end": row[3], "label": row[0].label(), "rarity": row[0].rarity,
                                   "parts": describe(row[0])} for lid, row in live[:30]]})

    # ---------------------------------------------------------------- item pages

    def _litter(self, entry):
        """The rarity picker on a pet's page. Each rarity is its own market, so this moves between
        pages rather than filtering one."""
        catalog = self.s.catalog
        rows = catalog.litter(entry)
        return [{"entry": e, "tier": catalog.tier(e)} for e in sorted(rows, key=lambda e: RANK.get(catalog.tier(e) or "NONE", 0))] if len(rows) > 1 else []

    def _filters(self, entry, info, seen):
        """What this item can have, not only what has happened to sell. A helmet with no recombed
        sale on record can still be recombed, so the filter is offered either way."""
        category = info.get("category")
        keys = set(ALWAYS.get(category, ()))
        catalog = self.s.catalog
        values = collections.defaultdict(set)
        for key, defaults in DEFAULTS.items():
            if key in keys:
                values[key].update(defaults)
        for item in seen:
            for key in FILTERS:
                value = field(item, key)
                if value not in ("", "none", "0", "no") and value != "NONE":
                    keys.add(key)
                    values[key].add(value)
        for key in OPTIONAL:
            if key in keys:
                values[key].add("none")
        if catalog.litter(entry) or len(values["rarity"]) < 2:
            keys.discard("rarity")
        return {key: [[v, filter_label(catalog, key, v)] for v in sorted(values[key], key=filter_order)] for key in FILTERS if key in keys}

    def _sold(self, entry, q, limit=6000):
        """Sales of an item that pass the page's filters, newest first."""
        db = replay.connect(self.s.source)
        rows = db.execute("SELECT id, sold_at, created_at, unit_price, count, seller, buyer, item, bin FROM sales WHERE entry = ? "
                          "ORDER BY sold_at DESC LIMIT ?", (entry, limit)).fetchall()
        db.close()
        wanted = filters_from(q)
        out = []
        for row in rows:
            item = from_json(row[7])
            if passes(item, wanted):
                out.append((row, item))
        return out

    def history(self, entry, q):
        """What the chart draws. The ranges are spaced so each has a sensible
        number of points on a market this thin."""
        entry = entry.lower()
        name = q.get("range", ["week"])[0]
        span, step = RANGES.get(name, RANGES["week"])
        now = self.s.engine.now
        since = now - span if span else 0
        buckets = collections.defaultdict(list)
        for row, _ in self._sold(entry, q):
            if row[1] >= since:
                buckets[row[1] // step].append((row[3], row[4]))
        points = []
        for slot, sales in sorted(buckets.items()):
            prices = [p for p, _ in sales]
            middle = statistics.median(prices)
            # A 1 coin gift flattens the whole chart and says nothing about the price.
            real = [p for p in prices if p >= middle * 0.05] or prices
            count = sum(n for _, n in sales)
            points.append({"t": slot * step, "avg": sum(p * n for p, n in sales) / count, "min": min(real), "max": max(real), "vol": count})
        out = {"range": name, "rows": points, "bazaar": False}
        book = self.s.engine.bazaar.books.get(entry) if self.s.engine.bazaar else None
        if book and not any(q.get(k) for k in FILTERS):
            db = replay.connect(self.s.source)
            rows = db.execute("SELECT ts, best_bid, best_ask FROM quotes WHERE product = ? AND ts >= ? ORDER BY ts", (entry, since)).fetchall()
            db.close()
            quotes = collections.defaultdict(list)
            for ts, bid, ask in rows:
                quotes[ts // step].append((bid or 0, ask or 0))
            fills = collections.Counter()
            for fill in self.s.tape_rows(since, entry):
                fills[fill[0] // step] += fill[4]
            out["bazaar"] = True
            out["book"] = [{"t": slot * step, "bid": statistics.median(b for b, _ in v), "ask": statistics.median(a for _, a in v), "vol": fills.get(slot, 0)}
                           for slot, v in sorted(quotes.items())]
        return out

    def auctions(self, entry, q):
        """The cards under the chart: sold or still listed."""
        entry = entry.lower()
        wanted = filters_from(q)
        if q.get("type", ["sold"])[0] == "active":
            with self.s.lock:
                live = sorted(((lid, row) for lid, row in self.s.listing.items() if row[0].entry == entry and passes(row[0], wanted)), key=lambda kv: kv[1][1])
            return {"auctions": [{"id": lid, "unit": row[1], "count": row[4], "seller": row[5], "end": row[3], "start": row[2], "label": row[0].label(),
                                  "rarity": row[0].rarity, "parts": describe(row[0])} for lid, row in live[:60]]}
        return {"auctions": [{"id": row[0], "unit": row[3], "count": row[4], "seller": row[5], "buyer": row[6], "at": row[1], "label": item.label(),
                              "rarity": item.rarity, "parts": describe(item)} for row, item in self._sold(entry, q)[:60]]}

    @staticmethod
    def _listing_status(outcome):
        if outcome in ("expired", "cancelled", "sold"):
            return outcome
        # A listing disappearing from the feed does not establish how it ended.
        return "unobserved" if outcome else "active"

    def auction(self, aid):
        """One auction, sold or not, for its own page."""
        db = replay.connect(self.s.source)
        sold = db.execute("SELECT entry, sold_at, created_at, price, unit_price, count, seller, buyer, item, bin FROM sales WHERE id = ?", (aid,)).fetchone()
        listed = db.execute("SELECT entry, created_at, end_at, price, unit_price, count, seller, item, bin, outcome, last_seen FROM listings WHERE id = ?", (aid,)).fetchone()
        db.close()
        if not sold and not listed:
            return None
        raw = sold[8] if sold else listed[7]
        item = from_json(raw)
        out = {"id": aid, "entry": item.entry, "label": item.label(), "rarity": item.rarity, "parts": describe(item), "count": item.count,
               "reforge": item.reforge, "upgrades": {k: str(v) for k, v in item.upgrades().items()}}
        if sold:
            out.update(status="sold", price=sold[3], unit=sold[4], seller=sold[6], buyer=sold[7], start=sold[2], end=sold[1], bin=bool(sold[9]))
        else:
            status = self._listing_status(listed[9])
            out.update(status=status, price=listed[3], unit=listed[4], seller=listed[6], buyer=None, start=listed[1],
                       end=listed[2] if status in ("active", "expired") else listed[10], bin=bool(listed[8]))
        with self.s.lock:
            est = dict(self.s.engine.estimate(item))
            est["offers"] = E.offers(est)
            out["estimate"] = est
            flips = [f for f in self.s.tracker.flips if f["sell"]["ref"] == aid or f["buy"]["ref"] == aid]
        out["flips"] = [self.tag(f) for f in flips]
        return self.tag(out)

    def player_auctions(self, name, q):
        """A player's Auctions tab: everything they listed, sold or not, newest first."""
        offset = int(q.get("offset", ["0"])[0])
        db = replay.connect(self.s.source)
        listed = db.execute("SELECT id, entry, created_at, end_at, unit_price, count, bin, item, outcome, last_seen FROM listings WHERE seller = ? "
                            "ORDER BY created_at DESC LIMIT 60 OFFSET ?", (name, offset)).fetchall()
        known = {row[0] for row in listed}
        sold = {row[0]: row for row in db.execute("SELECT id, sold_at, unit_price, buyer, entry, created_at, count, bin, item FROM sales WHERE seller = ? "
                                                  "ORDER BY sold_at DESC LIMIT 200", (name,))}
        db.close()
        rows = []
        for lid, entry, created, end, unit, count, bin_, raw, outcome, last in listed:
            item = from_json(raw)
            sale = sold.get(lid)
            status = "sold" if sale else self._listing_status(outcome)
            rows.append(self._card(lid, item, unit, count, bin_, created, sale[1] if sale else (end if status in ("active", "expired") else last), status,
                                   sale[3] if sale else None))
        if not offset:
            for sid, row in sold.items():
                if sid not in known:
                    rows.append(self._card(sid, from_json(row[8]), row[2], row[6], row[7], row[5], row[1], "sold", row[3]))
        rows.sort(key=lambda r: -r["start"])
        return {"auctions": rows[:60]}

    def player_bids(self, name, q):
        offset = int(q.get("offset", ["0"])[0])
        db = replay.connect(self.s.source)
        rows = db.execute("SELECT id, sold_at, created_at, unit_price, count, seller, bin, item FROM sales WHERE buyer = ? ORDER BY sold_at DESC LIMIT 60 OFFSET ?",
                          (name, offset)).fetchall()
        db.close()
        return {"auctions": [dict(self._card(r[0], from_json(r[7]), r[3], r[4], r[6], r[2], r[1], "sold", None), seller=r[5]) for r in rows]}

    def _card(self, aid, item, unit, count, bin_, start, end, status, buyer):
        return self.tag({"id": aid, "entry": item.entry, "name": item.label(), "rarity": item.rarity, "unit": unit, "count": count, "bin": bool(bin_),
                         "start": start or 0, "end": end or 0, "status": status, "buyer": buyer, "parts": describe(item)})

    def player(self, name, q):
        s = self.s
        with s.lock:
            flips = self._flips(name, 0)
            summary = self._brief(s.tracker.summary(name))
        db = replay.connect(s.source)
        sold = db.execute("SELECT sold_at, entry, unit_price, count, buyer FROM sales WHERE seller = ? ORDER BY sold_at DESC LIMIT 60", (name,)).fetchall()
        bought = db.execute("SELECT sold_at, entry, unit_price, count, seller FROM sales WHERE buyer = ? ORDER BY sold_at DESC LIMIT 60", (name,)).fetchall()
        db.close()
        names = s.catalog.name
        return {"player": name, "summary": summary, "flips": [self.tag(f) for f in flips[:100]],
                "sold": [self.tag({"at": a, "entry": e, "name": names(e), "unit": u, "count": c, "other": o}) for a, e, u, c, o in sold],
                "bought": [self.tag({"at": a, "entry": e, "name": names(e), "unit": u, "count": c, "other": o}) for a, e, u, c, o in bought]}

    def bazaar(self, q):
        s = self.s
        tally = dict(s.tape.tally)
        return {"products": [self.tag(r) for r in s.bazaar_board()], "tally": tally, "since": (s.tape_rows(limit=None)[:1] or [[None]])[0][0]}

    def bazaar_tape(self, entry, q):
        rows = self.s.tape_rows(0, entry, 200)
        return {"entry": entry, "tape": [{"at": r[0], "side": r[2], "price": r[3], "amount": r[4], "proof": r[5]} for r in rows]}

    # ---------------------------------------------------------------- crafts

    def _craft_row(self, row):
        sale = row["sale"] or {}
        catalog = self.s.catalog
        return self.tag({"entry": row["entry"], "name": catalog.name(row["entry"]), "tier": catalog.tier(row["entry"]), "count": row["count"], "kind": row["kind"],
                         "cost": row["cost"], "price": sale.get("price", 0), "net": row["net"], "profit": row["profit"], "roi": row["roi"], "hours": row["hours"],
                         "per_hour": row["per_hour"], "via": sale.get("via"), "median": sale.get("median", 0), "lbin": sale.get("lbin", 0), "volume": sale.get("volume", 0),
                         "time_to_sell": sale.get("time_to_sell", 0), "multi_step": row["multi_step"], "tested": C.tested(row), "estimated": row["estimated"],
                         "category": (catalog.items.get(row["entry"]) or {}).get("category")})

    def crafts(self, q):
        rows = [self._craft_row(row) for row in self.s.craft_board()]
        rows.sort(key=lambda r: -r["profit"])
        return {"crafts": rows, "as_of": self.s.last_poll or self.s.built_at}

    def _plan(self, got, shopping):
        """The tree of what to do, and the same thing as one list of what to go and buy."""
        catalog = self.s.catalog
        sources = {}
        for source, amount, unit, seller in got.fills:
            row = sources.setdefault(source, {"source": source, "count": 0, "coins": 0, "sellers": []})
            row["count"] += amount
            row["coins"] += amount * unit
            if seller and seller not in row["sellers"]:
                row["sellers"].append(seller)
        if sources:
            # Anything bought goes on the list, including the copies bought for a part that is
            # otherwise made (the hybrid: buy the cheap ones, make the rest).
            line = shopping.setdefault(got.entry, self.tag({"entry": got.entry, "name": catalog.name(got.entry), "count": 0, "cost": 0, "sources": {}}))
            line["count"] += sum(row["count"] for row in sources.values())
            line["cost"] += sum(row["coins"] for row in sources.values())
            for source, row in sources.items():
                into = line["sources"].setdefault(source, {"source": source, "count": 0, "coins": 0, "sellers": []})
                into["count"] += row["count"]
                into["coins"] += row["coins"]
                into["sellers"].extend(x for x in row["sellers"] if x not in into["sellers"])
        return self.tag({"entry": got.entry, "name": catalog.name(got.entry), "count": got.count, "cost": got.cost, "method": got.method,
                         "made": got.made, "kind": (got.recipe or {}).get("kind"), "hours": got.hours, "coins": got.coins, "buy_cost": got.buy_cost,
                         "sources": list(sources.values()), "parts": [self._plan(part, shopping) for part in got.parts]})

    def craft(self, entry, q):
        """Craft details: what to buy, where, and how it all adds up, at any quantity."""
        s = self.s
        entry = entry.lower()
        count = max(1, min(int(q.get("n", ["1"])[0] or 1), C.MAX_COUNT))
        catalog = s.catalog
        with s.lock:
            crafts = s.crafts()
            row = crafts.flip(entry, count)
            if row is None:
                return self.tag({"entry": entry, "name": catalog.name(entry), "tier": catalog.tier(entry), "count": count, "cost": 0,
                                 "short": [catalog.name(e) for e in dict.fromkeys(crafts.short(entry, count))], "recipes": s.recipes.get(entry) or []})
            shopping = {}
            out = self._craft_row(row)
            out["plan"] = self._plan(row["plan"], shopping)
        for line in shopping.values():
            line["sources"] = list(line["sources"].values())
        out["shopping"] = sorted(shopping.values(), key=lambda line: -line["cost"])
        out["recipes"] = s.recipes.get(entry) or []
        return out

    # ---------------------------------------------------------------- the notifier

    def watches(self, q):
        s = self.s
        return {"watches": [self._watch(w) for w in s.watches()]}

    def _watch(self, watch):
        """One tracked item: where it is now, where it has been, and anything at your price."""
        s = self.s
        entry = watch["entry"]
        wanted = watch["filters"]
        est = s.engine.estimate(self._probe(entry))
        listed = s.listed(entry, wanted)
        book = s.engine.bazaar.books.get(entry) if s.engine.bazaar else None
        since = s.engine.now - 30 * DAY
        db = replay.connect(s.source)
        rows = db.execute("SELECT sold_at, unit_price, item FROM sales WHERE entry = ? AND sold_at >= ? ORDER BY sold_at", (entry, since)).fetchall()
        db.close()
        sold = [(at, unit) for at, unit, raw in rows if not wanted or passes(from_json(raw), wanted)]
        typical = statistics.median(unit for _, unit in sold) if sold else 0
        real = [(at, unit) for at, unit in sold if unit >= typical * 0.05]     # a 1 coin gift is not a low
        week = [unit for at, unit in real if at >= s.engine.now - 7 * DAY]
        before = [unit for at, unit in real if s.engine.now - 14 * DAY <= at < s.engine.now - 7 * DAY]
        days = collections.defaultdict(list)
        for at, unit in real:
            days[at // DAY].append(unit)
        last = sold[-1] if sold else None
        return self.tag({"id": watch["id"], "entry": entry, "name": s.catalog.name(entry), "tier": s.catalog.tier(entry), "side": watch["side"], "price": watch["price"],
                         "filters": [[LABEL_OF.get(k, k), filter_label(s.catalog, k, v)] for k, v in wanted.items()], "lowest": book.ask if book and book.ask else (listed[0][0] if listed else 0),
                         "listed": sum(row[1] for row in listed), "worth": est["value"], "last": last[1] if last else 0, "last_at": last[0] if last else 0,
                         "week_low": min(week) if week else 0, "week_high": max(week) if week else 0, "sold_week": len(week),
                         "change": statistics.median(week) / statistics.median(before) - 1 if week and before else None,
                         "days": [statistics.median(v) for _, v in sorted(days.items())], "matches": s.watch_matches(watch), "hits": s.watch_hits(watch["id"])})

    def save_watch(self, body):
        with self.s.lock:
            return {"id": self.s.save_watch(body)}

    def drop_watch(self, body):
        with self.s.lock:
            self.s.drop_watch(int(body["id"]))
        return {"ok": True}

    # ---------------------------------------------------------------- favorites

    def price_changes(self, q):
        """/api/prices/change, which the Favorites cards read. Per item: `now` is what one costs this minute, `recent` what
        clean copies have been selling for, `monthly` the middle of the last month's daily
        averages. The card shows now against recent and keeps now against monthly for its tooltip.

        `now` is the item page's Lowest BIN, a bazaar product's Buy price, so the number a card
        prints is the one on the page it opens. With nothing listed it falls back to recent, which reads
        as no change rather than free."""
        s = self.s
        wanted = []
        for raw in q.get("itemTags", []):
            for entry in raw.lower().split(","):
                entry = entry.strip()
                if 0 < len(entry) <= 64 and entry not in wanted:
                    wanted.append(entry)
        out = {}
        with s.lock:
            engine = s.engine
            stamp = engine.now
            for entry in wanted[:FAVORITES]:
                book = engine.bazaar.books.get(entry) if engine.bazaar else None
                if not book and entry not in s.catalog.items and entry not in engine.lookups:
                    continue        # answer for the items known and leave the rest out
                est = engine.estimate(self._probe(entry))
                out[entry] = {"tag": entry, "now": (est.get("bz_buy") if book else est["lbin"]) or None, "recent": est["median"] or None,
                              "monthly": None, "volume": sum(engine.bazaar.daily(entry)) if book else round(est["volume"], 2),
                              "lastUpdated": stamp, "bazaar": bool(book)}
        self._from_sales(out, stamp)
        for row in out.values():
            row["recent"] = round(row["recent"]) if row["recent"] else None
            row["now"] = round(row["now"]) if row["now"] else row["recent"]
        return out

    def _from_sales(self, rows, now):
        """recent and monthly, from the collector and outside the service lock so a month of
        quotes never holds up a poll. Kept a minute, since the cards poll, and a bazaar
        product's month for ten, since that reads every quote of the month.

        recent is the price of clean copies: the median of two
        days of sales, or of fourteen when two days hold fewer than three. Not the engine's
        median, which is a careful flip estimate and runs low. Live it read 21m on Superior Dragon
        Boots whose clean copies had sold around 39m for two days, so a 35m BIN showed as a 67%
        rise when it was 10% under. A product keeps the engine's bazaar price: the middle of the book
        would not do, since here a 1 coin bid against a 500m offer would put that at 250m. An item with
        no clean sale in a fortnight keeps the engine's median too, the only number it has."""
        s = self.s
        clock = time.monotonic()
        db = None
        try:
            for entry, row in rows.items():
                key = (s.source, entry)
                hit = SALE_STATS.get(key)
                if hit is None or clock - hit[0] > (MONTH_SECONDS if row["bazaar"] else MINUTE_SECONDS):
                    db = db or replay.connect(s.source)
                    if row["bazaar"]:
                        # A product is priced by its Buy, so the month it is held against is too.
                        days = collections.defaultdict(list)
                        for ts, ask in db.execute("SELECT ts, best_ask FROM quotes WHERE product = ? AND ts >= ? AND best_ask > 0",
                                                  (entry, now - 30 * DAY)):
                            days[ts // DAY].append(ask)
                        hit = (clock, None, month_median(statistics.median(v) for v in days.values()))
                    else:
                        sales = [(at, unit, count) for at, unit, count, raw in db.execute(
                            "SELECT sold_at, unit_price, count, item FROM sales WHERE entry = ? AND sold_at >= ? AND unit_price > 0",
                            (entry, now - 30 * DAY)) if clean(from_json(raw))]
                        hit = (clock, recent_median(sales, now), month_median(daily_averages(sales)))
                    SALE_STATS[key] = hit
                if hit[1]:
                    row["recent"] = hit[1]
                row["monthly"] = round(hit[2]) if hit[2] else None
        finally:
            if db is not None:
                db.close()

    # ---------------------------------------------------------------- what trades off the AH

    def reported(self, q):
        """Skins, dyes and runes, priced from receipts players posted, and the receipts themselves."""
        s = self.s
        reports = s.reports
        items = []
        for row in {row["entry"]: row for row in s.engine.reported.values() if row["collectible"]}.values():
            est = s.engine.estimate(self._probe(row["entry"]))
            # A real sale beats any receipt, and a thin item can have one without anyone noticing.
            market = est["median_key"] != "reported"
            sold = est["last_sale"] or {}
            items.append(self.tag(dict(row, tier=s.catalog.tier(row["entry"]), value=est["value"] if market and est["value"] else row["value"],
                                       how="ah" if market else row["how"], at=max(row["at"], sold.get("at") or 0))))
        items.sort(key=lambda row: -row["value"])
        money = collections.defaultdict(lambda: {"spent": 0, "got": 0, "trades": 0})
        for row in reports.rows:
            if not row["coins"]:
                continue
            payer, paid = (row["by"], row["other"]) if row["coins"] > 0 else (row["other"], row["by"])
            for name, key in ((payer, "spent"), (paid, "got")):
                if name:
                    money[name][key] += abs(row["coins"])
                    money[name]["trades"] += 1
        people = sorted(({"player": k, **v} for k, v in money.items()), key=lambda r: -(r["spent"] + r["got"]))
        return {"items": items, "trades": [reports.view(r) for r in reversed(reports.rows)],
                "ratio": reports.ratio, "since": reports.since, "cut": reports.cut, "people": people}

    def scan(self, name):
        try:
            result = self.s.scan_player(name)
        except OSError:
            return {"player": name, "error": "dragsim.co did not answer"}
        for row in result.get("items") or ():
            self.tag(row)
        return result

    # ---------------------------------------------------------------- the mod

    def quote(self, body):
        return {"estimates": [self.s.quote(item) for item in (body.get("items") or [])[:64]]}

    def trade(self, body):
        """A receipt from the mod. Applied straight away so its flip shows up at once, because a
        trade can only ever reach the tracker from here."""
        trade = dict(player=body["player"], other=body["other"], at=int(body.get("at") or time.time() * 1000), got=body.get("got") or [],
                     gave=body.get("gave") or [], source="mod", detail=body.get("detail"))
        ref, new = self.s.add_trade(trade)
        record = None
        if new:
            trade["ref"] = ref
            with self.s.lock:
                record = self.s.tracker.on_trade(trade)
        return {"ref": ref, "new": new, "kind": record and record["kind"], "flips": record and record["flips"]}

    def event(self, body):
        self.s.add_event(body)
        return {"ok": True}


HOUR = 3_600_000
RANGES = {"day": (DAY, HOUR), "week": (7 * DAY, 4 * HOUR), "month": (30 * DAY, DAY), "all": (None, DAY)}
LABEL_OF = {"rarity": "Rarity", "stars": "Stars", "reforge": "Reforge", "recomb": "Recomb", "hpb": "Books", "ghpb": "Golden books", "aow": "Art of War", "sync": "Synchronizers",
            "enrich": "Enrichment", "dye": "Dye", "skin": "Skin", "pet_level": "Level", "pet_item": "Pet item", "pet_skin": "Pet skin", "engine": "Engine", "tank": "Tank", "module": "Module"}
ARMOUR = ("stars", "reforge", "recomb", "hpb", "ghpb", "aow", "sync", "dye", "skin")
WEAPON = ("stars", "reforge", "recomb", "hpb", "ghpb", "aow", "sync", "skin")
ALWAYS = {"HELMET": ARMOUR, "CHESTPLATE": ARMOUR, "LEGGINGS": ARMOUR, "BOOTS": ARMOUR, "SWORD": WEAPON, "BOW": WEAPON, "WAND": WEAPON, "AXE": WEAPON,
          "PICKAXE": ("reforge", "recomb", "stars"), "DRILL": ("reforge", "recomb", "engine", "tank", "module"), "ACCESSORY": ("reforge", "recomb", "enrich")}
DEFAULTS = {"recomb": ("yes", "no"), "aow": ("yes", "no"), "stars": tuple("012345"), "hpb": ("0",), "ghpb": ("0",), "sync": ("0",)}
OPTIONAL = ("reforge", "enrich", "dye", "skin", "pet_item", "pet_skin", "engine", "tank", "module")     # can be missing, so "None" is a choice


def filter_order(value):
    return (0, float(value)) if value.replace(".", "", 1).isdigit() else (1, value == "none", value)


def filter_label(catalog, key, value):
    if value in ("yes", "no", "none"):
        return value.title()
    if key in ("dye", "skin", "pet_item", "pet_skin", "engine", "tank", "module"):
        return catalog.name(value)
    if key in ("reforge", "enrich", "rarity"):
        return value.replace("_", " ").title()
    return value


def filters_from(q):
    return {k: q[k][0] for k in FILTERS if q.get(k) and q[k][0]}


FAVORITES = 100                 # more than anyone stars; it only bounds what one request can ask for
SALE_STATS = {}                 # (collector, entry) -> (monotonic seconds, recent, monthly)
MINUTE_SECONDS = 60
MONTH_SECONDS = 600


def clean(item):
    """A clean copy, which price changes are priced by: nothing on the item that a player added,
    except a reforge, which does not move a price."""
    return all(key == "reforge" for key in item.upgrades())


def daily_averages(sales):
    """Each day's average price per unit, from (sold_at, unit, count).
    A 1 coin gift is dropped first, as the chart drops it, or one drags a whole day down."""
    days = collections.defaultdict(list)
    for at, unit, count in sales:
        days[at // DAY].append((unit, count or 1))
    out = []
    for rows in days.values():
        middle = statistics.median(unit for unit, _ in rows)
        real = [(unit, n) for unit, n in rows if unit >= middle * 0.05]
        out.append(sum(unit * n for unit, n in real) / sum(n for _, n in real))
    return out


def month_median(averages):
    """The monthly price: the middle of the positive daily averages, the upper of the two middle
    days when the count is even."""
    rows = sorted(a for a in averages if a > 0)
    return rows[len(rows) // 2] if rows else None


def recent_median(sales, now):
    """The recent median from (sold_at, unit, count): two days of sales, or fourteen when two hold
    fewer than three, since one odd sale would otherwise be the whole number. Sorted highest
    first, so an even count takes the lower middle."""
    units = [unit for at, unit, _ in sales if at > now - 2 * DAY]
    if len(units) < 3:
        units = [unit for at, unit, _ in sales if at > now - 14 * DAY]
    units.sort(reverse=True)
    return units[len(units) // 2] if units else None


def describe(item):
    out = []
    upgrades = item.upgrades()
    names = {"stars": "stars", "hpb": "books", "ghpb": "golden books", "sync": "sync", "aow": "Art of War"}
    for key, label in names.items():
        if key in upgrades:
            out.append(f"{upgrades[key]} {label}" if key != "aow" else label)
    if item.recomb:
        out.append("recomb")
    for key in ("skin", "dye", "pet_skin"):
        if key in upgrades:
            out.append(str(upgrades[key]).replace("_", " "))
    return out


class LinkedApi(Api):
    def __init__(self, service, player):
        super().__init__(service, None)
        self.player_name = player

    def trade(self, body):
        body = dict(body, player=self.player_name)
        if hasattr(self.s, "record_trade"):
            return self.s.record_trade(dict(player=self.player_name, other=body["other"],
                                           at=int(body.get("at") or time.time() * 1000), got=body.get("got") or [],
                                           gave=body.get("gave") or [], source="mod", detail=body.get("detail")))
        return super().trade(body)

    def event(self, body):
        return super().event(dict(body, player=self.player_name))

    def quote(self, body):
        from .account_tracker import validate_linked_item

        items = body.get("items") or []
        if not isinstance(items, list) or len(items) > 64:
            raise ValueError("invalid quote batch")
        # The mod prices every tooltip it saw together, sixteen at a time, so one odd item answers
        # with nothing instead of failing the request and blanking the prices of the rest with it.
        # Trade totals read value and count from every row, so a blank row still carries both.
        estimates = []
        for item in items:
            try:
                validate_linked_item(item, self.s.catalog)
            except ValueError:
                estimates.append({"value": 0, "count": 1})
                continue
            estimates.append(self.s.quote(item))
        return {"estimates": estimates}


class PublicMarket:
    """What a signed-out visitor reads: every price, listing and auction, and nobody's flips, trades or
    alerts. The owner's tracker holds his trades from the mod, so it is never reached from here; this
    view gets an empty one of its own."""
    SHARED = frozenset({"source", "catalog", "engine", "deals", "lock", "listing", "built_at", "last_poll", "tape",
                        "tape_rows", "bazaar_board", "craft_board", "recipes", "crafts", "reports", "players", "pictures"})
    tracked = ()

    def __init__(self, service):
        self._service = service
        self._tracker = None
        self._built_at = None

    def __getattr__(self, name):
        if name in self.SHARED:
            return getattr(self._service, name)
        raise AttributeError(name)

    @property
    def tracker(self):
        from .tracker import Tracker

        if self._tracker is None or self._built_at != self.built_at:
            self._tracker, self._built_at = Tracker(self.engine, ()), self.built_at
        return self._tracker

    def watches(self):
        return []

    def watch_matches(self, watch):
        return False

    def recent_hits(self):
        return []


class PublicApi(Api):
    """The site for anyone who has not linked. Personal pages answer 401, and the page shows how to
    link with /dsm link instead. A profile scan also waits for a link, since each one fetches
    dragsim.co, and nothing here may become a way to hammer it."""
    PERSONAL = frozenset({"flips", "trades", "positions", "recent", "leaderboard", "watches", "notification-settings"})

    def __init__(self, service):
        super().__init__(PublicMarket(service), None)

    def personal(self, path):
        return path in self.PERSONAL or path.startswith("scan/")

    def status(self, q):
        s = self.s
        return {"built_at": s.built_at, "last_poll": s.last_poll, "listings": len(s.listing), "deals": len(s.deals),
                "now": int(time.time() * 1000), "market_time": s.engine.now, "tracked": [], "alerts": 0, "signed_in": False,
                "mod_version": mod_version()}

    def home(self, q):
        return {"player": None, "deals": self.deals(q)["deals"][:8], "market": self.market_note()}


class PublicV1:
    """The public API at /api/v1, documented at /api (site/api.html). A thin, fixed shape over the
    public site's own methods: the site may add fields for its pages whenever it likes, but what
    is here is a promise to other people's code, so only documented fields go out. Nothing
    personal is reachable: it wraps PublicApi, which has no flips, trades or accounts."""
    ENTRY = re.compile(r"^[a-z0-9_\-']{1,64}$")
    RANGES = ("day", "week", "month", "all")
    MAX_PRICES = 50

    def __init__(self, api):
        self.api = api
        self.s = api.s

    def known(self, entry):
        s = self.s
        return bool(entry in s.catalog.items or entry in s.engine.lookups
                    or (s.engine.bazaar and entry in s.engine.bazaar.books))

    def handle(self, path, q):
        """(status, body) for one GET."""
        if not self.s.built_at:
            return 503, {"error": "Prices are rebuilding after a restart. Try again in a few seconds."}
        if path == "status":
            last = self.s.last_poll or 0
            return 200, {"market_time": self.s.engine.now, "listings": len(self.s.listing),
                         "ok": bool(last) and time.time() * 1000 - last < 5 * 60_000}
        if path == "search":
            text = (q.get("q", [""])[0] or "").strip()
            if not text:
                return 400, {"error": "Add ?q= with part of an item name."}
            with self.s.lock:
                found = self.api.search({"q": [text]})
            return 200, {"items": [{"entry": i["entry"], "name": i["name"], "tier": i.get("tier")} for i in found["items"]]}
        if path == "bazaar":
            with self.s.lock:
                rows = self.api.bazaar(q)["products"]
            side = lambda d: {"amount": round(d.get("amount") or 0), "average": round(d.get("average") or 0)}
            return 200, {"products": [{"entry": p["entry"], "name": p["name"], "tier": p.get("tier"), "ask": p["ask"], "bid": p["bid"],
                                       "price": round(p["price"] or 0), "bought": side(p["bought"]), "sold": side(p["sold"])} for p in rows]}
        if path == "prices":
            ids = [x.strip().lower() for raw in q.get("ids", []) for x in raw.split(",") if x.strip()]
            if not ids:
                return 400, {"error": "Add ?ids= with up to 50 item ids, separated by commas."}
            if len(ids) > self.MAX_PRICES:
                return 400, {"error": f"Up to {self.MAX_PRICES} ids per request."}
            rows = self.api.price_changes({"itemTags": [",".join(ids)]})
            return 200, {e: {"now": r["now"], "recent": r["recent"], "monthly": r["monthly"], "volume": r["volume"], "bazaar": r["bazaar"]}
                         for e, r in rows.items()}
        m = re.match(r"^item/([^/]+)(?:/(history|sold|active))?$", path)
        if not m:
            return 404, {"error": "No such endpoint. See https://dsmnet.xyz/api"}
        entry, view = urllib.parse.unquote(m.group(1)).lower(), m.group(2)
        if not self.ENTRY.match(entry) or not self.known(entry):
            return 404, {"error": f"Unknown item: {entry}"}
        if view is None:
            d = self.api.item(entry, q)
            e = d["estimate"]
            keep = ("value", "median", "lbin", "lbin_live", "last_sale", "volume", "time_to_sell", "insta_sell", "confidence")
            return 200, {"entry": d["entry"], "name": d["name"], "tier": d["tier"], "npc": d["npc"], "bazaar": d.get("bazaar"),
                         "estimate": {k: e.get(k) for k in keep}}
        if view == "history":
            name = q.get("range", ["week"])[0]
            if name not in self.RANGES:
                return 400, {"error": "range must be day, week, month or all."}
            d = self.api.history(entry, q)
            return 200, {"range": d["range"], "bazaar": d["bazaar"], "rows": d["rows"]}
        try:
            limit = int(q.get("limit", ["24"])[0])
        except ValueError:
            return 400, {"error": "limit must be a number."}
        limit = max(1, min(limit, 100 if view == "sold" else 60))
        d = self.api.auctions(entry, dict(q, type=["active" if view == "active" else "sold"]))
        if view == "active":
            rows = [{"id": a["id"], "unit": a["unit"], "count": a["count"], "seller": a["seller"], "end": a["end"], "parts": a["parts"]}
                    for a in d["auctions"][:limit]]
        else:
            rows = [{"id": a["id"], "unit": a["unit"], "count": a["count"], "seller": a["seller"], "buyer": a["buyer"], "at": a["at"],
                     "parts": a["parts"]} for a in d["auctions"][:limit]]
        return 200, {"auctions": rows}


def make_handler(owner_api, pairing=None, accounts=None):
    public_api = PublicApi(owner_api.s) if pairing is not None else None
    public_v1 = PublicV1(public_api) if public_api is not None else None

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 30

        def log_message(self, *args):
            pass

        def _send(self, code, body=b"", kind="application/json", cache="no-store", headers=()):
            accepts = "gzip" in (self.headers.get("Accept-Encoding") or "")
            if accepts and len(body) > 1024:
                body = gzip.compress(body, 5)
                gz = True
            else:
                gz = False
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            for name, value in headers:
                self.send_header(name, value)
            if gz:
                self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data, code=200, headers=()):
            self._send(code, json.dumps(data, separators=(",", ":"), default=str).encode("utf-8"), headers=headers)

        def _route(self):
            url = urllib.parse.urlsplit(self.path)
            parts = url.path.split("/")
            # /market/<key>/... once through nginx, /<key>/... directly
            if len(parts) > 1 and parts[1] == "market":
                parts = parts[1:]
            linked = len(parts) >= 2 and parts[1] == "link" and pairing is not None
            if not linked and (len(parts) < 2 or parts[1] != owner_api.key):
                return None, None, False
            return "/" + "/".join(parts[2:]), urllib.parse.parse_qs(url.query), linked

        def _browser_token(self):
            try:
                cookie = SimpleCookie(self.headers.get("Cookie", "")).get(COOKIE)
                return cookie.value if cookie else ""
            except CookieError:
                return ""

        def _account(self):
            auth = self.headers.get("Authorization", "")
            if auth:
                return pairing.account(auth.removeprefix("Bearer "), "mod") if auth.startswith("Bearer ") else None
            return pairing.account(self._browser_token(), "browser")

        def _linked_api(self, identity):
            account, name = identity
            if account == pairing.owner:
                return LinkedApi(owner_api.s, owner_api.s.tracked[0])
            return LinkedApi(accounts.for_account(account, name), name)

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            if url.path.startswith("/api/v1/") and public_v1 is not None:
                return self._v1(url.path[len("/api/v1/"):], urllib.parse.parse_qs(url.query))
            path, query, linked = self._route()
            if path is None:
                return self._send(404)
            try:
                if linked:
                    if urllib.parse.urlsplit(self.path).path == "/market/link":
                        return self._send(308, headers=(("Location", "/market/link/"),))
                    identity = self._account()
                    if not path.startswith("/api/"):
                        return self._static(path)      # the whole site for everyone; linking is a page in it
                    if not identity:
                        if path == "/api/account" or public_api.personal(path[5:]):
                            return self._json({"error": "Link your Minecraft account"}, 401)
                        return self._api_get(public_api, path[5:], query)
                    if path == "/api/account":
                        return self._json({"player": identity[1]})
                    api = self._linked_api(identity)
                    return self._api_get(api, path[5:], query)
                if path.startswith("/api/"):
                    return self._api_get(owner_api, path[5:], query)
                return self._static(path)
            except Exception as e:      # a page should fail alone, not take the service with it
                print("market GET failed", type(e).__name__, flush=True)
                self._json({"error": "failed"}, 500)

        def _v1(self, path, query):
            # Open to any page (no cookies are read here) and cached briefly: prices move every 30 s.
            headers = (("Access-Control-Allow-Origin", "*"),)
            send = lambda code, body: self._send(code, json.dumps(body, separators=(",", ":"), default=str).encode("utf-8"),
                                                 cache="public, max-age=30" if code == 200 else "no-store", headers=headers)
            if not (self.headers.get("User-Agent") or "").strip():
                return send(400, {"error": "Send a User-Agent that names your project."})
            try:
                code, body = public_v1.handle(path, query)
            except Exception as e:
                print("v1 GET failed", type(e).__name__, flush=True)
                code, body = 500, {"error": "failed"}
            return send(code, body)

        def _api_get(self, api, path, query):
            if isinstance(api, LinkedApi) and path in ("home", "flips", "trades", "positions"):
                query = dict(query, player=[api.player_name])
            fixed = {"status": api.status, "home": api.home, "flips": api.flips, "recent": api.recent, "positions": api.positions,
                     "trades": api.trades, "deals": api.deals, "leaderboard": api.leaderboard, "search": api.search, "bazaar": api.bazaar,
                     "crafts": api.crafts, "reported": api.reported, "watches": api.watches,
                     "notification-settings": lambda _: api.s.notification_settings()}
            if path in fixed:
                with api.s.lock:
                    return self._json(fixed[path](query))
            if path == "prices/change":     # takes the lock itself, only for as long as it prices
                return self._json(api.price_changes(query))
            m = re.match(r"^crafts/([a-z0-9_\-']+)$", path)
            if m:
                return self._json(api.craft(m.group(1), query))
            m = re.match(r"^item/([a-z0-9_\-']+)$", path)
            if m:
                return self._json(api.item(m.group(1), query))
            m = re.match(r"^item/([a-z0-9_\-']+)/history$", path)
            if m:
                return self._json(api.history(m.group(1), query))
            m = re.match(r"^item/([a-z0-9_\-']+)/auctions$", path)
            if m:
                return self._json(api.auctions(m.group(1), query))
            m = re.match(r"^auction/([A-Za-z0-9_\-]{1,64})$", path)
            if m:
                found = api.auction(m.group(1))
                return self._json(found if found else {"error": "no such auction"}, 200 if found else 404)
            m = re.match(r"^player/([A-Za-z0-9_]{1,16})/auctions$", path)
            if m:
                return self._json(api.player_auctions(m.group(1), query))
            m = re.match(r"^player/([A-Za-z0-9_]{1,16})/bids$", path)
            if m:
                return self._json(api.player_bids(m.group(1), query))
            m = re.match(r"^player/([A-Za-z0-9_]{1,16})$", path)
            if m:
                return self._json(api.player(m.group(1), query))
            m = re.match(r"^bazaar/([a-z0-9_\-]+)$", path)
            if m:
                return self._json(api.bazaar_tape(m.group(1), query))
            m = re.match(r"^scan/([A-Za-z0-9_]{1,16})$", path)
            if m:
                return self._json(api.scan(m.group(1)))
            self._json({"error": "no such thing"}, 404)

        def _static(self, path):
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            m = re.match(r"^pictures/([a-z0-9_]+)\.png$", name)
            if m:
                return self._picture(m.group(1))
            if not re.match(r"^[a-z0-9_\-]+\.[a-z]+$", name):
                return self._send(404)
            full = os.path.join(SITE, name)
            if not os.path.isfile(full):
                return self._send(404)
            with open(full, "rb") as f:
                body = f.read()
            kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if name.endswith((".html", ".js", ".css")):
                kind += "; charset=utf-8"
            self._send(200, body, kind, "no-store" if name in ("index.html", "link.html") else "no-cache")

        def _picture(self, entry):
            """A picture drawn from Dragsim's pack. Only ids the service drew are served, so the name
            in the path never reaches the file system unchecked."""
            service = owner_api.s
            if entry not in service.pictures:
                return self._send(404)
            try:
                with open(os.path.join(service.pictures_dir, entry + ".png"), "rb") as f:
                    body = f.read()
            except OSError:
                return self._send(404)
            self._send(200, body, "image/png", "max-age=3600")

        def _linked_post(self, path, body):
            origin = self.headers.get("Origin")
            if origin and origin != pairing.origin:
                raise LinkError(403, "Request must come from DSM")
            if path == "/api/start":
                return self._json(pairing.start(body))
            if path == "/api/verify":
                reply = pairing.finish(body)
                account, player = pairing.account(reply["token"], "mod")
                if account != pairing.owner:
                    owner_api.s.update_watch_player(account, player)
                return self._json(reply)
            if path in ("/api/pair", "/api/logout"):
                if path == "/api/pair":
                    token, lifetime = pairing.pair(body), BROWSER_SECONDS
                else:
                    pairing.logout(self._browser_token())
                    token, lifetime = "", 0
                cookie = f"{COOKIE}={token}; Path=/market/link/; Max-Age={lifetime}; Secure; HttpOnly; SameSite=Strict"
                return self._json({"ok": True}, headers=(("Set-Cookie", cookie),))
            identity = self._account()
            if not identity:
                raise LinkError(401, "Link your Minecraft account")
            # Browser sessions manage site settings. Only the mod credential can upload game events.
            if path in ("/api/trade", "/api/event") and not self.headers.get("Authorization", "").startswith("Bearer "):
                raise LinkError(403, "A linked mod is required for uploads")
            return self._post_api(self._linked_api(identity), path, body)

        def _post_api(self, api, path, body):
            if path == '/api/nw-quote':
                if api is not owner_api or self.client_address[0] not in ('127.0.0.1', '::1'):
                    return self._send(404)
                from .networth import batch
                return self._json(batch(api.s, body))
            routes = {"/api/quote": api.quote, "/api/trade": api.trade, "/api/event": api.event,
                      "/api/watch": api.save_watch, "/api/watch/delete": api.drop_watch,
                      "/api/notification-settings": api.s.save_notification_settings}
            if path not in routes:
                return self._send(404)
            return self._json(routes[path](body))

        def do_POST(self):
            path, _, linked = self._route()
            if path is None:
                return self._send(404)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                max_size = 524_288 if linked else 4_000_000
                if linked and path in ("/api/start", "/api/verify", "/api/pair", "/api/logout"):
                    max_size = 4096
                if length < 0 or length > max_size:
                    self.close_connection = True
                    return self._send(413)
                if linked and self.headers.get_content_type() != "application/json":
                    self.close_connection = True
                    return self._send(415)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    return self._json({"error": "Expected an object"}, 400)
                return self._linked_post(path, body) if linked else self._post_api(owner_api, path, body)
            except LinkError as e:
                self._json({"error": str(e)}, e.status)
            except (ValueError, KeyError, TypeError):
                self._json({"error": "bad request"}, 400)
            except Exception as e:
                print("market POST failed", type(e).__name__, flush=True)
                self._json({"error": "failed"}, 500)

    return Handler


class Server(ThreadingHTTPServer):
    """Python's server lets only 5 connections wait to be picked up. Measured on 2026-11-11 with real
    54-item hover batches: 10 players at once already stalled one for a second, and 50 at once got a
    connection reset, while each batch alone took 9 ms. The queue, not the work, was the limit."""
    request_queue_size = 256
    daemon_threads = True


def serve(service, key, host="127.0.0.1", port=8793):
    folder = os.path.dirname(service.own_path)
    config_path = os.path.join(folder, "link.json")
    pairing = accounts = None
    if os.path.exists(config_path):
        from .account_tracker import AccountTrackers

        with open(config_path, encoding="utf-8") as file:
            config = json.load(file)
        pairing = Pairing(os.path.join(folder, "linked-auth.db"), config["origin"], config.get("owner"))
        accounts = AccountTrackers(service)
    server = Server((host, port), make_handler(Api(service, key), pairing, accounts))
    server.serve_forever()
