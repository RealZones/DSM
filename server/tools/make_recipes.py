#!/usr/bin/env python3
"""Adds what the mod's scans know to data/recipes.json: which kind of recipe each one is, how long
a forge step takes, and anything scanned since the file was built. Run on this PC, where the game's
config folder is, then deploy data/recipes.json.

    python tools/make_recipes.py

Two things in the scans are not what they look like:

  - The recipe menu draws forge recipes on a crafting grid, so a Void Plate turns up in recipes.txt
    as if it could be crafted on the spot. A craft flip list that believed that would hide every
    forge wait. So a grid recipe that needs exactly what a forge line needs is dropped.
  - A scan can catch a grid before its items load, which leaves cells reading "1*" with no name.
    Flawed Amethyst came out that way and it is a real recipe with five cells of Rough. A line
    with a blank cell never replaces one that was whole.
"""
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from market.items import Catalog            # noqa: E402
from import_wither_recipes import merge     # noqa: E402

DATA = os.path.join(HERE, "..", "data")
CONFIG = r"C:\Users\Robdy\AppData\Roaming\PrismLauncher\instances\Fabulously Optimized\minecraft\config\endsight"
DURATION = re.compile(r"(\d+)\s*([dhms])")
SECONDS = {"d": 86_400, "h": 3_600, "m": 60, "s": 1}


def hours(text):
    return sum(int(n) * SECONDS[unit] for n, unit in DURATION.findall(text)) / 3600


def cells(fields):
    """count*name cells into {name: amount}, and whether any cell had lost its name."""
    need = collections.Counter()
    coins = 0
    blank = False
    for cell in fields:
        if "*" not in cell:
            continue
        count, _, name = cell.partition("*")
        name = name.strip()
        if not name:
            blank = True
        elif name.lower().endswith(" coins"):
            coins += int(re.sub(r"[^0-9]", "", name)) * int(count)
        else:
            need[name] += int(count)
    return need, coins, blank


def main():
    catalog = Catalog.load(os.path.join(DATA, "catalog.json"))
    lower = {name.lower(): entry for name, entry in catalog.names.items()}
    resolve = lambda name: catalog.names.get(name) or lower.get(name.lower())
    with open(os.path.join(DATA, "recipes.json"), encoding="utf-8") as f:
        recipes = json.load(f)

    # What is forged, with its time, from the longest scan anyone has.
    forged = {}
    with open(os.path.join(CONFIG, "forge.txt"), encoding="utf-8-sig") as f:
        for line in f:
            if line[:1] in "#-" or not line.strip():
                continue
            parts = line.rstrip("\r\n").split("\t")
            entry = resolve(parts[1])
            if not entry:
                print("forge line with no item:", parts[1])
                continue
            need, coins, _ = cells(parts[3:])
            forged[entry] = dict(hours=hours(parts[2]), need={resolve(n) or n: c for n, c in need.items()}, coins=coins)

    added = []
    with open(os.path.join(CONFIG, "recipes.txt"), encoding="utf-8-sig") as f:
        for line in f:
            if line[:1] in "#-@" or not line.strip():
                continue
            parts = line.rstrip("\r\n").split("\t")
            entry = resolve(parts[0])
            need, coins, blank = cells(parts[2:])
            if not entry or blank:
                continue
            missing = [n for n in need if not resolve(n)]
            joined = {resolve(n): c for n, c in need.items() if resolve(n)}
            text = ", ".join(f"{c}× {catalog.name(e)}" for e, c in joined.items())
            row = {"yield": max(1, int(parts[1] or 1)), "need": joined, "coins": coins, "missing": missing, "text": "craft: " + text}
            rows = recipes.setdefault(entry, [])
            if any(r["need"] == joined and (r.get("yield") or 1) == row["yield"] and (r.get("coins") or 0) == coins for r in rows):
                continue
            rows.append(row)
            added.append(parts[0])

    dropped = 0
    timed = 0
    for entry, rows in recipes.items():
        keep = []
        for row in rows:
            row["kind"] = row["text"].split(":")[0]
            job = forged.get(entry)
            if job and row["kind"] == "craft" and row["need"] == job["need"]:
                dropped += 1
                continue
            if row["kind"] == "forge":
                if job:
                    row["hours"] = job["hours"]
                    timed += 1
                else:
                    print("forge recipe with no time:", entry)
            keep.append(row)
        recipes[entry] = keep
    # A forge line with no recipe left means the grid copy was the only one, so it comes back as a forge job.
    for entry, job in forged.items():
        if not recipes.get(entry):
            text = ", ".join(f"{c}× {catalog.name(e)}" for e, c in job["need"].items())
            recipes[entry] = [{"yield": 1, "need": job["need"], "coins": job["coins"], "missing": [n for n in job["need"] if n not in catalog.items],
                               "text": "forge: " + text, "kind": "forge", "hours": job["hours"]}]
            timed += 1
    overrides = os.path.join(DATA, "recipe_overrides.json")
    if os.path.exists(overrides):
        with open(overrides, encoding="utf-8") as f:
            merge(recipes, json.load(f))
    with open(os.path.join(DATA, "recipes.json"), "w", encoding="utf-8") as f:
        json.dump(recipes, f, ensure_ascii=False)
    kinds = collections.Counter(r["kind"] for rows in recipes.values() for r in rows)
    print(f"added {added}\ndropped {dropped} grid copies of forge recipes\ntimed {timed}\nkinds {dict(kinds)}, outputs {len(recipes)}")


if __name__ == "__main__":
    main()
