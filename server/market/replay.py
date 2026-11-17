"""Turns prices.db into one time-ordered stream, so the live service and the backtest build their
state the same way. Reads only."""
import sqlite3

from .items import from_json


def connect(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)


def quotes(db, since=0):
    return db.execute("SELECT ts, product, best_bid, best_ask, buy_quantity, sell_quantity, traded, book FROM quotes "
                      "WHERE ts >= ? AND book IS NOT NULL ORDER BY ts", (since,))


def events(db, since=0, with_quotes=True):
    """(ts, order, kind, row). `order` settles ties: the book moves, then a listing appears, then
    a sale happens, the only order those can really occur in within one poll."""
    out = []
    if with_quotes:
        for row in quotes(db, since):
            out.append((row[0], 0, "quote", row))
    for lid, entry, created, end, unit, count, bin_, seller, item, first, last, outcome in db.execute(
            "SELECT id, entry, created_at, end_at, unit_price, count, bin, seller, item, first_seen, last_seen, outcome "
            "FROM listings"):
        seen = first or created
        if not bin_ or not unit:
            continue
        out.append((seen, 1, "list", (lid, entry, created, end, unit, count or 1, seller, item)))
        if outcome and outcome != "sold":
            out.append((last or seen, 2, "unlist", lid))
    for sid, entry, sold, created, unit, count, bin_, seller, buyer, item, source in db.execute(
            "SELECT id, entry, sold_at, created_at, unit_price, count, bin, seller, buyer, item, source FROM sales"):
        if not unit:
            continue
        out.append((sold, 3, "sale", (sid, entry, sold, created, unit, count or 1, seller, buyer, item)))
    out.sort(key=lambda e: (e[0], e[1]))
    return out


def apply(engine, event):
    """Feeds one non-sale event in. Sales are left to the caller, who may want to price one
    before the engine learns from it."""
    ts, _, kind, row = event
    if kind == "quote":
        engine.now = max(engine.now, ts)
        if engine.bazaar:
            engine.bazaar.update(*row)
    elif kind == "list":
        lid, entry, created, end, unit, count, seller, item = row
        engine.add_listing(lid, from_json(item), unit, seller, ts, end or ts, count)
    elif kind == "unlist":
        engine.remove_listing(row)
