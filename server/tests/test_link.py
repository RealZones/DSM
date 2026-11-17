import json
import os
import threading
import urllib.error
import urllib.request
import unittest
from http.server import ThreadingHTTPServer

from test_live import Service_
from test_market import raw
from market import web
from market.account_tracker import AccountTrackers
from market.link import Pairing


class LinkedMarket(Service_, unittest.TestCase):
    origin = "https://market.example"
    identities = {"SampleOwner": "0" * 32, "Friend": "1" * 32, "Other": "2" * 32}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        def verify(name, challenge):
            if name in cls.identities:
                return {"id": cls.identities[name], "name": name}

        cls.pairing = Pairing(os.path.join(cls.folder, "link-test.db"), cls.origin, cls.identities["SampleOwner"], verify=verify)
        handler = web.make_handler(web.Api(cls.service, "secret"), cls.pairing, AccountTrackers(cls.service))
        cls.link_http = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.link_http.serve_forever, daemon=True).start()
        cls.link_base = f"http://127.0.0.1:{cls.link_http.server_address[1]}/market/link/"

    @classmethod
    def tearDownClass(cls):
        cls.link_http.shutdown()
        cls.link_http.server_close()
        super().tearDownClass()

    def request(self, path, body=None, token=None, cookie=None, origin=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if cookie:
            headers["Cookie"] = cookie
        if origin:
            headers["Origin"] = origin
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.link_base + path, data=data, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.code, response.headers, json.load(response)

    def link(self, name):
        code, _, start = self.request("api/start", {"player": name})
        self.assertEqual(code, 200)
        code, _, reply = self.request("api/verify", start)
        self.assertEqual(code, 200)
        return start, reply

    def test_linked_clients_can_only_read_or_modify_their_own_data(self):
        self.assertEqual(self.request("api/trades")[0], 401)
        _, _, start = self.request("api/start", {"player": "Unverified"})
        self.assertEqual(self.request("api/verify", start)[0], 401)
        self.service.save_watch({"entry": "dye_eclipse", "price": 100})
        _, owner = self.link("SampleOwner")
        start, friend = self.link("Friend")
        _, other = self.link("Other")
        self.assertEqual(self.request("api/verify", start)[0], 401)
        self.assertEqual(self.request("api/pair", {"code": friend["code"]}, origin="https://untrusted.example")[0], 403)
        code, headers, _ = self.request("api/pair", {"code": friend["code"].lower()}, origin=self.origin)
        self.assertEqual(code, 200)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("Secure", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        browser = headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(self.request("api/pair", {"code": friend["code"]})[0], 401)
        self.assertEqual(self.request("api/home?player=SampleOwner", cookie=browser)[2]["player"], "Friend")
        self.assertEqual(self.request("api/watches", cookie=browser)[2]["watches"], [])
        self.assertEqual(list(self.request("api/prices/change?itemTags=dye_eclipse", cookie=browser)[2]), ["dye_eclipse"],
                         "a friend's favorites read the same prices as the owner's")
        self.assertEqual([w["price"] for w in self.request("api/watches", token=owner["token"])[2]["watches"]], [100])
        _, _, watch = self.request("api/watch", {"entry": "dye_eclipse", "price": 200}, cookie=browser)
        self.assertEqual(self.request("api/watch/delete", watch, token=other["token"])[0], 200)
        self.assertEqual([w["id"] for w in self.request("api/watches", cookie=browser)[2]["watches"]], [watch["id"]])
        self.assertEqual(self.request("api/watches", token=other["token"])[2]["watches"], [])
        _, _, settings = self.request("api/notification-settings", {"webhook": "https://discord.com/api/webhooks/123/linkTest"}, cookie=browser)
        self.assertTrue(settings["webhook_configured"])
        self.assertNotIn("linkTest", json.dumps(settings))
        self.assertFalse(self.request("api/notification-settings", token=other["token"])[2]["webhook_configured"])
        receipt = {"player": "SampleOwner", "other": "Partner", "got": ["§dEclipse Dye"], "gave": ["§6Coins: 300,000,000"]}
        self.assertEqual(self.request("api/trade", receipt, cookie=browser)[0], 403)
        self.assertEqual(self.request("api/trade", receipt, token=friend["token"])[0], 200)
        self.assertEqual([r["player"] for r in self.request("api/trades?player=SampleOwner", cookie=browser)[2]["trades"]], ["Friend"])
        self.assertEqual(self.request("api/trades?player=Friend", token=other["token"])[2]["trades"], [])
        self.assertEqual(self.get("/api/trades")["trades"], [])
        self.assertEqual(self.request("api/logout", {}, cookie=browser)[0], 200)
        self.assertEqual(self.request("api/account", cookie=browser)[0], 401)
        self.link("Friend")
        self.assertEqual(self.request("api/account", token=friend["token"])[0], 401)

    def test_signed_out_visitors_read_the_market_but_nothing_personal(self):
        with urllib.request.urlopen(self.link_base, timeout=5) as reply:
            self.assertIn(b'id="rail"', reply.read(), "the whole site loads; signing in is a page inside it")
        code, _, status = self.request("api/status")
        self.assertEqual((code, status["signed_in"], status["tracked"], status["alerts"]), (200, False, [], 0))
        code, _, deals = self.request("api/deals")
        self.assertEqual((code, deals["watch"]), (200, []), "nobody's Notifier hits")
        home = self.request("api/home")[2]
        self.assertIsNone(home["player"])
        self.assertNotIn("day", home, "the owner's profit is his own")
        self.assertEqual(self.request("api/item/dye_eclipse")[0], 200)
        self.assertEqual(list(self.request("api/prices/change?itemTags=dye_eclipse")[2]), ["dye_eclipse"])
        self.assertIn("seller1", self.request("api/search?q=seller")[2]["players"], "players from the AH, not from anyone's tracker")
        for path in ("api/flips", "api/trades", "api/positions", "api/watches", "api/notification-settings", "api/leaderboard",
                     "api/scan/SampleOwner", "api/account"):
            self.assertEqual(self.request(path)[0], 401, path)
        self.assertEqual(self.request("api/watch", {"entry": "dye_eclipse", "price": 1})[0], 401)

    def test_one_odd_item_does_not_blank_the_rest_of_a_batch(self):
        _, friend = self.link("Friend")
        good = raw("dye_eclipse", "Eclipse Dye", "MYTHIC")
        code, _, reply = self.request("api/quote", {"items": [good, "not an item", good]}, token=friend["token"])
        self.assertEqual(code, 200)
        owner = self.post("/api/quote", {"items": [good]})["estimates"][0]
        self.assertEqual([row["value"] for row in reply["estimates"]], [owner["value"], 0, owner["value"]])
        self.assertEqual(reply["estimates"][1]["count"], 1)
        self.assertEqual(reply["estimates"][0]["lore"], owner["lore"])
        self.assertTrue(owner["lore"] and all(line.startswith("§7") for line in owner["lore"]), owner["lore"])
        self.assertNotIn("lore", reply["estimates"][1])
