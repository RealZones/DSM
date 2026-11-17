#!/usr/bin/env python3
"""Rebuilds what the game logs know about trades, the auction house and upgrades.

Every Prism instance keeps its chat in logs/*.log(.gz). Dragsim prints a receipt after each
trade, so trades made before the tracker existed can still be read back:

    Trade successful with SampleBuyer!
      + Eclipse Dye
      - Coins: 630,000,000

Output (next to this script, under ../data):
    log-trades.json   every trade, with the account that made it
    log-shapes.txt    every system message shape that mentions money or an item change

Log times are local to the PC, so each file is dated from its name (latest.log from its mtime).
"""
import collections
import datetime
import glob
import gzip
import io
import json
import os
import re
import sys

ROOT = r"C:\Users\Robdy\AppData\Roaming\PrismLauncher\instances"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
LINE = re.compile(r"^\[(\d\d:\d\d:\d\d)\] \[[^\]]+\]: (?:\[System\] )?\[CHAT\] (.*)$")
USER = re.compile(r"Setting user: (\S+)")
HOST = re.compile(r"Connecting to ([\w.\-]+), ?\d+")
TRADE = re.compile(r"^(?:§.)*Trade successful with (\S+?)!")
SIDE = re.compile(r"^§([ac])\s+([+\-])\s(.*)$")
PLAYER_CHAT = re.compile(r"^(?:Instance > |§9Party §8> |Guild > )?(?:\[[A-Z+]+\] )?[A-Za-z0-9_]{3,16}(?:§f)?: ")
REFORGE = re.compile(r"You applied the .+ reforge to your item! \(-[\d,]+ coins\)|Supercrafted |Collected .+ from the Forge")
KEYWORD = re.compile(
    r"auction|bazaar|\bbuy order|sell offer|purchased|bought|claimed|collected|\bsold\b|\bsell\b|applied|reforge|recomb|"
    r"upgrad|crafted|forge|forging|salvag|combin|star|skin|dye|\bbook|enrich|coat|synchron|art of|residue|trade|coins|"
    r"\bgems?\b|withdr|deposit|listed|listing", re.I)


def files():
    for inst in sorted(os.listdir(ROOT)):
        logs = os.path.join(ROOT, inst, "minecraft", "logs")
        if not os.path.isdir(logs):
            continue
        for path in sorted(glob.glob(os.path.join(logs, "*"))):
            name = os.path.basename(path)
            if name.endswith(".log.gz") or name == "latest.log":
                yield inst, path


def lines(path):
    opener = (lambda p: gzip.open(p, "rb")) if path.endswith(".gz") else (lambda p: open(p, "rb"))
    with opener(path) as f:
        for raw in io.TextIOWrapper(f, encoding="utf-8", errors="replace"):
            yield raw.rstrip("\r\n")


def date_of(path):
    m = re.match(r"(\d{4}-\d\d-\d\d)", os.path.basename(path))
    if m:
        return m.group(1)
    return datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")


def main():
    trades, shapes, samples, upgrades = [], collections.Counter(), {}, []
    seen_files = 0
    earliest = None
    for inst, path in files():
        seen_files += 1
        day = date_of(path)
        earliest = min(earliest or day, day)
        user, host = None, None
        cur = None
        try:
            for raw in lines(path):
                m = USER.search(raw)
                if m:
                    user = m.group(1)
                m = HOST.search(raw)
                if m and "discord" not in m.group(1):
                    host = m.group(1)
                m = LINE.match(raw)
                if not m:
                    continue
                at, msg = m.group(1), m.group(2)
                t = TRADE.match(msg)
                if t:
                    cur = dict(day=day, at=at, inst=inst, user=user, host=host, other=t.group(1), got=[], gave=[])
                    trades.append(cur)
                    continue
                s = SIDE.match(msg)
                if s and cur is not None:
                    (cur["got"] if s.group(2) == "+" else cur["gave"]).append(s.group(3))
                    continue
                cur = None
                if PLAYER_CHAT.match(msg) or "To " == msg[:3] or "From " == msg[:5]:
                    continue
                if host != "dragsim.co":
                    continue   # the same instances also played Hypixel; only Dragsim's lines count here
                if REFORGE.search(re.sub(r"§.", "", msg)):
                    upgrades.append(dict(day=day, at=at, user=user, text=msg))
                if KEYWORD.search(msg):
                    plain = re.sub(r"§.", "", msg)
                    k = re.sub(r"[\d][\d,\.]*[kKmMbB]?", "#", plain)
                    k = re.sub(r"\b[A-Z][a-z]+(?: [A-Z][a-z']+){1,4}\b", "Item", k)[:90]
                    shapes[k] += 1
                    samples.setdefault(k, (day, msg[:150]))
        except (OSError, EOFError) as e:
            print("skip", path, e, file=sys.stderr)
    os.makedirs(OUT, exist_ok=True)
    uniq = {}
    for t in trades:
        uniq[(t["day"], t["at"], t["other"], tuple(t["got"]), tuple(t["gave"]))] = t
    out = sorted(uniq.values(), key=lambda t: (t["day"], t["at"]))
    with open(os.path.join(OUT, "log-trades.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    with open(os.path.join(OUT, "log-upgrades.json"), "w", encoding="utf-8") as f:
        json.dump(upgrades, f, ensure_ascii=False, indent=0)
    with open(os.path.join(OUT, "log-shapes.txt"), "w", encoding="utf-8") as f:
        for k, n in shapes.most_common():
            f.write(f"{n}\t{k}\t{samples[k][0]}\t{samples[k][1]}\n")
    print("log files", seen_files, "earliest", earliest, "trades", len(out), "shapes", len(shapes))
    print("by user", collections.Counter(t["user"] for t in out).most_common())
    print("by host", collections.Counter(t["host"] for t in out).most_common())


if __name__ == "__main__":
    main()
