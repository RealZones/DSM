"""The code that runs unattended: the poll loop, the HTTP server and the mod's requests.

These build a small fake collector database and drive the real service against it, because a bug
here does not show on any page. It just quietly leaves prices stale.
"""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_market import CATALOG, T0, DAY, raw                   # noqa: E402
from http.server import ThreadingHTTPServer                     # noqa: E402
from market import web                                          # noqa: E402
from market.items import Catalog, learned_row                   # noqa: E402
from market.service import Service                              # noqa: E402


def collector_db(path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE sales (id TEXT PRIMARY KEY, entry TEXT, sold_at INTEGER, created_at INTEGER, price INTEGER, unit_price INTEGER, count INTEGER, "
               "bin INTEGER, bids INTEGER, seller TEXT, buyer TEXT, item TEXT, source TEXT, recorded_at INTEGER)")
    db.execute("CREATE TABLE listings (id TEXT PRIMARY KEY, entry TEXT, created_at INTEGER, end_at INTEGER, price INTEGER, unit_price INTEGER, count INTEGER, "
               "bin INTEGER, bids INTEGER, seller TEXT, item TEXT, first_seen INTEGER, last_seen INTEGER, outcome TEXT)")
    db.execute("CREATE TABLE quotes (ts INTEGER, product TEXT, best_bid REAL, best_ask REAL, buy_quantity INTEGER, sell_quantity INTEGER, traded INTEGER, book TEXT, source TEXT)")
    db.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    return db


def market_snapshot(db, observed, traded, sells):
    data = {"coverage": {"last_observed": observed}, "products": [
        {"id": "goop", "best_bid": 100, "best_ask": 110, "buy_quantity": 1, "sell_quantity": 1, "traded": traded, "buys": [],
         "sells": [{"price": p, "amount": a, "orders": 1} for p, a in sells]}]}
    db.execute("INSERT OR REPLACE INTO meta VALUES ('market_snapshot', ?)", (json.dumps(data),))
    db.commit()


def add_sale(db, sid, price, seller, buyer, at, entry="dye_eclipse"):
    db.execute("INSERT INTO sales VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (sid, entry, at, at - 60_000, price, price, 1, 1, 1, seller, buyer, json.dumps(raw(entry, "Eclipse Dye", "MYTHIC")), "market", at))
    db.commit()


def add_listing(db, lid, price, seller, at, outcome=None, entry="dye_eclipse"):
    db.execute("INSERT INTO listings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (lid, entry, at, at + 900 * DAY, price, price, 1, 1, 0, seller, json.dumps(raw(entry, "Eclipse Dye", "MYTHIC")), at, at, outcome))
    db.commit()


class Service_(object):
    """A real service over a small fake collector, one per test class, because the poll's cursors
    only move forward and tests in one class would hide each other's rows."""
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()
        cls.source = os.path.join(cls.folder, "prices.db")
        cls.db = collector_db(cls.source)
        for i in range(8):
            add_sale(cls.db, f"s{i}", 400_000_000 + i * 1_000_000, f"seller{i}", f"buyer{i}", T0 + i * 3_600_000)
        with open(os.path.join(cls.folder, "catalog.json"), "w", encoding="utf-8") as f:
            json.dump(CATALOG, f)
        with open(os.path.join(cls.folder, "recipes.json"), "w", encoding="utf-8") as f:
            json.dump({}, f)
        cls.service = Service(cls.source, os.path.join(cls.folder, "catalog.json"), os.path.join(cls.folder, "recipes.json"), os.path.join(cls.folder, "own.db"), tracked=("SampleOwner",))
        cls.service.build()
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(web.Api(cls.service, "secret")))
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.http.server_address[1]}/market/secret"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.db.close()

    def post(self, path, body):
        request = urllib.request.Request(self.base + path, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as reply:
            return json.load(reply)

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as reply:
            return json.load(reply)


class Live(Service_, unittest.TestCase):
    def test_bazaar_tape_reads_the_snapshot_and_survives_a_rebuild(self):
        market_snapshot(self.db, T0 + 1000, 500, [(110, 10), (120, 5)])
        self.service.poll()
        market_snapshot(self.db, T0 + 31_000, 504, [(110, 6), (120, 5)])
        self.service.poll()
        market_snapshot(self.db, T0 + 31_000, 504, [(110, 6), (120, 5)])
        self.service.poll()     # the same snapshot read twice must not double a fill
        rows = self.get("/api/bazaar/goop")["tape"]
        self.assertEqual([(r["side"], r["price"], r["amount"], r["proof"]) for r in rows], [("buy", 110, 4, "exact")])
        self.service.build()
        self.assertEqual(len(self.service.tape_rows(0, "goop")), 1)
        self.assertIsInstance(self.get("/api/bazaar"), dict)

    def test_a_new_listing_becomes_the_lbin_and_a_vanished_one_stops_being_it(self):
        add_listing(self.db, "l1", 300_000_000, "cheap", T0 + 9 * 3_600_000)
        self.service.poll()
        est = self.post("/api/quote", {"items": [raw("dye_eclipse", "Eclipse Dye", "MYTHIC")]})["estimates"][0]
        self.assertEqual(est["lbin"], 300_000_000)
        # Cancelled with nothing else changing, the case a cursor can miss.
        self.db.execute("UPDATE listings SET outcome = 'unobserved' WHERE id = 'l1'")
        self.db.commit()
        self.service.poll()
        est = self.post("/api/quote", {"items": [raw("dye_eclipse", "Eclipse Dye", "MYTHIC")]})["estimates"][0]
        self.assertEqual(est["lbin"], 0, "a ghost listing would cap this price until a restart")

    def test_a_new_sale_reaches_the_tracker(self):
        before = len(self.service.tracker.flips)
        add_sale(self.db, "buy1", 300_000_000, "x", "Flipper", T0 + 10 * 3_600_000)
        add_sale(self.db, "sell1", 420_000_000, "Flipper", "y", T0 + 11 * 3_600_000)
        self.service.poll()
        self.assertEqual(len(self.service.tracker.flips), before + 1)

    def test_the_mod_can_send_a_trade_and_it_is_applied_once(self):
        body = {"player": "SampleOwner", "other": "SampleBuyer", "at": T0 + 12 * 3_600_000, "got": ["§dEclipse Dye"], "gave": ["§6Coins: 300,000,000"]}
        first = self.post("/api/trade", body)
        self.assertEqual((first["new"], first["kind"]), (True, "bought"))
        self.assertFalse(self.post("/api/trade", body)["new"], "a retry from the outbox must not double count")
        held = self.get("/api/positions?player=SampleOwner")["positions"]
        self.assertEqual(len([p for p in held if p["entry"] == "dye_eclipse"]), 1)

    def test_in_game_item_without_an_entry_still_prices(self):
        item = raw("", "Eclipse Dye", "MYTHIC")
        item.pop("entry")
        est = self.post("/api/quote", {"items": [item]})["estimates"][0]
        self.assertGreater(est["value"], 0)

    def test_a_trade_survives_a_full_rebuild(self):
        self.post("/api/trade", {"player": "SampleOwner", "other": "Keeper", "at": T0 + 13 * 3_600_000, "got": ["§5Bottled Wisp"], "gave": ["§6Coins: 150,000,000"]})
        self.service.build()
        self.assertTrue(any(t["other"] == "Keeper" for t in self.service.tracker.trades))

    def test_wrong_key_looks_like_nothing_is_here(self):
        for path in ("/market/wrong/api/status", "/api/status", "/market/"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"http://127.0.0.1:{self.http.server_address[1]}{path}", timeout=5)
            self.assertEqual(caught.exception.code, 404)

    def test_every_page_answers(self):
        for path in ("status", "home", "flips?player=SampleOwner", "recent", "positions", "trades", "deals", "leaderboard?days=7", "search?q=ecl", "item/dye_eclipse", "player/Flipper",
                     "item/dye_eclipse/history?range=day", "item/dye_eclipse/history?range=all", "item/dye_eclipse/auctions", "item/dye_eclipse/auctions?type=active",
                     "player/buyer1/auctions", "player/buyer1/bids", "player/seller1/auctions", "auction/s1", "bazaar", "crafts", "crafts/dye_eclipse", "reported", "watches",
                     "flips?flag=crafts"):
            self.assertIsInstance(self.get("/api/" + path), dict, path)

    def test_deals_show_observed_units_sold_in_the_last_day(self):
        now = int(time.time() * 1000)
        add_sale(self.db, "volume-stack", 200_000_000, "a", "b", now - DAY + 60_000, entry="stacked")
        add_sale(self.db, "volume-old", 200_000_000, "a", "b", now - DAY - 60_000, entry="stacked")
        add_sale(self.db, "volume-free", 0, "a", "b", now - 60_000, entry="stacked")
        self.db.execute("UPDATE sales SET count = 4 WHERE id = 'volume-stack'")
        self.db.commit()
        try:
            deals = {
                "a": dict(entry="stacked", profit=100, volume=1.25),
                "b": dict(entry="quiet", profit=50, volume=0.5),
            }
            with patch.object(self.service, "deals", deals):
                rows = self.get("/api/deals")["deals"]
            self.assertEqual([(r["units_sold_24h"], r["volume"]) for r in rows], [(4, 1.25), (0, 0.5)])
        finally:
            self.db.execute("DELETE FROM sales WHERE id IN ('volume-stack', 'volume-old', 'volume-free')")
            self.db.commit()

    def test_search_shows_a_pet_once_and_opens_the_rarity_that_trades(self):
        catalog = self.service.catalog
        catalog.items.update({"bal_pet_epic": {"name": "[Lvl 1] Bal", "tier": "EPIC"}, "bal_pet_legendary": {"name": "[Lvl 1] Bal", "tier": "LEGENDARY"}})
        catalog.litters["bal"] = ["bal_pet_epic", "bal_pet_legendary"]
        try:
            rows = [r for r in self.get("/api/search?q=bal")["items"] if r["name"] == "Bal"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(self.get("/api/item/bal_pet_epic")["litter"]), 2)
        finally:
            del catalog.items["bal_pet_epic"], catalog.items["bal_pet_legendary"], catalog.litters["bal"]

    def test_a_filter_works_on_every_field_and_gear_always_offers_recomb(self):
        catalog = self.service.catalog
        catalog.items["warden_helmet"]["category"] = "HELMET"
        plain_one = raw("warden_helmet", "Warden Helmet", "LEGENDARY", mods={"hot_potato_books": 10})
        rich_one = raw("warden_helmet", "Warden Helmet", "LEGENDARY", mods={"hot_potato_books": 10, "recombobulated": True, "star_level": 5})
        for sid, item, at in (("h1", plain_one, T0 - 3_600_000), ("h2", rich_one, T0 - 7_200_000)):
            self.db.execute("INSERT INTO sales VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (sid, "warden_helmet", at, at - 1000, 100, 100, 1, 1, 1, "a", "b", json.dumps(item), "market", at))
        self.db.commit()
        filters = self.get("/api/item/warden_helmet")["filters"]
        self.assertIn("yes", [v for v, _ in filters["recomb"]])
        self.assertIn("10", [v for v, _ in filters["hpb"]])
        self.assertIn("5", [v for v, _ in filters["stars"]])
        self.assertIn("aow", filters)
        self.assertIn("none", [v for v, _ in filters["dye"]])
        self.assertEqual([a["id"] for a in self.get("/api/item/warden_helmet/auctions?recomb=yes")["auctions"]], ["h2"])
        self.assertEqual([a["id"] for a in self.get("/api/item/warden_helmet/auctions?recomb=no&stars=0")["auctions"]], ["h1"])
        self.assertEqual(self.get("/api/item/warden_helmet/auctions?hpb=3")["auctions"], [])

    def test_auction_outcomes_only_count_confirmed_sales(self):
        for outcome in ("expired", "cancelled", "unobserved"):
            add_listing(self.db, "outcome-" + outcome, 9_000_000_000, "outcome_seller", T0 + DAY, outcome)
            self.assertEqual(self.get("/api/auction/outcome-" + outcome)["status"], outcome)
        self.assertEqual(self.get("/api/auction/s1")["status"], "sold")
        player = self.get("/api/player/outcome_seller/auctions")["auctions"]
        self.assertEqual({row["status"] for row in player}, {"expired", "cancelled", "unobserved"})
        sold = self.get("/api/item/dye_eclipse/auctions?type=sold")["auctions"]
        self.assertFalse(any(row["id"].startswith("outcome-") for row in sold))

    def test_player_pages_match_what_they_did(self):
        sold = self.get("/api/player/seller1/auctions")["auctions"]
        self.assertTrue(any(a["id"] == "s1" and a["status"] == "sold" for a in sold))
        bought = self.get("/api/player/buyer1/bids")["auctions"]
        self.assertEqual(bought[0]["id"], "s1")
        self.assertEqual(bought[0]["seller"], "seller1")

    def test_chart_keeps_a_gift_out_of_the_low_line(self):
        add_sale(self.db, "gift", 1, "friend", "pal", T0 + 3 * 3_600_000)
        rows = self.service.engine.now, self.get("/api/item/dye_eclipse/history?range=all")["rows"]
        self.assertGreater(min(r["min"] for r in rows[1]), 1_000_000)

    def test_filters_narrow_the_chart(self):
        self.assertEqual(self.get("/api/item/dye_eclipse/history?range=all&rarity=COMMON")["rows"], [])
        self.assertGreater(len(self.get("/api/item/dye_eclipse/history?range=all&rarity=MYTHIC")["rows"]), 0)

    def test_bad_request_is_refused_cleanly(self):
        request = urllib.request.Request(self.base + "/api/trade", b"not json", {"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 400)

    def test_a_reforge_fee_uses_what_the_anvil_charged(self):
        s = self.service
        with s.lock:
            s.tracker.on_chat("Anvil", T0 + 1000, "§aYou applied the Renowned reforge to your item! §c(-9,000,000 coins)")
            stock = s.tracker.stocks["Anvil"]
        self.assertEqual(stock.reforges[0][1:3], ["renowned", 9_000_000])

    def test_profile_scan_values_items_and_reports_hidden(self):
        s = self.service
        profile = {"profile": {"coins": 5, "inventory": {"3": raw("dye_eclipse", "Eclipse Dye", "MYTHIC")}, "ender_chest": {"0": raw("dye_eclipse", "Eclipse Dye", "MYTHIC")}}}
        result = s.value_profile(profile, "Someone")
        self.assertEqual((len(result["items"]), result["items"][0]["count"]), (1, 2), "two copies of one item are one row")
        self.assertTrue(s.value_profile({"profile": {"inventory_hidden": True}}, "Shy")["hidden"])

    def test_own_trades_can_be_backed_up(self):
        self.service.backup()
        folder = os.path.join(self.folder, "backups")
        self.assertEqual(len(os.listdir(folder)), 1)


class Phone:
    def __init__(self):
        self.lines = []

    def send(self, lines):
        self.lines += lines


class Favorites(Service_, unittest.TestCase):
    """The price change endpoint, which the favorites on the start page read. Its rows are dated from
    today, since the month it looks over ends now."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        now = int(time.time() * 1000)

        def sale(sid, price, at, mods=None):
            cls.db.execute("INSERT INTO sales VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (sid, "warden_helmet", at, at - 60_000, price, price, 1, 1, 1, "a", "b",
                            json.dumps(raw("warden_helmet", "Warden Helmet", mods=mods)), "market", now))

        sale("w1", 100_000_000, now - DAY)
        sale("w2", 80_000_000, now - 3 * DAY)
        sale("w3", 100_000_000, now - 3 * DAY)
        sale("w4", 120_000_000, now - 5 * DAY)
        sale("w5", 1, now - 5 * DAY)                                        # a gift
        sale("w6", 300_000_000, now - DAY, {"recombobulated": 1})          # not clean
        sale("w7", 999_000_000, now - 40 * DAY)                             # before the month
        book = json.dumps({"buys": [{"price": 40, "amount": 5}], "sells": [{"price": 50, "amount": 5}]})
        for ts, ask in ((now - 2 * DAY, 50), (now - DAY, 70)):
            cls.db.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?)", (ts, "frozen_frag", 40, ask, 1, 1, 10, book, "market"))
        cls.db.commit()
        cls.service.poll()

    def test_an_item_is_priced_now_against_its_median_and_its_month(self):
        rows = self.get("/api/prices/change?itemTags=warden_helmet,no_such_item&itemTags=frozen_frag")
        self.assertEqual(sorted(rows), ["frozen_frag", "warden_helmet"], "an unknown item is left out")
        helmet = rows["warden_helmet"]
        # One clean sale in two days is too few, so a fortnight's: 120m, 100m, 100m, 80m and the
        # gift, middle 100m. The recombed one is not clean and the 999m one is older than that.
        self.assertEqual(helmet["recent"], 100_000_000)
        self.assertEqual(helmet["now"], helmet["recent"], "nothing listed reads as no change, the current price falls back to recent")
        # Days of 100m, 90m and, once the 1 coin gift is dropped, 120m.
        self.assertEqual(helmet["monthly"], 100_000_000)
        self.assertEqual(helmet["lastUpdated"], self.service.engine.now)
        product = rows["frozen_frag"]
        median = self.service.engine.estimate(web.Api(self.service, "k")._probe("frozen_frag"))["median"]
        self.assertEqual((product["now"], product["recent"], product["monthly"], product["bazaar"]), (70, round(median), 70, True),
                         "a product is its Buy now, against the engine's bazaar price and the month's Buy")

        now = int(time.time() * 1000)
        self.db.execute("INSERT INTO listings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("wl1", "warden_helmet", now, now + DAY, 90_000_000, 90_000_000, 1, 1, 0, "someone",
                         json.dumps(raw("warden_helmet", "Warden Helmet")), now, now, None))
        self.db.commit()
        self.service.poll()
        self.assertEqual(self.get("/api/prices/change?itemTags=warden_helmet")["warden_helmet"]["now"], 90_000_000)

    def test_the_month_is_the_median_of_daily_averages(self):
        self.assertIsNone(web.month_median([]))
        self.assertEqual(web.month_median([4, 1, 3, 2]), 3, "Skip(count / 2) takes the upper middle")
        self.assertEqual(web.month_median([0, 5]), 5)
        self.assertEqual(web.daily_averages([(0, 10, 1), (1, 30, 3), (DAY, 7, 1)]), [25, 7])
        now = 100 * DAY
        self.assertEqual(web.recent_median([(now - 1, p, 1) for p in (10, 20, 30, 40)], now), 20, "highest first, so the lower middle")
        self.assertEqual(web.recent_median([(now - 1, 50, 1), (now - 5 * DAY, 10, 1), (now - 6 * DAY, 30, 1)], now), 30,
                         "under three sales in two days looks back a fortnight")
        self.assertIsNone(web.recent_median([(now - 20 * DAY, 10, 1)], now))
        self.assertTrue(web.clean(web.from_json(raw("warden_helmet", reforge="ancient"))), "a clean copy may carry a reforge")
        self.assertFalse(web.clean(web.from_json(raw("warden_helmet", mods={"recombobulated": 1}))))


class NewItems(Service_, unittest.TestCase):
    """An id the catalog does not have joins it the moment the feed shows one. The Colossus skins
    of 2026-11-08 sold for a day with no search result, Notifier or favourite."""

    def test_a_new_item_is_added_from_its_first_copy_and_kept(self):
        now = int(time.time() * 1000)
        skin = raw("colossus_helmet_skin", "Colossus Helmet Skin")
        skin["item_data"]["item_type"] = "COSMETIC"
        self.db.execute("INSERT INTO listings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("n1", "colossus_helmet_skin", now, now + DAY, 2_000_000_000, 2_000_000_000, 1, 1, 0, "someone",
                         json.dumps(skin), now, now, None))
        blade = raw("new_blade", "§6Withered New Blade ✪✪", rarity="MYTHIC", reforge="Withered", mods={"recombobulated": 1})
        blade["item_skin"] = {"value": "abc"}
        self.db.execute("INSERT INTO sales VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("n2", "new_blade", now, now - 60_000, 5_000_000, 5_000_000, 1, 1, 1, "a", "b", json.dumps(blade), "market", now))
        self.db.commit()
        self.assertNotIn("colossus_helmet_skin", self.service.catalog.items)
        self.service.poll()

        catalog = self.service.catalog
        self.assertIs(self.service.engine.catalog, catalog)
        self.assertIs(self.service.reports.catalog, catalog)
        self.assertEqual((catalog.name("colossus_helmet_skin"), catalog.tier("colossus_helmet_skin"),
                          catalog.items["colossus_helmet_skin"]["category"]), ("Colossus Helmet Skin", "LEGENDARY", "COSMETIC"))
        self.assertEqual((catalog.name("new_blade"), catalog.tier("new_blade")), ("New Blade", "LEGENDARY"),
                         "no reforge or stars in the name, and the rarity from before its recombobulator")
        self.assertEqual(catalog.items["new_blade"]["skin"], {"value": "abc"}, "a skull keeps its head, so the site can draw it")
        self.assertEqual(learned_row(raw("new_rune", "◆ New Rune III"))["name"], "New Rune", "a rune's id is every level")
        self.assertEqual([i["entry"] for i in self.get("/api/search?q=colossus")["items"]], ["colossus_helmet_skin"])
        self.assertIn("colossus_helmet_skin", self.get("/api/prices/change?itemTags=colossus_helmet_skin"))

        again = Catalog.load(os.path.join(self.folder, "catalog.json"))
        self.assertIn("colossus_helmet_skin", again.items, "a restart keeps it")
        listed = dict(CATALOG, items=dict(CATALOG["items"], new_blade={"name": "New Blade", "tier": "EPIC"}))
        self.assertEqual(Catalog(listed, again.extra).tier("new_blade"), "EPIC", "dragsim.co's own entry wins once it lists one")

    def test_a_new_item_is_drawn_as_its_base_minecraft_item(self):
        # 2026-11-10: 83 of the 121 learned items showed a letter, because a row kept only a head's picture.
        now = int(time.time() * 1000)
        bow = raw("death_bow", "Death Bow")
        bow["material"] = "minecraft:bow"
        self.db.execute("INSERT INTO listings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("p1", "death_bow", now, now + DAY, 9_000_000, 9_000_000, 1, 1, 0, "someone", json.dumps(bow), now, now, None))
        self.db.commit()
        self.service.poll()
        self.assertEqual(self.service.catalog.items["death_bow"]["material"], "bow")
        self.assertEqual(self.get("/api/search?q=death%20bow")["items"][0]["icon"],
                         {"url": "https://dragsim.co/assets/vanilla/item-bow.png", "alt": "https://dragsim.co/assets/vanilla/block-bow.png"},
                         "a base item dragsim.co has never drawn is tried flat, then as a block")

        listed = dict(CATALOG, items=dict(CATALOG["items"],
                                          longbow={"name": "Longbow", "material": "bow", "icon": "/assets/vanilla/item-bow.png"},
                                          haunted_candy={"name": "Haunted Candy", "material": "orange_dye", "icon": "/assets/items/haunted_candy.png"},
                                          listed_bow={"name": "Listed Bow", "material": "bow"}))
        head = raw("mana_draught", "Mana Draught")
        head.update(material="minecraft:player_head", item_skin={"value": "abc"})
        dye = raw("new_dye", "New Dye")
        dye["material"] = "minecraft:orange_dye"
        block = raw("minecraft:lime_terracotta", "Lime Terracotta")
        catalog = Catalog(listed, {r["entry"]: learned_row(r) for r in (bow, head, dye, block)})
        self.assertEqual(catalog.vanilla_picture("death_bow"), ("/assets/vanilla/item-bow.png", None), "dragsim.co's own bow, once it has one")
        self.assertEqual(catalog.vanilla_picture("new_dye")[0], "/assets/vanilla/item-orange_dye.png", "a hand-drawn picture is not the base item's")
        self.assertEqual(catalog.vanilla_picture("minecraft:lime_terracotta")[1], "/assets/vanilla/block-lime_terracotta.png",
                         "a Minecraft id is its own base item")
        self.assertIsNone(catalog.vanilla_picture("mana_draught"), "a head is drawn from its skin")
        self.assertIsNone(catalog.vanilla_picture("listed_bow"), "an item dragsim.co lists stays as it shows it")

    def test_a_new_item_in_dragsims_pack_shows_its_own_model(self):
        # 2026-11-11: Colossus armour showed as iron and its skins as paper; in game both are pack models.
        from test_pictures import build_pack
        now = int(time.time() * 1000)
        helm = raw("test_helm", "Test Helm")
        helm["material"] = "minecraft:iron_helmet"
        self.db.execute("INSERT INTO listings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("q1", "test_helm", now, now + DAY, 7_000_000, 7_000_000, 1, 1, 0, "someone", json.dumps(helm), now, now, None))
        self.db.commit()
        self.service.poll()
        build_pack(self.service.pack_path)
        try:
            self.service.draw_pictures()
            self.assertEqual(self.service.pictures, {"test_helm"}, "only items the site learned are drawn")
            self.assertEqual(self.get("/api/search?q=test%20helm")["items"][0]["icon"], {"url": "pictures/test_helm.png"},
                             "the pack's model, ahead of the iron helmet underneath")
            with urllib.request.urlopen(self.base + "/pictures/test_helm.png", timeout=5) as reply:
                self.assertEqual(reply.headers["Content-Type"], "image/png")
                self.assertTrue(reply.read().startswith(b"\x89PNG"))
            for missing in ("/pictures/flat_thing.png", "/pictures/..%2Fcatalog.png"):
                with self.assertRaises(urllib.error.HTTPError, msg=missing) as caught:
                    urllib.request.urlopen(self.base + missing, timeout=5)
                self.assertEqual(caught.exception.code, 404)
        finally:
            self.service.pictures = frozenset()

    def test_the_bots_price_fills_what_nothing_traded_prices(self):
        path = os.path.join(self.folder, "index.json")      # beside the collector's database, as on the VPS
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"rows": [{"id": "necron_handle", "price": 114_000_000_000, "source": "manual"},
                                    {"id": "nothing", "price": None}]}, f)
            self.service.use_bot_prices()
            self.assertEqual(self.service.bot, {"necron_handle": 114_000_000_000})
            self.assertEqual(self.service.engine.price_of("necron_handle"), (114_000_000_000, True))
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            self.assertEqual(self.service.bot_prices(), {}, "a broken file means no bot prices, not a service that will not start")
        finally:
            os.remove(path)
            self.service.use_bot_prices()

    def test_the_pack_is_fetched_again_only_when_it_changes(self):
        import io
        import zipfile
        from test_pictures import build_pack
        served = os.path.join(self.folder, "served.zip")
        build_pack(served)
        with open(served, "rb") as f:
            body = f.read()
        asked = []

        class Reply(io.BytesIO):
            headers = {"ETag": '"v1"'}

        def fake(request, timeout):
            asked.append(request.get_header("If-none-match"))
            if asked[-1] == '"v1"':
                raise urllib.error.HTTPError(request.full_url, 304, "Not Modified", {}, None)
            return Reply(body)

        with patch("urllib.request.urlopen", fake):
            self.assertTrue(self.service.fetch_pack())
            self.assertFalse(self.service.fetch_pack(), "an unchanged pack is not downloaded again")
        self.assertEqual(asked, [None, '"v1"'])
        with zipfile.ZipFile(self.service.pack_path) as pack:
            self.assertIn("assets/test/items/test_helm.json", pack.namelist())

    def test_items_people_own_are_added_before_anyone_lists_one(self):
        # The 2026-11-10 dungeon update: Nightfall sat in the top players' inventories with none on the AH.
        import gzip
        blade = raw("awakened_duskblade", "§6Nightfall", "LEGENDARY")
        blade["item_data"]["item_type"] = "SWORD"
        placeholder = raw("leg_edrag", "leg_edrag", "COMMON")
        profile = {"inventory": {"0": blade, "1": placeholder}, "storage": {"1": [raw("warden_helmet", "Warden Helmet")]}}
        db = sqlite3.connect(os.path.join(self.folder, "profiles.db"))
        db.execute("CREATE TABLE snapshots (profile_id TEXT, updated_at INTEGER, uuid TEXT, ign TEXT, profile_name TEXT, mode TEXT, "
                   "fetched_at INTEGER, hidden INTEGER, data BLOB)")
        db.execute("INSERT INTO snapshots VALUES ('p1', 0, 'u', 'Someone', 'Apple', '', 1000, 0, ?)",
                   (gzip.compress(json.dumps({"profile": profile}).encode()),))
        db.commit()
        db.close()
        self.service.learn_from_profiles()
        catalog = self.service.catalog
        self.assertEqual((catalog.name("awakened_duskblade"), catalog.tier("awakened_duskblade"),
                          catalog.items["awakened_duskblade"]["category"]), ("Nightfall", "LEGENDARY", "SWORD"))
        self.assertNotIn("leg_edrag", catalog.items, "a copy named with its own id is a placeholder")
        self.assertEqual([i["entry"] for i in self.get("/api/search?q=nightfall")["items"]], ["awakened_duskblade"])
        learned = len(self.service.catalog.extra)
        self.service.learn_from_profiles()
        self.assertEqual(len(self.service.catalog.extra), learned, "a snapshot is read once")


class Notifier(Service_, unittest.TestCase):
    clock = T0 + 100 * 3_600_000

    def setUp(self):
        self.phone = self.service.discord = Phone()

    def at(self):
        """The poll only reads forward, so every row is newer than the last whatever order tests run in."""
        Notifier.clock += 60_000
        return Notifier.clock

    def test_a_listing_buzzes_once_with_where_to_buy_it(self):
        ref = self.watch(side="below", price=250_000_000)
        add_listing(self.db, "buzz1", 200_000_000, "seller5", self.at())
        self.service.poll()
        self.service.poll()
        self.assertEqual(self.phone.lines, ["**Eclipse Dye** listed at **200m**, your price 250m. `/ah seller5`"])
        self.post("/api/watch/delete", {"id": ref})
        self.db.execute("UPDATE listings SET outcome = 'unobserved' WHERE id = 'buzz1'")
        self.db.commit()
        self.service.poll()

    def test_a_sale_you_missed_stays_on_the_page(self):
        ref = self.watch(side="below", price=250_000_000)
        add_sale(self.db, "gone1", 100_000_000, "a", "b", self.at())
        self.service.poll()
        self.assertEqual(self.phone.lines, [])
        self.assertEqual([h["sold"] for h in self.row(ref)["hits"]], [True])
        self.post("/api/watch/delete", {"id": ref})

    def test_a_sale_over_your_price_buzzes(self):
        ref = self.watch(side="above", price=900_000_000)
        add_sale(self.db, "big2", 2_500_000_000, "a", "b", self.at())
        self.service.poll()
        self.assertEqual(self.phone.lines, ["**Eclipse Dye** sold for **2.5b**, your price 900m"])
        self.post("/api/watch/delete", {"id": ref})

    def test_a_flood_of_listings_is_a_few_lines(self):
        ref = self.watch(side="below", price=250_000_000)
        for i in range(9):
            add_listing(self.db, f"flood{i}", 200_000_000 + i, f"seller{i}", self.at())
        self.service.poll()
        self.assertEqual(len(self.phone.lines), 6)
        self.assertEqual(self.phone.lines[-1], "and 4 more Eclipse Dye")
        self.post("/api/watch/delete", {"id": ref})
        for i in range(9):
            self.db.execute("UPDATE listings SET outcome = 'unobserved' WHERE id = ?", (f"flood{i}",))
        self.db.commit()
        self.service.poll()

    def watch(self, **body):
        return self.post("/api/watch", dict(entry="dye_eclipse", **body))["id"]

    def row(self, ref):
        return next(w for w in self.get("/api/watches")["watches"] if w["id"] == ref)

    def test_a_listing_at_your_price_hits_once_and_can_be_bought(self):
        ref = self.watch(side="below", price=250_000_000)
        add_listing(self.db, "cheap1", 200_000_000, "seller9", self.at())
        self.service.poll()
        self.service.poll()
        row = self.row(ref)
        self.assertEqual([m["seller"] for m in row["matches"]], ["seller9"])
        self.assertEqual(len(row["hits"]), 1, "a listing that stays up is one alert, not one each poll")
        self.assertEqual(self.get("/api/status")["alerts"], 1)
        watch = self.get("/api/deals")["watch"]
        self.assertEqual([(w["unit"], w["seller"]) for w in watch], [(200_000_000, "seller9")])
        self.post("/api/watch/delete", {"id": ref})
        self.assertEqual(self.get("/api/deals")["watch"], [])
        self.db.execute("UPDATE listings SET outcome = 'unobserved' WHERE id = 'cheap1'")
        self.db.commit()
        self.service.poll()

    def test_a_listing_over_your_price_stays_quiet(self):
        ref = self.watch(side="below", price=100)
        add_listing(self.db, "dear1", 400_000_000, "seller8", self.at())
        self.service.poll()
        self.assertEqual(self.row(ref)["matches"], [])
        self.assertEqual(self.row(ref)["hits"], [])
        self.post("/api/watch/delete", {"id": ref})
        self.db.execute("UPDATE listings SET outcome = 'unobserved' WHERE id = 'dear1'")
        self.db.commit()
        self.service.poll()

    def test_your_own_listing_is_not_an_alert(self):
        ref = self.watch(side="below", price=250_000_000)
        add_listing(self.db, "mine1", 200_000_000, "SampleOwner", self.at())
        self.service.poll()
        self.assertEqual(self.row(ref)["matches"], [])
        self.post("/api/watch/delete", {"id": ref})
        self.db.execute("UPDATE listings SET outcome = 'unobserved' WHERE id = 'mine1'")
        self.db.commit()
        self.service.poll()

    def test_a_sale_over_your_price_hits_when_you_are_selling(self):
        ref = self.watch(side="above", price=900_000_000)
        add_sale(self.db, "big1", 950_000_000, "a", "b", self.at())
        add_sale(self.db, "small1", 100_000_000, "a", "c", self.at())
        self.service.poll()
        hits = self.row(ref)["hits"]
        self.assertEqual([(h["unit"], h["sold"]) for h in hits], [(950_000_000, True)])
        self.assertEqual(self.get("/api/deals")["watch"], [], "a sale cannot be bought, so the mod has nothing to say")
        self.post("/api/watch/delete", {"id": ref})

    def test_tracking_with_no_price_stays_quiet_and_survives_a_rebuild(self):
        ref = self.watch()
        add_listing(self.db, "any1", 1_000_000, "seller7", self.at())
        self.service.poll()
        self.assertEqual(self.row(ref)["hits"], [])
        self.service.build()
        self.assertEqual(self.row(ref)["price"], None)
        self.post("/api/watch/delete", {"id": ref})
        self.db.execute("UPDATE listings SET outcome = 'unobserved' WHERE id = 'any1'")
        self.db.commit()
        self.service.poll()

    def test_changing_the_price_clears_what_the_old_one_caught(self):
        ref = self.watch(side="below", price=250_000_000)
        add_listing(self.db, "cheap2", 200_000_000, "seller6", self.at())
        self.service.poll()
        self.assertEqual(len(self.row(ref)["hits"]), 1)
        self.post("/api/watch", {"entry": "dye_eclipse", "id": ref, "side": "below", "price": 100})
        self.assertEqual(self.row(ref)["hits"], [])
        self.assertEqual(self.row(ref)["matches"], [])
        self.post("/api/watch/delete", {"id": ref})
        self.db.execute("UPDATE listings SET outcome = 'unobserved' WHERE id = 'cheap2'")
        self.db.commit()
        self.service.poll()

    def test_an_item_that_does_not_exist_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/watch", {"entry": "not_a_real_item", "price": 5})
        self.assertEqual(caught.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
