"""Applied scrolls/runes remain separate identities and are valued once at retained shares."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from market import engine as E
from market.bazaar import Bazaar
from market.items import Catalog, WITHER_SCROLLS, applied_scrolls, from_json

NOW = 1_795_000_000_000
SCROLLS = tuple(WITHER_SCROLLS.values())


def raw(abilities=(), mask=None, rune=None, entry="hyperion"):
    item = {"entry": entry, "count": 1, "item_data": {"item_name": entry, "rarity": "LEGENDARY"},
            "item_modifiers": {}, "extra": {}, "abilities": abilities}
    if mask is not None:
        item["extra"]["wither_scrolls"] = {"mask": mask}
    if rune is not None:
        item["item_modifiers"]["rune_id"] = rune
    return item


class ScrollsAndRunes(unittest.TestCase):
    def setUp(self):
        entries = {"hyperion": {"name": "Hyperion", "tier": "LEGENDARY"},
                   "meow_music_rune": {"name": "Meow Music Rune", "category": "COSMETIC", "tier": "MYTHIC"}}
        entries.update({entry: {"name": entry.replace("_", " ").title(), "tier": "EPIC"} for entry in SCROLLS})
        self.catalog = Catalog({"items": entries, "pets": {}, "reforges": {}, "itemRules": {}})
        self.engine = E.Engine(self.catalog, E.Tuned(), {}, Bazaar(self.catalog.npc))
        self.engine.now = NOW
        self.engine.shop = dict(zip(SCROLLS, (8_000_000_000, 9_000_000_000, 10_000_000_000)))
        self.engine.shop["meow_music_rune"] = 6_500_000_000

    def sell(self, prefix, item, price, count=8):
        for n in range(count):
            self.engine.add_sale(prefix + str(n), item, price, prefix + "seller" + str(n), prefix + "buyer" + str(n),
                                 NOW + n, NOW - 1000)

    def test_public_and_nbt_forms_use_the_same_three_canonical_ids(self):
        public = from_json(raw(["wither_impact"]))
        nbt = from_json(raw({"abilities": ["implosion", "shadow_warp", "wither_shield"]}))
        self.assertEqual(public.scrolls, SCROLLS)
        self.assertEqual(nbt.scrolls, SCROLLS)
        self.assertEqual(public.pricing_identity(), nbt.pricing_identity())
        self.assertEqual(from_json(raw(mask=7)).scrolls, SCROLLS)
        self.assertEqual(from_json(raw(mask="7")).scrolls, SCROLLS)

    def test_partial_scrolls_are_deduplicated_and_unrelated_weapons_do_not_gain_them(self):
        self.assertEqual(applied_scrolls(raw(["shadow_warp", "shadow_warp", "unrelated"])), ("shadow_warp_scroll",))
        self.assertEqual(from_json(raw(["wither_impact"], mask=7, entry="giants_sword")).scrolls, ())
        self.assertEqual(from_json(raw({"invalid": ["wither_impact"]})).scrolls, ())

    def test_bare_strips_scrolls_and_rune_while_empty_identity_stays_compatible(self):
        clean = from_json(raw())
        old_identity = (clean.entry, clean.rarity, clean.reforge, clean.stars, clean.recomb, clean.hpb, clean.ghpb,
                        clean.sync, clean.aow, clean.enrich, clean.skin, clean.dye, clean.pet_item, clean.pet_skin,
                        clean.engine, clean.tank, clean.module, clean.coated)
        self.assertEqual(clean.identity(), old_identity)
        upgraded = from_json(raw(["wither_impact"], rune="meow_music_rune"))
        # Pricing can distinguish the applied upgrades while existing trade matching
        # remains untouched under the owner's explicit UUID-only change restriction.
        self.assertEqual(upgraded.identity(), clean.identity())
        self.assertNotEqual(upgraded.pricing_identity(), clean.pricing_identity())
        self.assertEqual(upgraded.bare().identity(), clean.identity())
        self.assertEqual(upgraded.bare().pricing_identity(), clean.pricing_identity())
        self.assertEqual(upgraded.bare().scrolls, ())
        self.assertIsNone(upgraded.bare().rune)
        self.assertEqual(upgraded.upgrades()["rune"], "meow_music_rune")

    def test_three_scroll_costs_share_one_kind_without_losing_any_scroll(self):
        item = from_json(raw(["wither_impact"]))
        elems = [e for e in self.engine.select_valuable(item) if e.kind == "scrolls"]
        self.assertEqual(len(elems), 1)
        self.assertEqual(elems[0].coins, 27_000_000_000)
        self.assertEqual(self.engine._kind_value(*elems[0].key), 27_000_000_000)
        self.assertEqual(set(elems[0].value.split("+")), set(SCROLLS))

    def test_structural_keys_survive_the_value_cap_and_missing_part_prices(self):
        self.engine.p.keep_max = 0
        item = from_json(raw(["wither_impact"], rune="meow_music_rune"))
        key = self.engine.detailed_key(item).key
        self.assertEqual({kind for kind, value in key.mods}, {"scrolls", "rune"})
        self.engine.shop.clear()
        self.engine._part_cache.clear()
        self.assertEqual(self.engine.detailed_key(item).key, key)

    def test_historical_scrolled_and_clean_sales_are_separate_and_not_added_twice(self):
        clean = from_json(raw())
        scrolled = from_json(raw(["wither_impact"]))
        self.sell("plain", clean, 100_000_000_000)
        self.sell("scrolled", scrolled, 125_000_000_000)
        lookup = self.engine.lookups["hyperion"]
        clean_key = self.engine.detailed_key(clean).key
        scroll_key = self.engine.detailed_key(scrolled).key
        self.assertNotEqual(clean_key, scroll_key)
        self.assertEqual(len(lookup.buckets[clean_key].refs), 8)
        self.assertEqual(len(lookup.buckets[scroll_key].refs), 8)
        self.assertAlmostEqual(self.engine.estimate(clean)["value"], 100_000_000_000, delta=1000)
        self.assertAlmostEqual(self.engine.estimate(scrolled)["value"], 125_000_000_000, delta=1000)

    def test_retained_build_adds_all_scrolls_at_sixty_percent_and_rune_at_thirty(self):
        self.sell("plain", from_json(raw()), 100_000_000_000)
        item = from_json(raw(["wither_impact"], rune="meow_music_rune"))
        total, bare, shares = self.engine.built_value(item)
        self.assertEqual(bare, 100_000_000_000)
        self.assertAlmostEqual(total, bare + 27_000_000_000 * .60 + 6_500_000_000 * .30)
        self.assertEqual({(p["label"], p["share"]) for p in shares}, {("Wither scrolls", .60), ("Meow Music Rune", .30)})
        self.assertEqual(self.engine._kind_value("rune", "meow_music_rune"), 6_500_000_000)

    def test_more_scrolls_are_better_but_different_single_scrolls_are_not_interchangeable(self):
        one = self.engine.detailed_key(from_json(raw(["implosion"]))).key
        another = self.engine.detailed_key(from_json(raw(["shadow_warp"]))).key
        full = self.engine.detailed_key(from_json(raw(["wither_impact"]))).key
        self.assertTrue(E.is_higher_value(one, full))
        self.assertFalse(E.is_higher_value(full, one))
        self.assertFalse(E.is_higher_value(one, another))

    def test_multiple_reforge_stones_choose_cheapest_positive_price(self):
        self.catalog.reforge_stone["fierce"] = "expensive_stone"
        self.catalog.reforge_stones = {"fierce": ["missing_stone", "expensive_stone", "cheap_stone"]}
        self.engine.shop.update(expensive_stone=100_000_000, cheap_stone=40_000_000)
        item = from_json(raw())
        item.reforge = "fierce"
        elem = next(e for e in self.engine.select_valuable(item) if e.kind == "reforge")
        self.assertEqual(elem.coins, 40_000_000 + E.reforge_coins(item.rarity))
        self.assertEqual(self.engine._kind_value("reforge", "fierce"), 40_000_000)


if __name__ == "__main__":
    unittest.main()
