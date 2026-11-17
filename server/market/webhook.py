"""Send Notifier messages to Discord without letting network delays hold the market lock."""
import json
import math
import queue
import re
import threading
import time
import urllib.error
import urllib.request

from .discord import AGENT, split

DISCORD_URL = re.compile(r"https://discord\.com/api/webhooks/[0-9]{1,20}/[A-Za-z0-9_-]{1,200}")


def validate_url(value):
    if not isinstance(value, str):
        raise ValueError("invalid Discord webhook URL")
    url = value.strip()
    if not url:
        return None
    if not DISCORD_URL.fullmatch(url):
        raise ValueError("invalid Discord webhook URL")
    return url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Webhooks:
    def __init__(self, destination, disable):
        self.destination = destination
        self.disable = disable
        self.opener = urllib.request.build_opener(NoRedirect())
        self.pending = queue.Queue(maxsize=128)
        self.worker = None

    def send(self, account, url, lines):
        for content in split(lines):
            try:
                self.pending.put_nowait((account, url, content))
            except queue.Full:
                print("Discord webhook queue full", flush=True)
                break
        if self.worker is None:
            self.worker = threading.Thread(target=self._run, name="market-webhooks", daemon=True)
            self.worker.start()

    def _run(self):
        while True:
            account, url, content = self.pending.get()
            self.deliver(account, url, content)

    def deliver(self, account, url, content):
        if not DISCORD_URL.fullmatch(url):
            return False
        request = urllib.request.Request(
            url + "?wait=true",
            json.dumps({"content": content, "allowed_mentions": {"parse": []}}).encode("utf-8"),
            {"Content-Type": "application/json", "User-Agent": AGENT},
            method="POST",
        )
        for attempt in range(3):
            try:
                # If an account pauses or replaces its webhook, discard queued messages for the old one.
                if url != self.destination(account):
                    return False
                with self.opener.open(request, timeout=8) as response:
                    return 200 <= response.status < 300
            except urllib.error.HTTPError as error:
                code = error.code
                retry_after = error.headers.get("Retry-After")
                error.close()
                if code != 429 and code < 500:
                    if code in (401, 404):
                        self.disable(account, url)
                    print("Discord webhook refused", code, flush=True)
                    return False
                try:
                    wait = float(retry_after)
                except (TypeError, ValueError):
                    wait = 2 ** attempt
            except OSError:
                wait = 2 ** attempt
            if attempt == 2 or not math.isfinite(wait) or wait > 30:
                break
            time.sleep(max(wait, 0))
        print("Discord webhook delivery failed", flush=True)
        return False
