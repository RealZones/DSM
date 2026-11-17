"""The whole market in memory: built by replaying history, then kept current by following it.

Replay and live use the same code path on purpose. A rule that differs between the two is a rule
the backtest never tested, and everything here was chosen by that backtest.
"""
import gzip
import json
import math
import os
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import closing

from . import engine as E
from . import fees
from . import pictures
from . import replay
from .bazaar import Bazaar
from .crafts import Crafts
from .discord import Discord, short
from .items import FILTERS, LEARNED, Catalog, from_json, learned_row, passes, plain
from .lore import describe
from .reported import Reports
from .tape import Fill, Tape, summary
from .tracker import Tracker
from .webhook import Webhooks, validate_url

DAY = 86_400_000
QUOTE_DAYS = 9                  # enough for the 7 day bazaar window to be full at start
CATALOG_URL = "https://dragsim.co/data/catalog.json"
CATALOG_EVERY = 6 * 3600
PACK_URL = "https://dragsim.co/textures/dragsim.zip"
PACK_EVERY = 6 * 3600           # about 40 MB, fetched again only when its ETag changes
PROFILES_EVERY = 3600           # archive.py saves the top 100 profiles nightly, so hourly is plenty
BOT_EVERY = 3600                # the bot's index.json is rebuilt hourly by dragsim-index.timer
BACKUPS_KEPT = 30
SCAN_SECONDS = 60               # dragsim.co caches profiles about this long, so sooner gains nothing
MAX_DEAL_AGE = 2 * DAY
MIN_DEAL_GAP = 0.15             # 76% profitable after the fee on unseen days, +18.3% median
CRAFT_SECONDS = 60              # the book moves every 30, and this runs under the lock the poll needs
HIT_MINUTES = 10                # how long the mod has to pick up a notifier hit
HIT_LINES = 5                   # a price set above the market would otherwise send a line per listing
LINKED_WATCH_LIMIT = 50          # each watch adds work to the shared poll


class Service:
    def __init__(self, source, catalog_path, recipes_path, own_path, tracked=()):
        self.source = source
        self.catalog_path = catalog_path
        self.recipes_path = recipes_path
        self.own_path = own_path
        folder = os.path.dirname(own_path)
        self.tape_path = os.path.join(folder, "bazaar.db")
        self.tape = Tape()
        self.tracked = tuple(tracked)
        self.lock = threading.RLock()
        self.catalog = Catalog.load(catalog_path)
        self.learned_path = os.path.join(os.path.dirname(os.path.abspath(catalog_path)), LEARNED)
        self.pack_path = os.path.join(os.path.dirname(self.learned_path), "dragsim-pack.zip")
        self.pictures_dir = os.path.join(os.path.dirname(self.learned_path), "pictures")
        self.pictures = frozenset()     # unlisted ids with a picture from Dragsim's pack; replaced, never changed
        self._drawn = None              # (pack time, ids) the pictures were last drawn for
        self.bot = {}                   # entry -> the bot's price, for what nothing traded prices (bot_prices)
        self._unseen = {}               # id -> one raw copy, for ids the catalog does not have yet
        self._profiles_at = 0           # newest saved profile already read for new items
        self.players = frozenset()      # everyone seen on the AH, for player search; replaced, never changed
        with open(recipes_path, encoding="utf-8") as f:
            self.recipes = json.load(f)
        shop_path = os.path.join(folder, "shop.json")
        self.shop = {}
        if os.path.exists(shop_path):
            with open(shop_path, encoding="utf-8") as f:
                self.shop = json.load(f)
        self.reports = Reports.load(os.path.join(folder, "reported.json"), self.catalog)
        self.discord = Discord.load(os.path.join(folder, "discord.json"))
        self.webhooks = Webhooks(self._webhook_destination, self._disable_webhook)
        self._board = None
        self.engine = None
        self.tracker = None
        self.listing = {}
        self.deals = {}
        self.cursor = dict(sale=0, listing=0, ended=0, quote=0)
        self.built_at = 0
        self.last_poll = 0
        self._scans = {}
        self._own()

    # ------------------------------------------------------------------ his own records

    def _own(self):
        db = sqlite3.connect(self.own_path)
        os.chmod(self.own_path, 0o600)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, at INTEGER, player TEXT, other TEXT, got TEXT, gave TEXT, source TEXT, detail TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER, player TEXT, kind TEXT, data TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS watches (id INTEGER PRIMARY KEY AUTOINCREMENT, entry TEXT, side TEXT, price REAL, filters TEXT, created INTEGER, account TEXT NOT NULL DEFAULT '', player TEXT)")
        # Existing watches belong to the owner. Their ids also own the hit history.
        columns = {row[1] for row in db.execute("PRAGMA table_info(watches)")}
        if "account" not in columns:
            db.execute("ALTER TABLE watches ADD COLUMN account TEXT NOT NULL DEFAULT ''")
        if "player" not in columns:
            db.execute("ALTER TABLE watches ADD COLUMN player TEXT")
        db.execute("CREATE INDEX IF NOT EXISTS watches_account ON watches (account, id)")
        db.execute("CREATE TABLE IF NOT EXISTS watch_hits (watch INTEGER, ref TEXT, at INTEGER, unit REAL, count INTEGER, seller TEXT, sold INTEGER, PRIMARY KEY (watch, ref))")
        db.execute("CREATE TABLE IF NOT EXISTS notification_settings (account TEXT PRIMARY KEY, in_game INTEGER NOT NULL, webhook TEXT, webhook_enabled INTEGER NOT NULL, discord_dm INTEGER NOT NULL)")
        db.commit()
        db.close()
        tape = sqlite3.connect(self.tape_path)
        tape.execute("PRAGMA journal_mode=WAL")
        tape.execute("CREATE TABLE IF NOT EXISTS fills (ts INTEGER, product TEXT, side TEXT, price REAL, amount REAL, proof TEXT, orders INTEGER)")
        tape.execute("CREATE INDEX IF NOT EXISTS fills_product ON fills (product, ts)")
        tape.commit()
        tape.close()

    def own_trades(self):
        db = sqlite3.connect(self.own_path)
        rows = db.execute("SELECT id, at, player, other, got, gave, detail FROM trades ORDER BY at").fetchall()
        db.close()
        return [dict(ref=r[0], at=r[1], player=r[2], other=r[3], got=json.loads(r[4]), gave=json.loads(r[5]),
                     detail=json.loads(r[6]) if r[6] else None) for r in rows]

    def add_trade(self, trade):
        """Stores a receipt once. A trade can arrive from a log and from the mod, a second apart
        and with different ids, so a repeat is spotted by what it says, not by its id."""
        lines = hash_lines(trade)
        db = sqlite3.connect(self.own_path)
        for ref, got, gave in db.execute("SELECT id, got, gave FROM trades WHERE other = ? AND at BETWEEN ? AND ?",
                                         (trade["other"], trade["at"] - 60_000, trade["at"] + 60_000)).fetchall():
            if hash_lines({"got": json.loads(got), "gave": json.loads(gave)}) == lines:
                db.close()
                return ref, False
        ref = trade.get("ref") or f"{trade['player']}:{trade['other']}:{trade['at']}:{lines}"
        db.execute("INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?,?,?)",
                   (ref, trade["at"], trade["player"], trade["other"], json.dumps(trade.get("got") or []),
                    json.dumps(trade.get("gave") or []), trade.get("source", "mod"),
                    json.dumps(trade["detail"]) if trade.get("detail") else None))
        db.commit()
        db.close()
        return ref, True

    def add_event(self, event):
        """Anything else the mod saw, kept as it came: upgrades, AH lines, a window dump. Stored
        first and read later, since what can be done with a message depends on messages not yet
        seen, and a dropped event cannot be recovered."""
        at = int(event.get("at") or time.time() * 1000)
        db = sqlite3.connect(self.own_path)
        db.execute("INSERT INTO events (at, player, kind, data) VALUES (?,?,?,?)",
                   (at, event.get("player"), event.get("kind"), json.dumps(event.get("data"))))
        db.commit()
        db.close()
        if event.get("kind") == "chat" and self.tracker:
            with self.lock:
                self.tracker.on_chat(event.get("player"), at, (event.get("data") or {}).get("text", ""))

    def own_events(self):
        db = sqlite3.connect(self.own_path)
        rows = db.execute("SELECT at, player, data FROM events WHERE kind = 'chat' ORDER BY at").fetchall()
        db.close()
        return [dict(at=r[0], player=r[1], text=(json.loads(r[2]) or {}).get("text", "")) for r in rows]

    def backup(self):
        """His trades exist nowhere else once the game logs rotate. A real snapshot, because a
        plain file copy can catch the database between two writes."""
        folder = os.path.join(os.path.dirname(self.own_path), "backups")
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, time.strftime("own-%Y%m%d.db", time.gmtime()))
        source = sqlite3.connect(self.own_path)
        copy = sqlite3.connect(target)
        os.chmod(target, 0o600)
        with copy:
            source.backup(copy)
        copy.close()
        source.close()
        for old in sorted(name for name in os.listdir(folder) if name.startswith("own-"))[:-BACKUPS_KEPT]:
            os.remove(os.path.join(folder, old))

    # ------------------------------------------------------------------ the notifier

    @staticmethod
    def _notification_options(db, account):
        row = db.execute("SELECT in_game, webhook, webhook_enabled, discord_dm FROM notification_settings WHERE account = ?",
                         (account or "",)).fetchone()
        return row if row is not None else (1, None, 0, int(not account))

    def notification_settings(self, *, account=None):
        with closing(sqlite3.connect(self.own_path)) as db:
            in_game, webhook, webhook_enabled, discord_dm = self._notification_options(db, account)
        dm_available = not account and self.discord is not None
        return dict(in_game=bool(in_game), webhook_configured=bool(webhook), webhook_enabled=bool(webhook and webhook_enabled),
                    discord_dm_available=dm_available, discord_dm=bool(dm_available and discord_dm))

    def save_notification_settings(self, body, *, account=None):
        for flag in ("in_game", "webhook_enabled", "discord_dm"):
            if flag in body and not isinstance(body[flag], bool):
                raise ValueError(f"{flag} must be true or false")
        if body.get("discord_dm") and (account or self.discord is None):
            raise ValueError("no verified Discord DM recipient")
        with self.lock, closing(sqlite3.connect(self.own_path)) as db, db:
            in_game, webhook, webhook_enabled, discord_dm = self._notification_options(db, account)
            if "webhook" in body:
                webhook = validate_url(body["webhook"])
                webhook_enabled = bool(webhook)
            webhook_enabled = body.get("webhook_enabled", webhook_enabled)
            if webhook_enabled and not webhook:
                raise ValueError("add a Discord webhook before enabling it")
            db.execute("INSERT OR REPLACE INTO notification_settings VALUES (?,?,?,?,?)",
                       (account or "", body.get("in_game", in_game), webhook, webhook_enabled, body.get("discord_dm", discord_dm)))
        return self.notification_settings(account=account)

    def _webhook_destination(self, account):
        try:
            with closing(sqlite3.connect(self.own_path)) as db:
                _, webhook, enabled, _ = self._notification_options(db, account)
            return webhook if enabled else None
        except sqlite3.Error:
            print("could not read Discord webhook setting", flush=True)
            return None

    def _disable_webhook(self, account, url):
        # A deleted webhook stays quiet until replaced or explicitly enabled again.
        try:
            with self.lock, closing(sqlite3.connect(self.own_path)) as db, db:
                db.execute("UPDATE notification_settings SET webhook_enabled = 0 WHERE account = ? AND webhook = ?",
                           (account or "", url))
        except sqlite3.Error:
            print("could not disable rejected Discord webhook", flush=True)

    def _watch_rows(self, account=None):
        query = "SELECT account, player, id, entry, side, price, filters FROM watches"
        with closing(sqlite3.connect(self.own_path)) as db:
            rows = db.execute(query + " ORDER BY id") if account is None else db.execute(query + " WHERE account = ? ORDER BY id", (account,))
            return [(owner, player, dict(id=ref, entry=entry, side=side, price=price, filters=json.loads(filters or "{}")))
                    for owner, player, ref, entry, side, price, filters in rows]

    def watches(self, *, account=None):
        return [watch for _, _, watch in self._watch_rows(account or "")]

    def save_watch(self, body, *, account=None, player=None):
        """Tracks an item, or changes one already tracked. No price means watch it and stay quiet."""
        entry = str(body.get("entry") or "").lower()
        if entry not in self.catalog.items:
            raise ValueError("no such item")
        if account and not player:
            raise ValueError("no player linked to the watch")
        side = "above" if body.get("side") == "above" else "below"
        price = float(body["price"]) if body.get("price") else None
        if price is not None and (not math.isfinite(price) or price <= 0):
            raise ValueError("watch price must be positive and finite")
        filters = {k: str(v) for k, v in (body.get("filters") or {}).items() if k in FILTERS and v}
        with self.lock, closing(sqlite3.connect(self.own_path)) as db, db:
            if body.get("id"):
                ref = int(body["id"])
                changed = db.execute("UPDATE watches SET side = ?, price = ? WHERE id = ? AND account = ?",
                                     (side, price, ref, account or "")).rowcount
                if not changed:
                    raise ValueError("no such watch")
                # A new price is a new question, so what the old one caught should not linger.
                db.execute("DELETE FROM watch_hits WHERE watch = ?", (ref,))
                return ref
            if account:
                count = db.execute("SELECT COUNT(*) FROM watches WHERE account = ?", (account,)).fetchone()[0]
                if count >= LINKED_WATCH_LIMIT:
                    raise ValueError(f"you can track up to {LINKED_WATCH_LIMIT} items")
            return db.execute("INSERT INTO watches (entry, side, price, filters, created, account, player) VALUES (?,?,?,?,?,?,?)",
                              (entry, side, price, json.dumps(filters), int(time.time() * 1000), account or "", player)).lastrowid

    def drop_watch(self, ref, *, account=None):
        with self.lock, closing(sqlite3.connect(self.own_path)) as db, db:
            removed = db.execute("DELETE FROM watches WHERE id = ? AND account = ?", (ref, account or "")).rowcount
            if removed:
                db.execute("DELETE FROM watch_hits WHERE watch = ?", (ref,))

    def update_watch_player(self, account, player):
        """Keep the market's seller name in sync when a linked Minecraft account changes names."""
        if not account:
            return
        with self.lock, closing(sqlite3.connect(self.own_path)) as db, db:
            db.execute("UPDATE watches SET player = ? WHERE account = ? AND player IS NOT ?", (player, account, player))

    def listed(self, entry, wanted, *, player=None):
        """Live listings of an item, cheapest first, as (unit, count, seller, id, end). Your own
        are left out, since being told about your own listing helps nobody."""
        now = self.engine.now
        own = {name.casefold() for name in (self.tracked if player is None else (player,))}
        rows = [(unit, count, seller, lid, end) for lid, (item, unit, created, end, count, seller) in self.listing.items()
                if item.entry == entry and (not end or end > now) and (seller or "").casefold() not in own and passes(item, wanted)]
        return sorted(rows, key=lambda r: r[0])

    def watch_matches(self, watch, *, player=None):
        """What can be bought at your price this minute."""
        if watch["side"] != "below" or not watch["price"]:
            return []
        out = [dict(id=lid, unit=unit, count=count, seller=seller, end=end)
               for unit, count, seller, lid, end in self.listed(watch["entry"], watch["filters"], player=player) if unit <= watch["price"]]
        book = self.engine.bazaar.books.get(watch["entry"]) if self.engine.bazaar else None
        if book and book.last:
            out += [dict(id=f"bazaar:{price}", unit=price, count=amount, seller=None, end=0) for price, amount in sorted(book.last[3].items())
                    if amount > 0 and price <= watch["price"]]
        return out

    def check_watches(self, events):
        """Run after each poll. A hit is written once, keyed by the listing or sale, so a cheap
        listing that stays up for a week is one alert. Sales count too: a good listing can come
        and go between polls, and knowing one sold under your price is worth having."""
        watches = [(account, player, watch) for account, player, watch in self._watch_rows() if watch["price"]]
        if not watches:
            return
        now = self.engine.now
        rows = []
        for _, player, watch in watches:
            for match in self.watch_matches(watch, player=player):
                rows.append((watch["id"], match["id"], now, match["unit"], match["count"], match["seller"], 0))
            for event in events:
                if event[2] != "sale" or event[3][1] != watch["entry"]:
                    continue
                sid, entry, sold, created, unit, count, seller, buyer, raw = event[3]
                if not passes(from_json(raw), watch["filters"]):
                    continue
                if (unit <= watch["price"]) if watch["side"] == "below" else (unit >= watch["price"]):
                    rows.append((watch["id"], sid, sold, unit, count, seller, 1))
        if not rows:
            return
        db = sqlite3.connect(self.own_path)
        fresh = [row for row in rows if db.execute("INSERT OR IGNORE INTO watch_hits VALUES (?,?,?,?,?,?,?)", row).rowcount]
        db.commit()
        db.close()
        if fresh:
            self._deliver_hits(fresh, watches)

    def _deliver_hits(self, fresh, watches):
        by_id = {watch["id"]: (account, watch) for account, _, watch in watches}
        grouped = {}
        for row in fresh:
            account, _ = by_id[row[0]]
            grouped.setdefault(account, []).append(row)
        with closing(sqlite3.connect(self.own_path)) as db:
            for account, rows in grouped.items():
                _, webhook, enabled, dm = self._notification_options(db, account)
                send_dm = not account and dm and self.discord is not None
                if not send_dm and not (webhook and enabled):
                    continue
                lines = self.hit_lines(rows, {row[0]: by_id[row[0]][1] for row in rows})
                if not lines:
                    continue
                if send_dm:
                    self.discord.send(lines)
                if webhook and enabled:
                    self.webhooks.send(account, webhook, lines)

    def hit_lines(self, rows, watches):
        """What a phone should hear. A listing under your price, or a sale over it. A sale under
        your price is gone already, so it stays on the page, or a busy item would buzz all day."""
        lines = []
        counted = {}
        for ref, _, _, unit, count, seller, sold in rows:
            watch = watches[ref]
            if sold and watch["side"] == "below":
                continue
            counted[ref] = counted.get(ref, 0) + 1
            if counted[ref] > HIT_LINES:
                continue
            name = self.catalog.name(watch["entry"])
            price = short(unit) + (f" x{count:,}" if count > 1 else "")
            yours = f"your price {short(watch['price'])}"
            if sold:
                lines.append(f"**{name}** sold for **{price}**, {yours}")
            elif seller:
                lines.append(f"**{name}** listed at **{price}**, {yours}. `/ah {seller}`")
            else:
                lines.append(f"**{name}** offered at **{price}** on the bazaar, {yours}")
        lines += [f"and {n - HIT_LINES} more {self.catalog.name(watches[ref]['entry'])}" for ref, n in counted.items() if n > HIT_LINES]
        return lines

    def watch_hits(self, ref, limit=6, *, account=None):
        with closing(sqlite3.connect(self.own_path)) as db:
            rows = db.execute("SELECT h.ref, h.at, h.unit, h.count, h.seller, h.sold FROM watch_hits h JOIN watches w ON w.id = h.watch "
                              "WHERE w.id = ? AND w.account = ? ORDER BY h.at DESC LIMIT ?", (ref, account or "", limit)).fetchall()
        return [dict(ref=r[0], at=r[1], unit=r[2], count=r[3], seller=r[4], sold=bool(r[5])) for r in rows]

    def recent_hits(self, *, account=None):
        """For the mod. Only what can still be bought, since a chat line about a sale you missed
        just makes noise while you are playing."""
        with closing(sqlite3.connect(self.own_path)) as db:
            rows = db.execute("SELECT w.id, w.entry, w.side, w.price, h.ref, h.unit, h.count, h.seller FROM watch_hits h JOIN watches w ON w.id = h.watch "
                              "LEFT JOIN notification_settings n ON n.account = w.account "
                              "WHERE w.account = ? AND COALESCE(n.in_game, 1) = 1 AND h.at >= ? AND h.sold = 0 AND h.seller IS NOT NULL ORDER BY h.at",
                              (account or "", self.engine.now - HIT_MINUTES * 60_000)).fetchall()
        return [dict(id=f"w{r[0]}:{r[4]}", entry=r[1], name=self.catalog.name(r[1]), side=r[2], price=r[3], unit=r[5], count=r[6], seller=r[7]) for r in rows]

    # ------------------------------------------------------------------ building

    def build(self, now=None):
        """Replays everything into fresh objects, then swaps them in, so a page being read while
        this runs sees the old state and not half of the new one."""
        source = replay.connect(self.source)
        started = time.time()
        latest = source.execute("SELECT max(sold_at) FROM sales").fetchone()[0] or 0
        since = latest - QUOTE_DAYS * DAY
        events = replay.events(source, since=since)
        cursor = dict(
            sale=source.execute("SELECT max(recorded_at) FROM sales").fetchone()[0] or 0,
            listing=source.execute("SELECT max(first_seen) FROM listings").fetchone()[0] or 0,
            ended=source.execute("SELECT max(last_seen) FROM listings WHERE outcome IS NOT NULL").fetchone()[0] or 0,
            quote=source.execute("SELECT max(ts) FROM quotes").fetchone()[0] or 0)
        players = frozenset(name for (name,) in source.execute(
            "SELECT seller FROM sales UNION SELECT buyer FROM sales UNION SELECT seller FROM listings") if name)
        source.close()
        trades = [(t["at"], 2, "trade", t) for t in self.own_trades()]
        chat = [(e["at"], 1, "chat", e) for e in self.own_events()]
        fills = [(f[0], 0, "fill", f) for f in self.tape_rows(since)]
        events = sorted(events + trades + chat + fills, key=lambda e: (e[0], e[1]))
        bazaar = Bazaar(self.catalog.npc)
        bazaar.tape_from = fills[0][0] if fills else None
        engine = E.Engine(self.catalog, E.Tuned(), self.recipes, bazaar)
        engine.shop = self.shop
        engine.bot = self.bot = self.bot_prices()
        # Twice, because bundles on your own trades need these while the market is still being
        # replayed, and only the finished market can say what the AH thinks of what is in them.
        engine.reported = self.reports.solve()
        tracker = Tracker(engine, self.tracked)
        listing = {}
        deals = {}
        for event in events:
            self._feed(engine, tracker, listing, deals, event)
        fresh = self.learn()
        if fresh:
            # Before the receipts are solved again, so one naming a new item finds it.
            engine.catalog = self.reports.catalog = fresh
        engine.reported = self.reports.solve(engine.market_price)
        engine._part_cache.clear()
        deals = self.find_deals(engine, listing)
        with self.lock:
            self.engine, self.tracker, self.listing, self.deals = engine, tracker, listing, deals
            self.players = players
            if fresh:
                self.catalog = fresh
            self._board = None
            self.cursor = cursor
            self.built_at = int(time.time() * 1000)
        return time.time() - started, len(events)

    def _feed(self, engine, tracker, listing, deals, event):
        ts, _, kind, row = event
        if kind == "sale":
            sid, entry, sold, created, unit, count, seller, buyer, raw = row
            item = from_json(raw)
            self._spot(item, raw)
            engine.now = max(engine.now, sold)
            est = engine.estimate(item, exclude=sid)
            info = listing.pop(sid, None)
            tracker.on_sale(sid, item, unit, count, seller, buyer, sold, created, info[3] if info else None, est)
            engine.add_sale(sid, item, unit, seller, buyer, sold, created)
        elif kind == "list":
            lid, entry, created, end, unit, count, seller, raw = row
            if lid in engine.sold_ids:
                return      # the sale got here first, so this one is already gone
            item = from_json(raw)
            self._spot(item, raw)
            listing[lid] = (item, unit, created, end, count, seller)
            engine.add_listing(lid, item, unit, seller, ts, end or ts, count)
        elif kind == "unlist":
            info = listing.pop(row, None)
            engine.remove_listing(row)
            if info:
                item, unit, created, end, count, seller = info
                tracker.on_unlisted(seller, item, unit * count, ts, end, created)
        elif kind == "trade":
            engine.now = max(engine.now, ts)
            tracker.on_trade(row)
        elif kind == "chat":
            tracker.on_chat(row["player"], ts, row["text"])
        elif kind == "fill":
            engine.bazaar.add_fill(ts, row[1], row[3], row[4], row[2])
        else:
            replay.apply(engine, event)

    def find_deals(self, engine, listing):
        """Checked afresh on every poll, because a deal stops being one when the next BIN moves or
        the cheap copy sells, and only recent listings count. The test that earned the rule
        looked at listings as they appeared. One that has sat for two days has been seen and
        passed over by everyone else, usually for a reason this cannot see."""
        now = engine.now
        groups = {}
        for lid, (item, unit, created, end, count, seller) in listing.items():
            if now - created <= MAX_DEAL_AGE:
                groups.setdefault(item.pricing_identity(), []).append((unit, lid, item, created, end, count, seller))
        out = {}
        for rows in groups.values():
            rows.sort(key=lambda r: r[0])
            for rank, (unit, lid, item, created, end, count, seller) in enumerate(rows):
                est = engine.estimate(item, exclude=lid)
                found = E.deal(est, unit, count, MIN_DEAL_GAP)
                if not found:
                    break       # dearer copies of the same item can only be worse
                # Copies compete, so each extra one is worth less.
                target = found["target"] / 1.05 ** rank
                profit = fees.net_of_fee(target * count) - unit * count
                if profit <= 0:
                    break
                out[lid] = dict(found, target=target, profit=profit, roi=profit / (unit * count), id=lid, entry=item.entry, name=item.label(),
                                rarity=item.rarity, unit=unit, count=count, seller=seller, at=created, end=end, confidence=est["confidence"],
                                median=est["median"], volume=est["volume"], time_to_sell=est["time_to_sell"])
        return out

    # ------------------------------------------------------------------ staying current

    def poll(self):
        """Reads what the collector added since last time. It writes every 30 seconds, and only
        7-29% of good listings go inside 10 minutes, so there is nothing to gain from racing."""
        source = replay.connect(self.source)
        cur = self.cursor
        events = []
        for row in source.execute("SELECT ts, product, best_bid, best_ask, buy_quantity, sell_quantity, traded, book FROM quotes "
                                  "WHERE ts > ? AND book IS NOT NULL ORDER BY ts", (cur["quote"],)):
            events.append((row[0], 0, "quote", row))
            cur["quote"] = max(cur["quote"], row[0])
        for lid, entry, created, end, unit, count, bin_, seller, item, first in source.execute(
                "SELECT id, entry, created_at, end_at, unit_price, count, bin, seller, item, first_seen FROM listings "
                "WHERE first_seen > ?", (cur["listing"],)):
            cur["listing"] = max(cur["listing"], first or 0)
            if bin_ and unit:
                events.append((first or created, 1, "list", (lid, entry, created, end, unit, count or 1, seller, item)))
        # Reconciled against the collector's whole live set rather than a cursor. One missed ending
        # would leave a ghost LBIN capping that item's price until the next restart.
        live = {row[0] for row in source.execute("SELECT id FROM listings WHERE outcome IS NULL")}
        gone = [lid for lid in self.listing if lid not in live]
        for start in range(0, len(gone), 400):
            part = gone[start:start + 400]
            marks = ",".join("?" * len(part))
            for lid, last, outcome in source.execute(f"SELECT id, last_seen, outcome FROM listings WHERE id IN ({marks})", part):
                if outcome != "sold":      # a sold one leaves through its sale, below
                    events.append((last or int(time.time() * 1000), 2, "unlist", lid))
        for sid, entry, sold, created, unit, count, seller, buyer, item, recorded in source.execute(
                "SELECT id, entry, sold_at, created_at, unit_price, count, seller, buyer, item, recorded_at FROM sales "
                "WHERE recorded_at > ? ORDER BY sold_at", (cur["sale"],)):
            cur["sale"] = max(cur["sale"], recorded or 0)
            if unit:
                events.append((sold, 3, "sale", (sid, entry, sold, created, unit, count or 1, seller, buyer, item)))
        snapshot = source.execute("SELECT v FROM meta WHERE k = 'market_snapshot'").fetchone()
        source.close()
        fresh = self.read_tape(snapshot[0]) if snapshot else []
        events += [(f.ts, 0, "fill", f.row()) for f in fresh]
        events.sort(key=lambda e: (e[0], e[1]))
        with self.lock:
            if fresh and self.engine.bazaar.tape_from is None:
                self.engine.bazaar.tape_from = fresh[0].ts
            for event in events:
                self._feed(self.engine, self.tracker, self.listing, self.deals, event)
            fresh = self.learn()
            if fresh:
                self._use_catalog(fresh)
            names = {row[6] for _, _, kind, row in events if kind in ("sale", "list")}
            names |= {row[7] for _, _, kind, row in events if kind == "sale"}
            names.discard(None)
            if not names <= self.players:
                # A new set rather than an added name, so a search reading the old one is never disturbed.
                self.players = self.players | names
            self.engine.now = max(self.engine.now, int(time.time() * 1000))
            self.deals = self.find_deals(self.engine, self.listing)
            self.check_watches(events)
            self.last_poll = int(time.time() * 1000)
        return len(events)

    def read_tape(self, raw):
        """The collector keeps the whole market snapshot from its last poll, so the book can be
        watched every 30 seconds with no extra request to dragsim.co."""
        data = json.loads(raw)
        observed = (data.get("coverage") or {}).get("last_observed")
        if not isinstance(observed, (int, float)) or data.get("stale"):
            return []
        fills = self.tape.feed(int(observed), data.get("products") or [])
        if fills:
            db = sqlite3.connect(self.tape_path)
            db.executemany("INSERT INTO fills VALUES (?,?,?,?,?,?,?)", [f.row() for f in fills])
            db.commit()
            db.close()
        return fills

    def tape_rows(self, since=0, product=None, limit=None):
        db = sqlite3.connect(self.tape_path)
        query = "SELECT ts, product, side, price, amount, proof, orders FROM fills WHERE ts >= ?"
        args = [since]
        if product:
            query += " AND product = ?"
            args.append(product)
        query += " ORDER BY ts" + (" DESC LIMIT ?" if limit else "")
        if limit:
            args.append(limit)
        rows = db.execute(query, args).fetchall()
        db.close()
        return rows

    def bazaar_board(self):
        """Every product: the book now, and what the tape saw in the last 24 hours."""
        now = self.engine.now
        rows = [Fill(*r) for r in self.tape_rows(now - DAY)]
        by = {}
        for fill in rows:
            by.setdefault(fill.product, []).append(fill)
        out = []
        for product, book in self.engine.bazaar.books.items():
            done = summary(by.get(product, ()), now, DAY)
            out.append({"entry": product, "name": self.catalog.name(product), "tier": self.catalog.tier(product),
                        "bid": book.bid, "ask": book.ask, "price": self.engine.bazaar.price(product), "bid_qty": book.bid_qty,
                        "ask_qty": book.ask_qty, "bought": done["bought"], "sold": done["sold"], "unseen": done["unseen"]})
        out.sort(key=lambda r: -(r["bought"]["coins"] + r["sold"]["coins"]))
        return out

    def crafts(self):
        return Crafts(self.engine, self.recipes, self.shop, self.tracked)

    def craft_board(self):
        now = time.time()
        with self.lock:
            if not self._board or now - self._board[0] >= CRAFT_SECONDS:
                self._board = (now, self.crafts().board())
            return self._board[1]

    def refresh_catalog(self):
        """New items appear on Dragsim without notice, and an unknown name cannot be matched to a
        trade receipt. Written beside the old copy first, so a bad fetch changes nothing."""
        request = urllib.request.Request(CATALOG_URL, headers={"User-Agent": "dragsim-market"})
        with urllib.request.urlopen(request, timeout=60) as reply:
            data = json.load(reply)
        fresh = Catalog(data, self.catalog.extra)
        if len(fresh.items) < len(self.catalog.items) * 0.8:
            return      # a truncated reply, keep what works
        temp = self.catalog_path + ".new"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(temp, self.catalog_path)
        self._use_catalog(fresh)

    def fetch_pack(self):
        """Dragsim's resource pack, for the pictures (market/pictures.py). Asked for with its last ETag,
        so an unchanged pack costs one empty reply instead of 40 MB. Returns whether it changed."""
        headers = {"User-Agent": "dragsim-market"}
        tag_path = self.pack_path + ".etag"
        if os.path.exists(self.pack_path) and os.path.exists(tag_path):
            with open(tag_path, encoding="utf-8") as f:
                headers["If-None-Match"] = f.read().strip()
        temp = self.pack_path + ".new"
        try:
            with urllib.request.urlopen(urllib.request.Request(PACK_URL, headers=headers), timeout=300) as reply:
                with open(temp, "wb") as f:
                    shutil.copyfileobj(reply, f)
                tag = reply.headers.get("ETag") or ""
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return False
            raise
        with zipfile.ZipFile(temp) as pack:         # a cut-off download is not a pack
            if pack.testzip() is not None:
                raise ValueError("the pack failed its own checksum")
        os.replace(temp, self.pack_path)
        with open(tag_path, "w", encoding="utf-8") as f:
            f.write(tag)
        return True

    def draw_pictures(self):
        """Pictures for every item dragsim.co does not list that the pack has a model for. Drawn
        outside the lock; only new items or a new pack cost anything (pictures.draw keeps the rest)."""
        if not os.path.exists(self.pack_path):
            return
        with self.lock:
            catalog = self.catalog
        wanted = tuple(sorted(e for e in catalog.extra if e not in catalog.published))
        key = (os.path.getmtime(self.pack_path), wanted)
        if key == self._drawn:
            return
        self._drawn = key           # set first, so a pack that cannot be drawn is not retried every poll
        self.pictures = frozenset(pictures.draw(self.pack_path, wanted, self.pictures_dir))

    def keep_pictures(self, fetched_at):
        """Pictures are a nicety, so a failure here is printed and tried again later, never fatal to
        the poll. Returns when the pack was last asked for."""
        try:
            if time.time() - fetched_at > PACK_EVERY:
                fetched_at = time.time()
                self.fetch_pack()
            self.draw_pictures()
        except Exception as e:      # noqa: BLE001 - a bad pack or download must not stop the market
            print("pictures failed", repr(e), flush=True)
        return fetched_at

    def bot_prices(self):
        """The Discord bot's price for everything it has one (index.json beside the collector's
        database, rebuilt hourly by build_index.py), for items nothing traded prices. Fear's rule
        for what the market cannot price: "it should have a price if the bot has 1". It carries his hand
        prices for what never reaches the AH, like Necron's Handle. A bad file means no bot prices,
        never a service that will not start."""
        path = os.path.join(os.path.dirname(os.path.abspath(self.source)), "index.json")
        try:
            with open(path, encoding="utf-8") as f:
                rows = json.load(f).get("rows") or []
        except (OSError, ValueError, AttributeError) as e:
            if os.path.exists(path):
                print("bot prices unreadable", repr(e), flush=True)
            return {}
        # An NPC floor is the index's "nothing known" answer, not a price, and it sits ahead of
        # craft_cost in _unit. Astraea had no recipe in the bot, so its 100m floor beat a ~132b
        # craft, and every Hyperion, Scylla and Valkyrie then priced as "convert an Astraea" at
        # 3-4b (2026-11-16). Fear's deliberate NPC rules (dragon armour and the rest) stay; an
        # older index without the npc_rule flag keeps every row, as before.
        return {str(r["id"]).lower(): float(r["price"]) for r in rows
                if isinstance(r, dict) and r.get("id") and isinstance(r.get("price"), (int, float)) and r["price"] > 0
                and not (r.get("confidence") == "floor" and r.get("npc_rule") is False)}

    def use_bot_prices(self):
        fresh = self.bot_prices()
        with self.lock:
            self.bot = fresh
            if self.engine is not None:
                self.engine.bot = fresh
                self.engine._part_cache.clear()     # prices worked out with the old list
                self.engine._craft_cache.clear()
            self._board = None

    def _use_catalog(self, fresh):
        with self.lock:
            fresh.reforge_stones = {name: list(ids) for name, ids in getattr(self.catalog, 'reforge_stones', {}).items()}
            for name, entry in fresh.reforge_stone.items():
                if entry not in fresh.reforge_stones.setdefault(name, []):
                    fresh.reforge_stones[name].append(entry)
            self.catalog = fresh
            self.engine.catalog = fresh
            self.reports.catalog = fresh

    def _spot(self, item, raw):
        """Notes an id the catalog does not have. Only the collector's market feed gets here, never a
        linked player's own items, so nobody can put an item on the site by sending one."""
        if item.entry not in self.catalog.items and item.entry not in self._unseen:
            self._unseen[item.entry] = raw

    def owned_items(self, obj, out):
        """Every item copy in a profile, by id, skipping ones the catalog already has. A copy named
        with its own id is a placeholder the server never finished (leg_edrag, spirit_sceptre)."""
        if isinstance(obj, dict):
            data = obj.get("item_data")
            if "entry" in obj and isinstance(data, dict):
                entry = str(obj["entry"] or "").lower()
                name = plain(data.get("item_name"))
                if entry and name and name != entry and not entry.startswith("minecraft:") and entry not in self.catalog.items:
                    out.setdefault(entry, obj)
            for value in obj.values():
                self.owned_items(value, out)
        elif isinstance(obj, list):
            for value in obj:
                self.owned_items(value, out)

    def take_owned(self, found):
        """Learns ids read off profiles. Profiles come from dragsim.co, never from a linked mod."""
        if not found:
            return
        with self.lock:
            for entry, raw in found.items():
                if entry not in self.catalog.items:
                    self._unseen.setdefault(entry, raw)
            fresh = self.learn()
            if fresh:
                self._use_catalog(fresh)

    def learn_from_profiles(self):
        """Items people own before anyone lists one. The dungeon update of 2026-11-10 put Colossus
        armour, Nightfall, Dawnbreaker and Gravebinder in the top players' inventories while none had
        reached the AH, so the AH feed alone left them unsearchable and unwatchable. archive.py saves
        those profiles nightly to profiles.db beside the collector's database. Only the newest
        snapshot of each profile since the last read is opened, outside the lock."""
        path = os.path.join(os.path.dirname(os.path.abspath(self.source)), "profiles.db")
        if not os.path.exists(path):
            return
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)) as db:
            rows = db.execute("SELECT s.data, s.fetched_at FROM snapshots s JOIN (SELECT profile_id, max(fetched_at) m FROM snapshots "
                              "WHERE fetched_at > ? GROUP BY profile_id) t ON s.profile_id = t.profile_id AND s.fetched_at = t.m "
                              "WHERE s.hidden = 0", (self._profiles_at,)).fetchall()
        found = {}
        for blob, at in rows:
            try:
                self.owned_items(json.loads(gzip.decompress(blob)).get("profile") or {}, found)
            except (OSError, ValueError, EOFError):
                continue
            self._profiles_at = max(self._profiles_at, at)
        self.take_owned(found)

    def learn(self):
        """Adds what the feed showed that the catalog lacks, as soon as it shows. A fire sale puts
        its skins on the AH before dragsim.co lists them: the Colossus and Soulfire Scepter skins
        sold on 2026-11-08 with no search result, Notifier or favourite, because each of those
        only knows catalog ids, and the only fix was running tools/harvest_items.py by hand.
        Kept in learned_items.json, which no deploy carries, so a deploy cannot undo it; the
        published entry takes over once dragsim.co has one. Returns the new catalog, or None."""
        if not self._unseen:
            return None
        unseen, self._unseen = self._unseen, {}
        rows = {}
        for entry, raw in unseen.items():
            try:
                rows[entry] = learned_row(json.loads(raw) if isinstance(raw, str) else raw)
            except (ValueError, TypeError, AttributeError, KeyError):
                continue
        if not rows:
            return None
        learned = {}
        if os.path.exists(self.learned_path):
            with open(self.learned_path, encoding="utf-8") as f:
                learned = json.load(f)
        learned.update(rows)
        temp = self.learned_path + ".new"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(learned.items())), f, ensure_ascii=False, indent=0)
        os.replace(temp, self.learned_path)
        print("new items:", ", ".join(sorted(rows)), flush=True)
        return Catalog(self.catalog.data, {**rows, **self.catalog.extra})

    def run(self, every=15):
        last_catalog = last_backup = last_bot = time.time()
        last_profiles = last_pack = 0
        while True:
            last_pack = self.keep_pictures(last_pack)
            try:
                self.poll()
                if time.time() - last_bot > BOT_EVERY:
                    last_bot = time.time()
                    self.use_bot_prices()
                if time.time() - last_profiles > PROFILES_EVERY:
                    last_profiles = time.time()
                    self.learn_from_profiles()
                if time.time() - last_catalog > CATALOG_EVERY:
                    last_catalog = time.time()
                    self.refresh_catalog()
                if time.time() - last_backup > 86_400:
                    last_backup = time.time()
                    self.backup()
            except (sqlite3.Error, OSError, ValueError) as e:
                print("poll failed", repr(e), flush=True)
            time.sleep(every)

    # ------------------------------------------------------------------ answers

    def scan_player(self, name, fetch=None):
        """Everything a player owns, with what to offer, before you ever message them. Dragsim's
        profiles are public, and about
        a quarter of players hide them, which is reported rather than treated as empty."""
        now = time.time()
        cached = self._scans.get(name.lower())
        if cached and now - cached[0] < SCAN_SECONDS:
            return cached[1]
        profile = (fetch or fetch_profile)(name)
        found = {}
        self.owned_items(profile.get("profile") or {}, found)
        self.take_owned(found)
        result = self.value_profile(profile, name)
        self._scans[name.lower()] = (now, result)
        return result

    def value_profile(self, player, name):
        prof = player.get("profile") or {}
        if prof.get("inventory_hidden"):
            return {"player": name, "hidden": True}
        found = []
        worn = set()
        for slot, item in (prof.get("inventory") or {}).items():
            if isinstance(item, dict) and "entry" in item:
                armor = slot.isdigit() and 41 <= int(slot) <= 44
                found.append(("worn" if armor else "inventory", item))
                if armor:
                    worn.add(item["entry"])
        wardrobe = prof.get("wardrobe") or {}
        equipped = str(wardrobe.get("equipped"))
        for key, pieces in (wardrobe.get("slots") or {}).items():
            for item in (pieces or {}).values():
                # The equipped set is what is being worn, already counted above.
                if isinstance(item, dict) and "entry" in item and not (key == equipped and item["entry"] in worn):
                    found.append(("wardrobe", item))
        for place in ("ender_chest", "storage", "accessory_bag"):
            collect_items(prof.get(place), place.replace("_", " "), found)
        for pet in prof.get("pets") or ():
            if pet.get("type") and pet.get("rarity"):
                found.append(("pets", {"entry": f"{pet['type'].lower()}_pet_{pet['rarity'].lower()}", "count": 1,
                                       "item_data": {"item_name": pet["type"].replace("_", " ").title(), "rarity": pet["rarity"]},
                                       "item_modifiers": {}, "extra": {"pet_data": pet}}))
        groups = {}
        for place, raw in found:
            item = from_json(raw, self.catalog)
            if not item.entry:
                continue
            row = groups.setdefault(item.pricing_identity(), [raw, 0, set()])
            row[1] += item.count
            row[2].add(place)
        rows = []
        for raw, count, places in groups.values():
            est = self.quote(dict(raw, count=1))
            if not est["value"]:
                continue
            offers = est["offers"] or {}
            item = from_json(raw, self.catalog)
            rows.append({"entry": item.entry, "name": item.label(), "rarity": item.rarity, "count": count, "where": sorted(places),
                         "value": int(est["value"]), "offer": int(offers.get("mid") or 0), "net": int(offers.get("net") or 0),
                         "confidence": est["confidence"], "total": int(est["value"] * count), "exact": est["exact"]})
        rows.sort(key=lambda r: -r["total"])
        return {"player": name, "hidden": False, "coins": prof.get("coins"), "total": sum(r["total"] for r in rows), "items": rows[:80]}

    def quote(self, raw):
        """Everything a tooltip or a page needs about one item, by the fields the mod reads."""
        item = from_json(raw, self.catalog)
        with self.lock:
            est = self.engine.estimate(item)
            est = dict(est)
            est["offers"] = E.offers(est)
            est["insta_sell"] = E.insta_sell(est) if est["value"] else 0
            est["craft"] = self.engine.craft_cost(item.entry) * (item.count or 1)
            # The hover lines. An item the catalog does not know gets none.
            bazaar = self.engine.bazaar
            est["lore"] = describe(est, item.count, self.engine.full_craft(item, est["value"]),
                                   bazaar.daily(item.entry) if bazaar else None) if item.entry else []
            est["npc"] = self.catalog.npc(item.entry) * (item.count or 1)
            est["fee_rate"] = fees.rate(est["value"] * (item.count or 1))
        return est


def collect_items(obj, where, out):
    if isinstance(obj, dict):
        if "entry" in obj and "item_data" in obj:
            out.append((where, obj))
            return
        for value in obj.values():
            collect_items(value, where, out)
    elif isinstance(obj, list):
        for value in obj:
            collect_items(value, where, out)


def fetch_profile(name):
    url = "https://dragsim.co/api/player/" + urllib.parse.quote(name)
    request = urllib.request.Request(url, headers={"User-Agent": "dragsim-market"})
    with urllib.request.urlopen(request, timeout=20) as reply:
        return json.load(reply)


def hash_lines(trade):
    import hashlib
    text = "|".join(sorted(trade.get("got") or []) + ["/"] + sorted(trade.get("gave") or []))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
