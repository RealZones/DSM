"""Puts Notifier hits on your phone, as a DM from the Endsight bot.

A DM because a post in a channel only buzzes a phone if that server is set to notify on every
message, and these are worth a buzz. Needs data/discord.json on the VPS, naming the file that holds
the bot's token and who to message: {"env": "/path/to/bot/.env", "user": "..."}. The token stays
in the bot's own file. With no discord.json nothing is sent, which keeps tests and local runs quiet.

Sending has its own thread. The poll holds the lock every page needs, and Discord can take seconds
or be down altogether, and neither should be able to stall the market.
"""
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request

API = "https://discord.com/api/v10"
AGENT = "DiscordBot (https://github.com/RealZones/Endsight, 1.0)"   # Discord refuses Python's default
LIMIT = 1900            # a message tops out at 2000
TRIES = 6
GAP = 1.0               # seconds between messages, well inside Discord's limit


def short(n):
    """190m, 2.5b, the way prices get typed."""
    for size, unit in ((1e9, "b"), (1e6, "m"), (1e3, "k")):
        if abs(n) >= size:
            return f"{n / size:.2f}".rstrip("0").rstrip(".") + unit
    return f"{n:g}"


def split(lines):
    """Lines into as few messages as fit."""
    out, current = [], ""
    for line in lines:
        if current and len(current) + len(line) + 1 > LIMIT:
            out.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    return out + [current] if current else out


class Discord:
    def __init__(self, token, user):
        self.token = token
        self.user = user
        self.channel = None
        self.queue = queue.Queue()
        self.thread = None

    @classmethod
    def load(cls, path):
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
        with open(config["env"], encoding="utf-8") as f:
            values = dict(line.strip().split("=", 1) for line in f if "=" in line and not line.startswith("#"))
        return cls(values["TOKEN"].strip("'\""), config["user"])

    def send(self, lines):
        """Takes the lines and returns at once."""
        for text in split(lines):
            self.queue.put(text)
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def _run(self):
        while True:
            self.deliver(self.queue.get())
            time.sleep(GAP)

    def deliver(self, text):
        """True once Discord has it. Refusals are not retried, since asking again changes nothing."""
        for attempt in range(TRIES):
            try:
                if self.channel is None:
                    self.channel = self._call("/users/@me/channels", {"recipient_id": self.user})["id"]
                self._call(f"/channels/{self.channel}/messages", {"content": text, "allowed_mentions": {"parse": []}})
                return True
            except urllib.error.HTTPError as e:
                if e.code != 429 and e.code < 500:
                    print("discord refused", e.code, flush=True)
                    return False
                wait = float(e.headers.get("Retry-After") or 5 * (attempt + 1))
            except (OSError, ValueError) as e:
                print("discord failed", type(e).__name__, flush=True)
                wait = 5 * (attempt + 1)
            time.sleep(wait)
        print("discord gave up", flush=True)
        return False

    def _call(self, path, body):
        request = urllib.request.Request(API + path, json.dumps(body).encode("utf-8"), {
            "Authorization": "Bot " + self.token, "Content-Type": "application/json", "User-Agent": AGENT})
        with urllib.request.urlopen(request, timeout=15) as reply:
            return json.load(reply)
