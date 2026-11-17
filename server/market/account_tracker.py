"""Linked players' private ledgers over the shared auction and pricing data."""
import json
import re
import sqlite3
import time
from collections import OrderedDict
from contextlib import closing

from . import replay
from .items import from_json, from_receipt
from .service import DAY, hash_lines
from .tracker import Tracker

SNAPSHOT_SECONDS = 30
CACHED_ACCOUNTS = 32
ACCOUNT_ID = re.compile(r"[0-9a-f]{32}\Z")
PLAYER_NAME = re.compile(r"[A-Za-z0-9_]{1,16}\Z")
MAX_COINS = 2**63 - 1
MAX_STACK = 2**31 - 1


def validate_linked_item(raw, catalog):
    """Keep untrusted client NBT inside the numeric bounds of a real item."""
    if not isinstance(raw, dict):
        raise ValueError("invalid market item")
    try:
        item = from_json(raw, catalog)
    except (AttributeError, TypeError, OverflowError) as error:
        raise ValueError("invalid market item") from error
    if type(item.count) is not int or not 0 < item.count <= MAX_STACK:
        raise ValueError("invalid item count")
    if not all(0 <= count <= 100 for count in (item.stars, item.hpb, item.ghpb, item.sync, item.aow)):
        raise ValueError("invalid item upgrades")
    if type(item.npc) not in (float, int) or not 0 <= item.npc <= MAX_COINS:
        raise ValueError("invalid NPC price")
    for part in (item.skin, item.dye, item.pet_item, item.pet_skin, item.engine, item.tank, item.module):
        if part is not None and (not isinstance(part, str) or len(part) > 128):
            raise ValueError("invalid item part")


def check_timestamp(at):
    if type(at) is not int or not 0 <= at <= int(time.time() * 1000) + DAY:
        raise ValueError("invalid market timestamp")


class AccountTrackers:
    def __init__(self, service):
        self.service = service
        self.cache = OrderedDict()
        # Use separate tables in the same database so existing backups cover every ledger.
        with closing(sqlite3.connect(service.own_path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS linked_trades ("
                       "account TEXT, ref TEXT, at INTEGER, other TEXT, fingerprint TEXT, receipt TEXT, "
                       "PRIMARY KEY (account, ref))")
            db.execute("CREATE INDEX IF NOT EXISTS linked_trade_receipt ON linked_trades (account, other, at)")
            db.execute("CREATE TABLE IF NOT EXISTS linked_events ("
                       "account TEXT, at INTEGER, kind TEXT, data TEXT, PRIMARY KEY (account, at, kind, data))")

    def for_account(self, account, player):
        if not ACCOUNT_ID.fullmatch(account) or not PLAYER_NAME.fullmatch(player):
            raise ValueError("invalid linked profile")
        with self.service.lock:
            view = self.cache.pop(account, None)
            if view is None or view.player != player:
                view = AccountMarket(self.service, account, player)
            self.cache[account] = view
            if len(self.cache) > CACHED_ACCOUNTS:
                self.cache.popitem(last=False)
            return view


class AccountMarket:
    # Shared pricing and collector views. Private service members must be routed explicitly.
    SHARED = frozenset({"source", "catalog", "engine", "deals", "lock", "listing", "built_at", "last_poll", "tape",
                        "tape_rows", "bazaar_board", "craft_board", "recipes", "crafts", "scan_player", "quote", "reports",
                        "players", "pictures"})

    def __init__(self, service, account, player):
        self._service = service
        self.account = account
        self.player = player
        self.tracked = (player,)
        self._tracker = None
        self._built_at = None
        self._fresh_until = 0

    def __getattr__(self, name):
        if name in self.SHARED:
            return getattr(self._service, name)
        raise AttributeError(name)

    @property
    def tracker(self):
        with self.lock:
            if self._tracker is None or self._built_at != self.built_at or self._fresh_until <= time.monotonic():
                with closing(sqlite3.connect(self._service.own_path)) as db:
                    tracker, _ = self._build_tracker(db)
                self._set_tracker(tracker)
            return self._tracker

    def watches(self):
        return self._service.watches(account=self.account)

    def listed(self, entry, wanted):
        return self._service.listed(entry, wanted, player=self.player)

    def watch_matches(self, watch):
        return self._service.watch_matches(watch, player=self.player)

    def watch_hits(self, ref, limit=6):
        return self._service.watch_hits(ref, limit, account=self.account)

    def recent_hits(self):
        return self._service.recent_hits(account=self.account)

    def save_watch(self, body):
        return self._service.save_watch(body, account=self.account, player=self.player)

    def drop_watch(self, ref):
        return self._service.drop_watch(ref, account=self.account)

    def notification_settings(self):
        return self._service.notification_settings(account=self.account)

    def save_notification_settings(self, body):
        return self._service.save_notification_settings(body, account=self.account)

    def record_trade(self, trade):
        trade = dict(trade, player=self.player)
        other, at = trade["other"], trade["at"]
        if not isinstance(other, str) or not PLAYER_NAME.fullmatch(other):
            raise ValueError("invalid trade partner")
        check_timestamp(at)
        for side in ("got", "gave"):
            lines = trade.get(side, [])
            if not isinstance(lines, list) or len(lines) > 128 or any(not isinstance(line, str) or len(line) > 512 for line in lines):
                raise ValueError("invalid receipt lines")
            for line in lines:
                kind, amount, _ = from_receipt(line, self.catalog)
                if not 0 < amount <= (MAX_STACK if kind == "item" else MAX_COINS):
                    raise ValueError("invalid trade amount")
        detail = trade.get("detail")
        if detail is not None:
            if not isinstance(detail, dict) or not isinstance(detail.get("items", []), list) or len(detail.get("items", [])) > 128:
                raise ValueError("invalid trade window")
            for row in detail.get("items", []):
                if not isinstance(row, dict) or not isinstance(row.get("name", ""), str):
                    raise ValueError("invalid trade window slot")
                if row.get("item") is not None:
                    validate_linked_item(row["item"], self.catalog)
        fingerprint = hash_lines(trade)
        ref = f"{other}:{at}:{fingerprint}"
        with self.lock:
            with closing(sqlite3.connect(self._service.own_path)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                match = db.execute("SELECT ref FROM linked_trades WHERE account = ? AND other = ? "
                                   "AND at BETWEEN ? AND ? AND fingerprint = ? LIMIT 1",
                                   (self.account, other, at - 60_000, at + 60_000, fingerprint)).fetchone()
                if match:
                    return {"ref": match[0], "new": False, "kind": None, "flips": None}
                db.execute("INSERT INTO linked_trades VALUES (?, ?, ?, ?, ?, ?)",
                           (self.account, ref, at, other, fingerprint, json.dumps(dict(trade, ref=ref))))
                # A delayed receipt may supply the purchase for an auction we already collected.
                # Replay in event order and roll back the insert if the receipt cannot be applied.
                try:
                    tracker, record = self._build_tracker(db, ref)
                except (AttributeError, TypeError, OverflowError) as error:
                    raise ValueError("invalid receipt detail") from error
            self._set_tracker(tracker)
        return {"ref": ref, "new": True, "kind": record["kind"], "flips": record["flips"]}

    def add_event(self, event):
        at = int(event.get("at") or time.time() * 1000)
        check_timestamp(at)
        kind = event.get("kind")
        data = event.get("data")
        if not isinstance(kind, str) or len(kind) > 32 or not isinstance(data, dict):
            raise ValueError("invalid market event")
        if kind == "chat" and (not isinstance(data.get("text"), str) or len(data["text"]) > 1024):
            raise ValueError("invalid market chat line")
        with self.lock:
            with closing(sqlite3.connect(self._service.own_path)) as db, db:
                inserted = db.execute("INSERT OR IGNORE INTO linked_events VALUES (?, ?, ?, ?)",
                                      (self.account, at, kind, json.dumps(data, sort_keys=True))).rowcount
            if kind == "chat" and inserted:
                self._fresh_until = 0

    def _build_tracker(self, db, ref=None):
        events = self._auction_events()
        for at, receipt in db.execute("SELECT at, receipt FROM linked_trades WHERE account = ? ORDER BY at, ref",
                                      (self.account,)):
            events.append((at, 2, dict(json.loads(receipt), player=self.player)))
        for at, data in db.execute("SELECT at, data FROM linked_events WHERE account = ? AND kind = 'chat' ORDER BY at, data",
                                   (self.account,)):
            events.append((at, 1, json.loads(data)["text"]))
        tracker = Tracker(self.engine, self.tracked)
        record = None
        for at, kind, row in sorted(events, key=lambda e: (e[0], e[1])):
            if kind == 3:
                sid, raw, unit, count, seller, buyer, created, end = row
                item = from_json(raw)
                est = self.engine.estimate(item, exclude=sid)
                tracker.on_sale(sid, item, unit, count or 1, seller, buyer, at, created, end, est)
                other = buyer if seller == self.player else seller
                if other != self.player:
                    tracker.stocks.pop(other, None)
            elif kind == 2:
                result = tracker.on_trade(row)
                if row["ref"] == ref:
                    record = result
            elif kind == 1:
                tracker.on_chat(self.player, at, row)
            else:
                raw, price, created, end = row
                tracker.on_unlisted(self.player, from_json(raw), price, at, end, created)
        # Counterparties may appear in several public auctions with this account. Their derived
        # totals are incomplete, so only keep this account's ledger results.
        tracker.flips = [row for row in tracker.flips if row["player"] == self.player]
        tracker.sales_without_buy = [row for row in tracker.sales_without_buy if row["player"] == self.player]
        return tracker, record

    def _set_tracker(self, tracker):
        self._tracker = tracker
        self._built_at = self.built_at
        self._fresh_until = time.monotonic() + SNAPSHOT_SECONDS

    def _auction_events(self):
        name = self.player
        events = []
        with closing(replay.connect(self.source)) as db:
            sales = db.execute("SELECT s.id, s.item, s.unit_price, s.count, s.seller, s.buyer, "
                               "s.created_at, l.end_at, s.sold_at FROM sales s LEFT JOIN listings l ON s.id = l.id "
                               "WHERE (s.seller = ? COLLATE NOCASE OR s.buyer = ? COLLATE NOCASE) AND s.unit_price > 0 "
                               "ORDER BY s.sold_at, s.id", (name, name))
            for sid, raw, unit, count, seller, buyer, created, end, sold in sales:
                seller = name if (seller or "").lower() == name.lower() else seller
                buyer = name if (buyer or "").lower() == name.lower() else buyer
                events.append((sold, 3, (sid, raw, unit, count, seller, buyer, created, end)))
            listings = db.execute("SELECT item, unit_price, count, created_at, end_at, last_seen FROM listings "
                                  "WHERE seller = ? COLLATE NOCASE AND bin = 1 AND unit_price > 0 "
                                  "AND outcome IS NOT NULL AND outcome != 'sold' "
                                  "AND id NOT IN (SELECT id FROM sales) ORDER BY last_seen, id", (name,))
            for raw, unit, count, created, end, last in listings:
                events.append((last or end or created, 0, (raw, unit * (count or 1), created, end)))
        return events
