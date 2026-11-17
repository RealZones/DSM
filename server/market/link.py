"""Minecraft session proof and one-time browser pairing for the shared site."""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from contextlib import closing

NAME = re.compile(r"[A-Za-z0-9_]{1,16}")
UUID = re.compile(r"[0-9a-f]{32}")
CHALLENGE = re.compile(r"[0-9a-f]{40}")
CODE = re.compile(r"[0-9A-F]{8}")
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
SESSION = "https://sessionserver.mojang.com/session/minecraft/hasJoined"
COOKIE = "market_session"
CODE_SECONDS = 300
BROWSER_SECONDS = 30 * 86_400


class LinkError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def digest(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def verify_minecraft(player, challenge):
    query = urllib.parse.urlencode({"username": player, "serverId": challenge})
    try:
        with urllib.request.urlopen(f"{SESSION}?{query}", timeout=6) as reply:
            return json.loads(reply.read(16_384)) if reply.status == 200 else None
    except urllib.error.HTTPError as error:
        if error.code in (400, 404):
            return None
        raise LinkError(503, "Minecraft sign-in is temporarily unavailable") from None
    except (OSError, ValueError):
        raise LinkError(503, "Minecraft sign-in is temporarily unavailable") from None


class Pairing:
    def __init__(self, db_path, origin, owner=None, verify=verify_minecraft):
        self.db_path = db_path
        self.origin = origin
        self.owner = owner
        self.verify = verify
        self.lock = threading.Lock()
        self.challenges = {}
        self.attempts = deque()
        os.close(os.open(db_path, os.O_CREAT | os.O_WRONLY, 0o600))
        with closing(self.connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, player TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS codes (hash TEXT PRIMARY KEY, account TEXT UNIQUE NOT NULL, expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS credentials (hash TEXT PRIMARY KEY, account TEXT NOT NULL, kind TEXT NOT NULL, expires INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS credentials_account ON credentials(account, kind);
            """)

    def connect(self):
        return sqlite3.connect(self.db_path, timeout=5)

    def throttle(self):
        now = time.monotonic()
        with self.lock:
            while self.attempts and self.attempts[0] <= now - 60:
                self.attempts.popleft()
            if len(self.attempts) >= 180:
                raise LinkError(429, "Too many link attempts. Try again in a minute.")
            self.attempts.append(now)

    def start(self, body):
        self.throttle()
        player = str(body.get("player") or "")
        if not NAME.fullmatch(player):
            raise LinkError(400, "Invalid Minecraft username")
        now = time.monotonic()
        challenge = secrets.token_hex(20)
        with self.lock:
            self.challenges = {key: row for key, row in self.challenges.items() if row[1] > now}
            self.challenges[challenge] = (player, now + 90)
        return {"challenge": challenge}

    def finish(self, body):
        self.throttle()
        challenge = str(body.get("challenge") or "")
        if not CHALLENGE.fullmatch(challenge):
            raise LinkError(400, "Invalid Minecraft challenge")
        with self.lock:
            pending = self.challenges.pop(challenge, None)
        if not pending or pending[1] <= time.monotonic():
            raise LinkError(401, "Request a fresh code in Minecraft")
        profile = self.verify(pending[0], challenge)
        account = str((profile or {}).get("id") or "").lower()
        player = str((profile or {}).get("name") or "")
        if not UUID.fullmatch(account) or not NAME.fullmatch(player) or player.lower() != pending[0].lower():
            raise LinkError(401, "Minecraft did not verify this session")

        now = int(time.time())
        token = secrets.token_urlsafe(32)
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO accounts VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET player=excluded.player", (account, player))
            db.execute("DELETE FROM codes WHERE expires <= ? OR account = ?", (now, account))
            db.execute("DELETE FROM credentials WHERE expires <= ? OR (account = ? AND kind = 'mod')", (now, account))
            while True:
                code = secrets.token_hex(4).upper()
                if db.execute("INSERT OR IGNORE INTO codes VALUES (?, ?, ?)", (digest(code), account, now + CODE_SECONDS)).rowcount:
                    break
            db.execute("INSERT INTO credentials VALUES (?, ?, 'mod', ?)", (digest(token), account, now + 180 * 86_400))
        return {"code": code, "token": token, "player": player}

    def pair(self, body):
        self.throttle()
        code = str(body.get("code") or "").strip().upper()
        if not CODE.fullmatch(code):
            raise LinkError(400, "Enter the 8-character code from Minecraft")
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with closing(self.connect()) as db, db:
            row = db.execute("DELETE FROM codes WHERE hash = ? AND expires > ? RETURNING account", (digest(code), now)).fetchone()
            if row is None:
                raise LinkError(401, "That code expired or was already used. Run /market link again.")
            db.execute("INSERT INTO credentials VALUES (?, ?, 'browser', ?)", (digest(token), row[0], now + BROWSER_SECONDS))
        return token

    def account(self, token, kind):
        if not TOKEN.fullmatch(token):
            return None
        with closing(self.connect()) as db:
            return db.execute("SELECT accounts.id, accounts.player FROM credentials JOIN accounts ON accounts.id = credentials.account "
                              "WHERE hash = ? AND kind = ? AND expires > ?", (digest(token), kind, int(time.time()))).fetchone()

    def logout(self, token):
        if TOKEN.fullmatch(token):
            with closing(self.connect()) as db, db:
                db.execute("DELETE FROM credentials WHERE hash = ? AND kind = 'browser'", (digest(token),))
