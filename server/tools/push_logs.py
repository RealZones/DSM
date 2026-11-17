#!/usr/bin/env python3
"""Sends what scan_logs.py read out of the game logs to the VPS's store of his own records.

    python tools/scan_logs.py
    python tools/push_logs.py make             on this PC: stamps times and writes data/log-push.json
    python tools/push_logs.py load FILE DB     on the VPS: adds what is new

Two steps because the times have to be worked out here, where the PC's timezone is, and the
database lives there. Safe to repeat: a trade the mod already sent, or an upgrade line already
loaded, is recognised and skipped, so a re-run cannot double a cost.
"""
import datetime
import hashlib
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
# The owner's Minecraft name, whose game logs these are.
PLAYER = os.environ.get("DSM_OWNER", "")


def stamp(day, at):
    return int(datetime.datetime.strptime(day + " " + at, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)


def make():
    with open(os.path.join(DATA, "log-trades.json"), encoding="utf-8") as f:
        trades = [dict(at=stamp(t["day"], t["at"]), player=t.get("user") or PLAYER, other=t["other"], got=t["got"], gave=t["gave"])
                  for t in json.load(f) if t.get("host") == "dragsim.co"]
    with open(os.path.join(DATA, "log-upgrades.json"), encoding="utf-8") as f:
        upgrades = [dict(at=stamp(u["day"], u["at"]), player=u.get("user") or PLAYER, text=u["text"]) for u in json.load(f)]
    with open(os.path.join(DATA, "log-push.json"), "w", encoding="utf-8") as f:
        json.dump(dict(trades=trades, upgrades=upgrades), f, ensure_ascii=False)
    print("trades", len(trades), "upgrade lines", len(upgrades))


def lines(got, gave):
    return hashlib.sha1("|".join(sorted(got) + ["/"] + sorted(gave)).encode("utf-8")).hexdigest()[:12]


def load(path, target):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    db = sqlite3.connect(target)
    added = 0
    for t in data["trades"]:
        same = db.execute("SELECT got, gave FROM trades WHERE other = ? AND at BETWEEN ? AND ?", (t["other"], t["at"] - 60_000, t["at"] + 60_000)).fetchall()
        wanted = lines(t["got"], t["gave"])
        if any(lines(json.loads(a), json.loads(b)) == wanted for a, b in same):
            continue
        ref = f"log:{t['other']}:{t['at']}:{wanted}"
        added += db.execute("INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?,?,?)",
                            (ref, t["at"], t["player"], t["other"], json.dumps(t["got"]), json.dumps(t["gave"]), "log", None)).rowcount
    known = {(a, d) for a, d in db.execute("SELECT at, data FROM events WHERE kind = 'chat'")}
    lines_added = 0
    for u in data["upgrades"]:
        text = json.dumps({"text": u["text"]})
        if (u["at"], text) not in known:
            known.add((u["at"], text))
            db.execute("INSERT INTO events (at, player, kind, data) VALUES (?,?,?,?)", (u["at"], u["player"], "chat", text))
            lines_added += 1
    db.commit()
    db.close()
    print("trades added", added, "upgrade lines added", lines_added)


if __name__ == "__main__":
    if sys.argv[1] == "make":
        make()
    else:
        load(sys.argv[2], sys.argv[3])
