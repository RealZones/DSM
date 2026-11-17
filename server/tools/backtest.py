#!/usr/bin/env python3
"""Walks real history forward and prices each sale from only what was known just before it.

    python tools/backtest.py                    the standard comparison
    python tools/backtest.py name=value ...     one extra run with those Params overrides

Three windows, because tuning on the crash after the 10-31 video and judging on the same days
would only prove a formula can memorise a fortnight:
    calm     10-23 to 10-30, before the video
    tune     10-31 to 11-03, what settings are chosen on
    verify   11-04 onward, never looked at while choosing
"""
import calendar
import collections
import json
import multiprocessing
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from market import engine as E                  # noqa: E402
from market.bazaar import Bazaar                # noqa: E402
from market.items import Catalog, from_json     # noqa: E402
from market import replay                       # noqa: E402

DATA = os.path.join(HERE, "..", "data")
SNAP = os.path.join(DATA, "prices.snapshot.db")


def stamp(month, day):
    return calendar.timegm((2026, month, day, 0, 0, 0)) * 1000


CALM, TUNE, VERIFY = stamp(10, 23), stamp(10, 31), stamp(11, 4)
_events = None


def load():
    global _events
    db = replay.connect(SNAP)
    _events = replay.events(db)
    db.close()


def run(job):
    name, overrides = job
    catalog = Catalog.load(os.path.join(DATA, "catalog.json"))
    with open(os.path.join(DATA, "recipes.json"), encoding="utf-8") as f:
        recipes = json.load(f)
    params = E.Params()
    for k, v in overrides.items():
        setattr(params, k, v)
    engine = E.Engine(catalog, params, recipes, Bazaar(catalog.npc))
    rows = []
    for ev in _events:
        if ev[2] == "sale":
            sid, entry, sold, created, unit, count, seller, buyer, raw = ev[3]
            item = from_json(raw)
            engine.now = max(engine.now, sold)
            if sold >= CALM:
                est = engine.estimate(item, exclude=sid)
                rows.append((sold, entry, item.identity(), unit, est["median"], est["lbin"], est["exact"]))
            engine.add_sale(sid, item, unit, seller, buyer, sold, created)
        else:
            replay.apply(engine, ev)
    return name, rows


def stats(rows):
    got = [(r[4], r[3]) for r in rows if r[4] > 0]
    if not got:
        return "no estimates"
    ape = sorted(abs(p - e) / p for e, p in got)
    bias = sorted(p / e for e, p in got)
    return (f"cov {len(got) / len(rows):4.2f}  err {ape[len(ape) // 2] * 100:5.1f}%  in15 {sum(1 for a in ape if a <= .15) / len(ape):4.2f}"
            f"  in30 {sum(1 for a in ape if a <= .30) / len(ape):4.2f}  act/est {bias[len(bias) // 2]:5.3f}")


def lowball(rows):
    """Buy at k of the estimate, sell at the next real sale of the same exact item."""
    by = collections.defaultdict(list)
    for r in rows:
        by[r[2]].append(r)
    ratios = []
    for group in by.values():
        for a, b in zip(group, group[1:]):
            if a[4] > 0 and a[3] >= 10_000_000:
                ratios.append(b[3] / a[4])
    ratios.sort()
    if not ratios:
        return ""
    q = lambda f: ratios[int(len(ratios) * f)]
    return f"next/est p10 {q(.1):.2f} p25 {q(.25):.2f} p50 {q(.5):.2f}  beats .82: {sum(1 for x in ratios if x > .82) / len(ratios):.2f}"


def main():
    jobs = [
        ("untuned", {}),
        ("no long term", {"use_long": False}),
        ("flat, thin ok", {"thin_fallback": True}),
        ("decay 24h", {"half_life_h": 24, "use_long": False, "thin_fallback": True}),
        ("decay 48h", {"half_life_h": 48, "use_long": False, "thin_fallback": True}),
        ("decay 96h", {"half_life_h": 96, "use_long": False, "thin_fallback": True}),
        ("decay 48h + LBIN cap", {"half_life_h": 48, "use_long": False, "thin_fallback": True, "lbin_cap": True}),
        ("decay 48h + LBIN .5", {"half_life_h": 48, "use_long": False, "thin_fallback": True, "lbin_cap": True, "lbin_blend": .5}),
        ("decay 24h, n=8", {"half_life_h": 24, "use_long": False, "thin_fallback": True, "short_n": 8, "short_n_fast": 10}),
        ("decay 48h, n=12", {"half_life_h": 48, "use_long": False, "thin_fallback": True, "short_n": 12, "short_n_fast": 12}),
    ]
    extra = {}
    for arg in sys.argv[1:]:
        k, v = arg.split("=")
        extra[k] = v == "True" if v in ("True", "False") else float(v) if "." in v else int(v)
    if extra:
        jobs = [("custom", extra)]
    with multiprocessing.get_context("spawn").Pool(min(len(jobs), 8), initializer=load) as pool:
        results = pool.map(run, jobs)
    for name, rows in results:
        print(f"\n{name}  ({len(rows)} sales)")
        for label, lo, hi in (("calm", CALM, TUNE), ("tune", TUNE, VERIFY), ("verify", VERIFY, 10 ** 15)):
            part = [r for r in rows if lo <= r[0] < hi]
            if part:
                print(f"  {label:7}{len(part):6}  {stats(part)}  {lowball(part)}")


if __name__ == "__main__":
    main()
