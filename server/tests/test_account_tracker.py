import json
import os
import tempfile
import unittest

from test_live import CATALOG, T0, add_sale, collector_db
from market.account_tracker import AccountTrackers, validate_linked_item
from market.service import Service


class AccountTrackerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = temporary.name
        source, catalog, recipes, own = [os.path.join(self.directory, name) for name in
                                         ("prices.db", "catalog.json", "recipes.json", "own.db")]
        self.db = collector_db(source)
        self.addCleanup(self.db.close)
        bow_catalog = dict(CATALOG, items=dict(CATALOG["items"], mosquito_shortbow={"name": "Mosquito Shortbow", "tier": "LEGENDARY"}),
                           reforges=dict(CATALOG["reforges"], nebulous=[]))
        for path, body in ((catalog, bow_catalog), (recipes, {})):
            with open(path, "w", encoding="utf-8") as out:
                json.dump(body, out)
        self.service = Service(source, catalog, recipes, own, tracked=("SampleOwner",))
        self.service.build(now=T0)
        self.accounts = AccountTrackers(self.service)

    def receipt(self, **overrides):
        return dict({"player": "SampleOwner", "other": "TradePartner", "at": T0 + 120_000,
                     "got": ["§dEclipse Dye"], "gave": ["§6Coins: 100,000,000"]}, **overrides)

    def test_linked_receipts_bind_to_uuid_and_ignore_supplied_player(self):
        account = self.accounts.for_account("a" * 32, "Alice")
        friend = self.accounts.for_account("b" * 32, "Bob")
        self.service.add_trade(self.receipt())
        self.assertTrue(account.record_trade(self.receipt())["new"])
        self.assertFalse(account.record_trade(self.receipt())["new"])
        self.assertEqual([row["player"] for row in account.tracker.trades], ["Alice"])
        self.assertEqual(friend.tracker.trades, [])
        self.assertEqual([row["player"] for row in self.service.own_trades()], ["SampleOwner"])

        restored = AccountTrackers(self.service)
        renamed = restored.for_account("a" * 32, "AliceNew")
        recycled = restored.for_account("c" * 32, "Alice")
        self.assertEqual([row["player"] for row in renamed.tracker.trades], ["AliceNew"])
        self.assertEqual(recycled.tracker.trades, [])

    def test_late_receipt_matches_own_auction_only(self):
        alice = self.accounts.for_account("a" * 32, "Alice")
        add_sale(self.db, "seller-auction", 250_000_000, "aLiCe", "Buyer", T0 + 300_000)
        add_sale(self.db, "other-auction", 250_000_000, "Other", "Buyer", T0 + 310_000)
        self.assertEqual(alice.tracker.flips, [])
        self.assertTrue(alice.record_trade(self.receipt())["new"])
        self.assertEqual([(f["player"], f["sell"]["ref"]) for f in alice.tracker.flips], [("Alice", "seller-auction")])
        self.assertEqual(self.service.tracker.flips, [])

    def test_nebulous_mosquito_shortbow_trade_matches_next_trade(self):
        alice = self.accounts.for_account("a" * 32, "Alice")
        bow = "§6Nebulous Mosquito Shortbow"
        bought = alice.record_trade(self.receipt(got=[bow], gave=["§6Coins: 520,000,000"]))
        sold = alice.record_trade(self.receipt(other="NextBuyer", at=T0 + 300_000,
                                               got=["§6Coins: 650,000,000"], gave=[bow]))
        self.assertEqual((bought["kind"], sold["kind"], len(sold["flips"])), ("bought", "sold", 1))
        flip, = alice.tracker.flips
        self.assertEqual((flip["id"], flip["entry"]), (sold["flips"][0], "mosquito_shortbow"))
        self.assertEqual((flip["buy"]["via"], flip["buy"]["price"], flip["buy"]["ref"]), ("trade", 520_000_000, bought["ref"]))
        self.assertEqual((flip["sell"]["via"], flip["sell"]["price"], flip["profit"]), ("trade", 650_000_000, 130_000_000))
        self.assertEqual(alice.tracker.positions("Alice"), [])

    def test_invalid_trade_detail_does_not_poison_saved_ledger(self):
        alice = self.accounts.for_account("a" * 32, "Alice")
        with self.assertRaises(ValueError):
            alice.record_trade(self.receipt(detail={"items": [1]}))
        with self.assertRaises(ValueError):
            alice.record_trade(self.receipt(at=10**50))
        with self.assertRaises(ValueError):
            alice.record_trade(self.receipt(gave=["Coins: " + "9" * 90]))
        with self.assertRaises(ValueError):
            validate_linked_item({"item_modifiers": {"star_level": 10**50}}, self.service.catalog)
        self.assertEqual(alice.tracker.trades, [])
        self.assertTrue(alice.record_trade(self.receipt())["new"])
