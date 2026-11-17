"""Private NW quotes retain applied values without leaking a player's pricing policy."""
import copy
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from market import networth
from market.bazaar import Bazaar
from market.engine import Engine, Tuned
from market.items import Catalog, from_json

NOW = 1_795_000_000_000
M = 1_000_000
B = 1_000_000_000


def raw(entry="test_blade", skin=None, dye=None, rune=None, recomb=False, pet_item=None, count=1):
    item = {"entry": entry, "count": count,
            "item_data": {"item_name": "[Lvl 100] Bal" if pet_item else entry,
                          "rarity": "MYTHIC" if recomb else "LEGENDARY"},
            "item_modifiers": {}, "extra": {}}
    if skin:
        item["extra"]["item_skin_applied"] = {"skinId": skin}
    if dye:
        item["extra"]["item_dye"] = {"dyeId": dye}
    if rune:
        item["item_modifiers"]["rune_id"] = rune
    if recomb:
        item["item_modifiers"]["recombobulated"] = True
    if pet_item:
        item["extra"]["pet_data"] = {"equippedItem": pet_item}
    return item


class NetworthBridge(unittest.TestCase):
    def setUp(self):
        entries = {entry: {"name": entry, "tier": "LEGENDARY"}
                   for entry in ("test_blade", "bal_pet_legendary", "new_relic", "material", "held_item",
                                 "recombobulator_3000")}
        entries.update({entry: {"name": entry, "tier": "MYTHIC", "category": "COSMETIC"}
                        for entry in ("manual_skin", "manual_dye", "manual_rune")})
        self.catalog = Catalog({"items": entries, "pets": {}, "reforges": {}, "itemRules": {}})
        self.engine = Engine(self.catalog, Tuned(), {}, Bazaar(self.catalog.npc))
        self.engine.now = NOW
        self.service = SimpleNamespace(catalog=self.catalog, engine=self.engine, lock=threading.RLock(),
                                       last_poll=NOW, built_at=NOW)
        clock = patch.object(networth.time, "time", return_value=NOW / 1000)
        clock.start()
        self.addCleanup(clock.stop)

    def quote(self, item=None, owner="", npc_parts=None, **policy):
        return networth.batch(self.service, {"items": [{"item": item or raw(), **policy}],
                                             "owner": owner, "npc_parts": npc_parts or {}})["estimates"][0]

    def sales(self, item, price):
        for n in range(8):
            self.engine.add_sale("sale" + str(n), item, price, "seller" + str(n), "buyer" + str(n),
                                 NOW - n * 60_000, NOW - (n + 1) * 60_000)

    def test_missing_manual_skin_retains_thirty_percent_on_fallback_base(self):
        quote = self.quote(raw(skin="manual_skin"), fallback_base=100 * M,
                           fallback_parts={"manual_skin": 6.5 * B})
        self.assertEqual(quote["value"], 2050 * M)
        self.assertEqual(quote["non_cosmetic_value"], 100 * M)
        self.assertEqual(quote["cosmetic_value"], 1.95 * B)
        self.assertEqual(quote["source"], "existing base fallback")
        self.assertEqual(quote["extras"], [{"entry": "manual_skin", "value": 1.95 * B,
                                           "included": False, "cosmetic": True}])

    def test_core_keeps_functional_upgrades_and_allocates_only_retained_cosmetics(self):
        quote = self.quote(raw(skin="manual_skin", dye="manual_dye", rune="manual_rune", recomb=True),
                           fallback_base=100 * M,
                           fallback_parts={"manual_skin": 6.5 * B, "manual_dye": B,
                                           "manual_rune": .5 * B, "recombobulator_3000": 100 * M})
        self.assertEqual(quote["non_cosmetic_value"], 160 * M)
        self.assertEqual(quote["value"], 2.56 * B)
        self.assertEqual(quote["cosmetic_value"], 2.4 * B)
        extras = {row["entry"]: row for row in quote["extras"]}
        self.assertEqual(extras["manual_skin"]["value"], 1.95 * B)
        self.assertEqual(extras["manual_dye"]["value"], .3 * B)
        self.assertEqual(extras["manual_rune"]["value"], .15 * B)
        self.assertEqual(extras["recombobulator_3000"], {"entry": "recombobulator_3000", "value": 0,
                                                       "included": True, "cosmetic": False})
        self.assertEqual(sum(row["value"] for row in extras.values()), quote["cosmetic_value"])

    def test_pet_held_item_is_included_once_at_forty_three_percent(self):
        quote = self.quote(raw(entry="bal_pet_legendary", pet_item="held_item"), fallback_base=100 * M,
                           fallback_parts={"held_item": 50 * M})
        self.assertEqual(quote["value"], 121.5 * M)
        self.assertEqual(quote["non_cosmetic_value"], 121.5 * M)
        self.assertEqual(quote["cosmetic_value"], 0)
        self.assertEqual(quote["extras"], [{"entry": "held_item", "value": 0,
                                           "included": True, "cosmetic": False}])

    def test_market_price_wins_over_manual_fallback(self):
        self.sales(from_json(raw(), self.catalog), 200 * M)
        self.assertEqual(self.quote(fallback_base=100 * M)["value"], 200 * M)
        self.assertEqual(self.quote(fallback_base=100 * M)["source"], "market")

    def test_personal_fallbacks_and_part_caches_do_not_cross_requests(self):
        before_parts = copy.deepcopy(self.engine._part_cache)
        before_crafts = copy.deepcopy(self.engine._craft_cache)
        first = self.quote(raw(skin="manual_skin"), fallback_base=100 * M,
                           fallback_parts={"manual_skin": 6.5 * B})
        second = self.quote(raw(skin="manual_skin"), fallback_base=50 * M,
                            fallback_parts={"manual_skin": B})
        self.assertEqual(first["value"], 2050 * M)
        self.assertEqual(second["value"], 350 * M)
        self.assertEqual(self.engine._part_cache, before_parts)
        self.assertEqual(self.engine._craft_cache, before_crafts)
        self.assertFalse(hasattr(self.engine, "fallback_parts"))
        self.assertEqual(self.quote()["source"], "unpriced")

    def test_owner_listing_is_excluded_only_for_that_owner_without_mutation(self):
        item = from_json(raw(), self.catalog)
        self.engine.add_listing("mine", item, 100 * M, "Owner", NOW, NOW + 86_400_000)
        self.engine.add_listing("theirs", item, 300 * M, "Other", NOW, NOW + 86_400_000)
        lookup = self.engine.lookups[item.entry]
        bucket = next(iter(lookup.buckets.values()))
        original_listings = bucket.lbins[:]
        original_cache = copy.deepcopy(self.engine._part_cache)
        self.assertEqual(self.quote(owner="OwNeR")["value"], 300 * M)
        self.assertEqual(self.quote(owner="someone_else")["value"], 100 * M)
        self.assertEqual(self.quote()["value"], 100 * M)
        self.assertIs(self.engine.lookups[item.entry], lookup)
        self.assertIs(next(iter(lookup.buckets.values())), bucket)
        self.assertEqual(bucket.lbins, original_listings)
        self.assertEqual(set(self.engine.listings), {"mine", "theirs"})
        self.assertEqual(self.engine._part_cache, original_cache)

    def test_unseen_recipe_prices_ingredients_before_existing_base_fallback(self):
        self.engine.recipes["new_relic"] = [{"need": {"material": 2}, "yield": 1, "coins": 5 * M}]
        self.assertNotIn("new_relic", self.engine.lookups)
        quote = self.quote(raw(entry="new_relic"), fallback_base=100 * M,
                           fallback_parts={"material": 10 * M})
        self.assertEqual(quote["value"], 25 * M)
        self.assertEqual(quote["non_cosmetic_value"], 25 * M)
        self.assertEqual(quote["source"], "craft fallback")

    def test_explicit_npc_policy_controls_recipe_ingredient_instead_of_market_fallback(self):
        self.engine.recipes["new_relic"] = [{"need": {"material": 2}, "yield": 1, "coins": 5 * M}]
        self.engine.shop["material"] = 20 * M
        quote = self.quote(raw(entry="new_relic"), fallback_parts={"material": 10 * M},
                           npc_parts={"material": 3 * M})
        self.assertEqual(quote["value"], 11 * M)

    def test_forced_npc_base_overrides_auction_but_preserves_retained_upgrades(self):
        self.sales(from_json(raw(), self.catalog), B)
        quote = self.quote(raw(skin="manual_skin", recomb=True), force_base=20 * M,
                           fallback_base=100 * M,
                           fallback_parts={"manual_skin": 6.5 * B, "recombobulator_3000": 100 * M})
        self.assertEqual(quote["source"], "explicit NPC policy")
        self.assertEqual(quote["value"], 2030 * M)
        self.assertEqual(quote["non_cosmetic_value"], 80 * M)
        self.assertEqual(quote["cosmetic_value"], 1.95 * B)
        self.assertEqual(self.quote(force_base=None, fallback_base=100 * M)["source"], "unpriced")

    def test_loose_cosmetic_is_entirely_cosmetic_and_count_is_not_multiplied(self):
        quote = self.quote(raw(entry="manual_skin", count=4), fallback_base=6.5 * B)
        self.assertEqual(quote["value"], 6.5 * B)
        self.assertEqual(quote["non_cosmetic_value"], 0)
        self.assertEqual(quote["cosmetic_value"], 6.5 * B)

    def test_fresh_batch_reports_its_snapshot_and_policy_version(self):
        result = networth.batch(self.service, {"items": [{"item": raw(), "fallback_base": 100 * M}]})
        self.assertEqual(result["source_ms"], NOW)
        self.assertEqual(result["version"], networth.VERSION)
        self.assertEqual(len(result["estimates"]), 1)

    def test_stale_or_missing_market_snapshot_is_rejected(self):
        for stamp in (NOW - 180_001, 0):
            with self.subTest(stamp=stamp):
                self.service.last_poll = stamp
                self.service.built_at = stamp
                with self.assertRaisesRegex(ValueError, "stale"):
                    self.quote(fallback_base=100 * M)
        self.service.last_poll = NOW
        self.service.engine = None
        with self.assertRaisesRegex(ValueError, "stale"):
            self.quote()

    def test_invalid_owner_is_a_bad_request(self):
        for owner in (123, True, "bad owner", "x" * 17):
            with self.subTest(owner=owner), self.assertRaisesRegex(ValueError, "owner"):
                self.quote(owner=owner, fallback_base=100 * M)

    def test_invalid_row_policy_is_a_bad_request(self):
        policies = ({"fallback_base": -1}, {"fallback_base": float("nan")},
                    {"fallback_base": True}, {"fallback_parts": {"material": 0}},
                    {"fallback_parts": {"material": float("inf")}},
                    {"fallback_parts": ["material"]}, {"aliases": "test_blade"},
                    {"force_base": -1})
        for policy in policies:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                self.quote(**policy)

    def test_forced_hand_price_reports_its_label_and_unknown_labels_are_refused(self):
        self.sales(from_json(raw(), self.catalog), B)
        quote = self.quote(force_base=300 * M, force_label="your estimate")
        self.assertEqual((quote["value"], quote["source"]), (300 * M, "your estimate"))
        with self.assertRaises(ValueError):
            self.quote(force_base=300 * M, force_label="anything else")

    def test_impossible_item_is_one_unpriced_row_not_a_failed_batch(self):
        bad = raw()
        bad["item_modifiers"].update(final_synchronizers=500, star_level=20)
        result = networth.batch(self.service, {"items": [{"item": bad, "fallback_base": 100 * M},
                                                         {"item": raw(), "fallback_base": 100 * M}]})
        first, second = result["estimates"]
        self.assertEqual((first["value"], first["source"]), (None, "invalid item"))
        self.assertEqual(second["value"], 100 * M)

    def test_bundle_share_receipts_are_ignored_but_lone_ones_count(self):
        self.engine.reported = {"manual_skin": {"value": 12.9 * B, "how": "share"},
                                "manual_dye": {"value": 3 * B, "how": "alone"}}
        skin = self.quote(raw(entry="manual_skin"), fallback_base=4.5 * B)
        self.assertEqual((skin["value"], skin["source"]), (4.5 * B, "existing base fallback"))
        self.assertEqual(self.quote(raw(entry="manual_dye"), fallback_base=1 * B)["value"], 3 * B)

    def test_invalid_batch_shape_is_a_bad_request(self):
        bodies = ({"items": []}, {"items": [{"item": raw()}] * 65},
                  {"items": [None]}, {"items": [{"item": "test_blade"}]},
                  {"items": [{"item": raw()}], "npc_parts": {"material": -1}},
                  {"items": [{"item": raw()}], "npc_parts": ["material"]})
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(ValueError):
                networth.batch(self.service, body)


if __name__ == "__main__":
    unittest.main()
