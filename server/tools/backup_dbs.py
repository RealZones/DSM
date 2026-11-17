#!/usr/bin/env python3
"""Daily copies of the two databases that cannot be rebuilt: own.db (every trade the mods sent)
and linked-auth.db (who is linked). Everything else in data/ comes back from the collector.

Run by dsm-backup.timer on the VPS. Uses SQLite's backup API, not a file copy, because the service
writes both files while this runs and a copied WAL database can come out torn. Keeps 14 days.
"""
import datetime
import os
import sqlite3
import sys

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OUT = os.path.join(DATA, "backups", "daily")
KEEP = 14
NAMES = ("own.db", "linked-auth.db")


def main():
    os.makedirs(OUT, mode=0o700, exist_ok=True)
    day = datetime.date.today().isoformat()
    for name in NAMES:
        source = os.path.join(DATA, name)
        if not os.path.isfile(source):
            continue
        target = os.path.join(OUT, f"{day}-{name}")
        temp = target + ".tmp"
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
        dst = sqlite3.connect(temp)
        with dst:
            src.backup(dst)
        src.close()
        check = dst.execute("PRAGMA integrity_check").fetchone()[0]
        dst.close()
        if check != "ok":
            os.remove(temp)
            sys.exit(f"{name}: backup failed its integrity check ({check})")
        os.chmod(temp, 0o600)
        os.replace(temp, target)
    for old in sorted(os.listdir(OUT))[:-KEEP * len(NAMES)]:
        os.remove(os.path.join(OUT, old))


if __name__ == "__main__":
    main()
