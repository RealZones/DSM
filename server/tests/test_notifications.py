"""Isolation and delivery tests for the same Notifier used by all linked players."""
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from email.message import Message
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, Mock

from test_live import Phone, add_listing, collector_db
from test_market import CATALOG
from market.service import LINKED_WATCH_LIMIT, Service
from market.webhook import Webhooks, validate_url


ACCOUNT = "a" * 32
OTHER = "b" * 32
URL = "https://discord.com/api/webhooks/123456789012345678/fake_token"


class Notifications(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = temporary.name
        source = os.path.join(folder, "prices.db")
        self.db = collector_db(source)
        self.addCleanup(self.db.close)
        catalog = os.path.join(folder, "catalog.json")
        recipes = os.path.join(folder, "recipes.json")
        for path, data in ((catalog, CATALOG), (recipes, {})):
            with open(path, "w", encoding="utf-8") as file:
                json.dump(data, file)
        self.own = os.path.join(folder, "own.db")
        self.args = (source, catalog, recipes, self.own, ("SampleOwner",))

    def make_service(self):
        self.service = Service(*self.args)
        self.service.build()
        self.service.discord = Phone()
        self.service.webhooks = Mock()
        return self.service

    def test_existing_watches_and_hits_migrate_to_the_owner(self):
        db = sqlite3.connect(self.own)
        with db:
            db.execute("CREATE TABLE watches (id INTEGER PRIMARY KEY AUTOINCREMENT, entry TEXT, side TEXT, price REAL, filters TEXT, created INTEGER)")
            db.execute("CREATE TABLE watch_hits (watch INTEGER, ref TEXT, at INTEGER, unit REAL, count INTEGER, seller TEXT, sold INTEGER, PRIMARY KEY (watch, ref))")
            db.execute("INSERT INTO watches VALUES (7,'dye_eclipse','below',200000000,'{}',1)")
            db.execute("INSERT INTO watch_hits VALUES (7,'old',2,150000000,1,'Alice',0)")
        db.close()
        for _ in range(2):  # A restart after migrating is also safe.
            service = self.make_service()
            self.assertEqual([w["id"] for w in service.watches()], [7])
            self.assertEqual([h["ref"] for h in service.watch_hits(7)], ["old"])
            self.assertEqual(service.watches(account=ACCOUNT), [])
            self.assertEqual(service.watch_hits(7, account=ACCOUNT), [])

    def test_watch_ownership_and_notification_destinations(self):
        service = self.make_service()
        owner = service.save_watch({"entry": "dye_eclipse", "price": 240_000_000})
        guest = service.save_watch({"entry": "dye_eclipse", "price": 300_000_000}, account=ACCOUNT, player="Guest")
        service.save_notification_settings({"webhook": URL}, account=ACCOUNT)
        now = int(time.time() * 1000)
        add_listing(self.db, "owner-listing", 210_000_000, "SampleOwner", now)
        add_listing(self.db, "guest-listing", 220_000_000, "guest", now + 1)

        service.poll()
        service.poll()

        self.assertEqual([w["id"] for w in service.watches()], [owner])
        self.assertEqual([w["id"] for w in service.watches(account=ACCOUNT)], [guest])
        self.assertEqual([h["seller"] for h in service.recent_hits()], ["guest"])
        self.assertEqual([h["seller"] for h in service.recent_hits(account=ACCOUNT)], ["SampleOwner"])
        self.assertEqual(service.recent_hits(account=OTHER), [])
        self.assertEqual(service.watch_hits(guest), [])
        self.assertEqual(service.watch_hits(owner, account=ACCOUNT), [])
        with self.assertRaisesRegex(ValueError, "no such watch"):
            service.save_watch({"id": owner, "entry": "dye_eclipse", "price": 1}, account=ACCOUNT, player="Guest")
        service.drop_watch(owner, account=ACCOUNT)
        self.assertEqual([h["ref"] for h in service.watch_hits(owner)], ["guest-listing"])
        self.assertEqual(service.discord.lines, ["**Eclipse Dye** listed at **220m**, your price 240m. `/ah guest`"])
        service.webhooks.send.assert_called_once_with(
            ACCOUNT, URL, ["**Eclipse Dye** listed at **210m**, your price 300m. `/ah SampleOwner`"])

        service.save_notification_settings({"in_game": False}, account=ACCOUNT)
        self.assertEqual(service.recent_hits(account=ACCOUNT), [])
        self.assertEqual([h["ref"] for h in service.watch_hits(guest, account=ACCOUNT)], ["owner-listing"])
        self.assertEqual([h["seller"] for h in service.recent_hits()], ["guest"])

    def test_webhook_options_are_private_and_can_be_paused(self):
        service = self.make_service()
        self.assertTrue(service.notification_settings()["discord_dm"])
        self.assertFalse(service.notification_settings(account=ACCOUNT)["discord_dm_available"])
        with self.assertRaisesRegex(ValueError, "no verified Discord"):
            service.save_notification_settings({"discord_dm": True}, account=ACCOUNT)
        settings = service.save_notification_settings({"webhook": URL}, account=ACCOUNT)
        self.assertTrue(settings["webhook_configured"])
        self.assertTrue(settings["webhook_enabled"])
        self.assertNotIn(URL, json.dumps(settings))
        self.assertFalse(service.notification_settings(account=OTHER)["webhook_configured"])
        self.assertFalse(service.notification_settings()["webhook_configured"])
        paused = service.save_notification_settings({"webhook_enabled": False}, account=ACCOUNT)
        self.assertTrue(paused["webhook_configured"])
        self.assertFalse(paused["webhook_enabled"])
        self.assertIsNone(service._webhook_destination(ACCOUNT))
        service.save_notification_settings({"webhook_enabled": True}, account=ACCOUNT)
        self.assertEqual(service._webhook_destination(ACCOUNT), URL)
        cleared = service.save_notification_settings({"webhook": ""}, account=ACCOUNT)
        self.assertFalse(cleared["webhook_configured"])
        self.assertFalse(cleared["webhook_enabled"])

    def test_watch_limit_is_per_linked_account(self):
        service = self.make_service()
        body = {"entry": "dye_eclipse"}
        account = {"account": ACCOUNT, "player": "Guest"}
        for _ in range(LINKED_WATCH_LIMIT):
            watch = service.save_watch(body, **account)
        with self.assertRaisesRegex(ValueError, "you can track up to"):
            service.save_watch(body, **account)
        service.save_watch(dict(body, id=watch, price=100), **account)
        service.save_watch(body, account=OTHER, player="Second")
        service.drop_watch(watch, account=ACCOUNT)
        service.save_watch(body, **account)


class WebhookTransport(unittest.TestCase):
    def test_url_validation_rejects_untrusted_routes(self):
        self.assertEqual(validate_url(URL), URL)
        for invalid in (URL.replace("https:", "http:"), URL.replace("discord.com", "discord.com.example.org"),
                        URL.replace("discord.com", "discord.com@localhost"), URL.replace("discord.com", "discord.com:443"),
                        URL + "?thread_id=1", URL + "#fragment", URL + "/../anything"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_url(invalid)

    def test_message_uses_confirmed_post_and_suppresses_mentions(self):
        webhooks = Webhooks(lambda _: URL, Mock())
        webhooks.opener = MagicMock()
        webhooks.opener.open.return_value.__enter__.return_value.status = 200
        self.assertTrue(webhooks.deliver(ACCOUNT, URL, "@everyone"))
        request = webhooks.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, URL + "?wait=true")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"content": "@everyone", "allowed_mentions": {"parse": []}})

    def test_redirects_are_not_followed(self):
        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header("Location", "/other")
                self.end_headers()

            def log_message(self, *_):
                pass

        with HTTPServer(("127.0.0.1", 0), Redirect) as server:
            thread = threading.Thread(target=server.handle_request, daemon=True)
            thread.start()
            webhooks = Webhooks(lambda _: URL, Mock())
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/", data=b"{}")
            with self.assertRaises(urllib.error.HTTPError) as error:
                webhooks.opener.open(request, timeout=2)
            self.assertEqual(error.exception.code, 302)
            error.exception.close()
            thread.join(2)

    def test_removed_webhook_drops_queued_message_and_revoked_webhook_is_paused(self):
        destination = Mock(return_value=None)
        disable = Mock()
        webhooks = Webhooks(destination, disable)
        webhooks.opener = Mock()
        self.assertFalse(webhooks.deliver(ACCOUNT, URL, "test"))
        webhooks.opener.open.assert_not_called()
        destination.return_value = URL
        webhooks.opener.open.side_effect = urllib.error.HTTPError(URL, 404, "", Message(), io.BytesIO())
        self.assertFalse(webhooks.deliver(ACCOUNT, URL, "test"))
        disable.assert_called_once_with(ACCOUNT, URL)
