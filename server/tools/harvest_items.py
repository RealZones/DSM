#!/usr/bin/env python3
"""Finds items the published catalog leaves out. Skins, runes and many dyes are not in it at all, so
a receipt naming one could not be matched to anything, and a profile's id had no name. Profiles and
the auction house both carry the real id beside the display name, so the two are read back here.

Run on the VPS, which can reach dragsim.co. Reads the busiest traders plus anyone named on a
reported trade, a second or so apart, and writes data/extra_items.json.

    python3 tools/harvest_items.py PRICES_DB OUT_JSON [how many traders]
"""
import collections
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
GAP = 1.2       # dragsim.co caches a profile about 30 seconds, and this is not worth being rude over
sys.path.insert(0, os.path.join(HERE, ".."))
from market.items import picture_fields         # noqa: E402


def plain(text):
    return re.sub(r"§.", "", str(text or "")).strip()


def walk(obj, out, copies):
    if isinstance(obj, dict):
        data = obj.get("item_data")
        if "entry" in obj and isinstance(data, dict):
            name = plain(data.get("item_name") or data.get("name"))
            if name:
                out[obj["entry"]].update([(name, data.get("rarity"))])
                copies.setdefault(obj["entry"], obj)
        for value in obj.values():
            walk(value, out, copies)
    elif isinstance(obj, list):
        for value in obj:
            walk(value, out, copies)


def traders(db, wanted):
    rows = db.execute("SELECT name, sum(n) FROM (SELECT seller name, count(*) n FROM sales GROUP BY seller UNION ALL "
                      "SELECT buyer name, count(*) n FROM sales GROUP BY buyer) WHERE name IS NOT NULL GROUP BY name ORDER BY 2 DESC LIMIT ?", (wanted,))
    return [row[0] for row in rows]


def main():
    source, target = sys.argv[1], sys.argv[2]
    wanted = int(sys.argv[3]) if len(sys.argv) > 3 else 80
    with open(os.path.join(DATA, "catalog.json"), encoding="utf-8") as f:
        known = set(json.load(f)["items"])
    db = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    names = traders(db, wanted)
    reported = os.path.join(DATA, "reported.json")
    if os.path.exists(reported):
        with open(reported, encoding="utf-8") as f:
            for row in json.load(f):
                names += [n for n in (row.get("by"), row.get("with")) if n]
    seen = collections.defaultdict(collections.Counter)
    copies = {}     # one copy of each id, for its picture
    for raw, in db.execute("SELECT item FROM sales WHERE rowid IN (SELECT max(rowid) FROM sales GROUP BY entry)"):
        walk(json.loads(raw), seen, copies)
    done = 0
    for name in dict.fromkeys(names):
        request = urllib.request.Request("https://dragsim.co/api/player/" + urllib.parse.quote(name), headers={"User-Agent": "dragsim-market"})
        try:
            with urllib.request.urlopen(request, timeout=20) as reply:
                walk(json.load(reply).get("profile") or {}, seen, copies)
            done += 1
        except OSError:
            pass
        time.sleep(GAP)
    out = {}
    for entry, names_ in seen.items():
        if entry in known:
            continue
        (name, tier), _ = names_.most_common(1)[0]
        out[entry] = {"name": name, "tier": tier, "category": "COSMETIC" if re.search(r"skin|dye|rune|sound", entry + name, re.I) else "ITEM",
                      **picture_fields(copies.get(entry) or {})}
    with open(target, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(out.items())), f, ensure_ascii=False, indent=0)
    print("profiles read", done, "items outside the catalog", len(out))


if __name__ == "__main__":
    main()
