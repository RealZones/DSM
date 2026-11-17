#!/usr/bin/env python3
"""One Discord DM when DSM stops answering and one when it is back. Run every minute on the VPS by
dsm-down-alert.timer; the DM goes where the Notifier's do (data/discord.json, the bot's own token).

Quiet on purpose ("that should be limited"): three failed checks in a row, about three minutes, before
anything is said, so a restart or a deploy never pings; at most one down message an hour, so a site
that flaps is one message; and a back message only after an outage that was announced. It checks the
public address through nginx, so a dead nginx or an expired certificate counts as down too. It runs
on the same VPS, so it cannot report the VPS itself dying.

    python3 tools/down_alert.py [--dry-run]      # --dry-run prints instead of sending or saving
"""
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from market.discord import Discord     # noqa: E402

DATA = os.path.join(HERE, "..", "data")
STATE = os.path.join(DATA, "down-alert.json")
FAILS = 3           # checks a minute apart before an outage is announced
QUIET = 3600        # seconds between two down messages, however often it flaps


def step(state, ok, now, detail=""):
    """The state after one check, and the message it calls for, if any."""
    if ok:
        message = None
        if state.get("announced"):
            message = f"DSM is back, after about {round((now - state['since']) / 60)} min down."
        return {"fails": 0, "since": None, "announced": False, "last_alert": state.get("last_alert")}, message
    state = dict(state, fails=state.get("fails", 0) + 1, since=state.get("since") or now)
    last = state.get("last_alert")
    if state["fails"] >= FAILS and not state.get("announced") and (last is None or now - last >= QUIET):
        state.update(announced=True, last_alert=now)
        return state, f"DSM is down: the site has not answered for {state['fails']} minutes ({detail})."
    return state, None


def check(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "dsm-down-alert"}), timeout=20) as reply:
            json.load(reply)
        return True, ""
    except Exception as e:      # noqa: BLE001 - any way of not answering is down
        return False, f"{type(e).__name__} {getattr(e, 'code', '')}".strip()


def main():
    dry = "--dry-run" in sys.argv
    with open(os.path.join(DATA, "link.json"), encoding="utf-8") as f:
        url = json.load(f)["origin"].rstrip("/") + "/market/link/api/status"
    state = {}
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            state = json.load(f)
    ok, detail = check(url)
    state, message = step(state, ok, time.time(), detail)
    if dry:
        print("answering" if ok else f"not answering: {detail}", "| would send:", message, "| state:", json.dumps(state))
        return
    if message:
        discord = Discord.load(os.path.join(DATA, "discord.json"))
        if discord is None or not discord.deliver(message):
            print("down alert not delivered:", message, flush=True)
    temp = STATE + ".new"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(temp, STATE)


if __name__ == "__main__":
    main()
