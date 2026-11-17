#!/usr/bin/env python3
"""Starts the market service: replays history, follows the collector, serves the site.

    python run.py --source /path/to/prices.db --data ./data --owner YourMinecraftName --key-file ./market.key
"""
import argparse
import os
import threading

from market.service import Service
from market import web


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=os.path.join(here, "data", "prices.snapshot.db"))
    ap.add_argument("--data", default=os.path.join(here, "data"))
    ap.add_argument("--key", default="dev")
    ap.add_argument("--key-file")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8793)
    # Whose own trades become tracked flips: the site owner's Minecraft name.
    ap.add_argument("--owner", default=os.environ.get("DSM_OWNER", ""))
    ap.add_argument("--no-follow", action="store_true", help="for a frozen snapshot: nothing new will arrive")
    args = ap.parse_args()
    key = args.key
    if args.key_file:
        with open(args.key_file, encoding="utf-8") as f:
            key = f.read().strip()
    service = Service(args.source, os.path.join(args.data, "catalog.json"), os.path.join(args.data, "recipes.json"), os.path.join(args.data, "own.db"),
                      tracked=(args.owner,) if args.owner else ())
    took, count = service.build()
    print(f"built {count} events in {took:.1f}s, {len(service.tracker.flips)} flips", flush=True)
    if not args.no_follow:
        threading.Thread(target=service.run, daemon=True).start()
    web.serve(service, key, args.host, args.port)


if __name__ == "__main__":
    main()
