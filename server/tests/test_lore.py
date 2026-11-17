"""The hover lines: formats, the line for each kind of item, and the full craft cost under them."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_crafts import make, sell                      # noqa: E402
from test_market import T0, raw                         # noqa: E402
from market import engine as E                          # noqa: E402
from market.items import from_json                      # noqa: E402
from market.lore import describe, number, short, upto   # noqa: E402

NONE = (0, 0, False)


def est(**fields):
    out = {"lbin": 0, "lbin_key": None, "item_key": "K", "median": 0, "median_key": None, "volume": 0.0}
    out.update(fields)
    return out


class Formats(unittest.TestCase):
    def test_format_number_is_whole_coins_with_commas(self):
        self.assertEqual([number(n) for n in (1_234_567.4, 999.94, 12, 2_500_000_000)],
                         ["1,234,567", "999.9", "12.0", "2,500,000,000"])

    def test_format_price_short_keeps_two_decimals_at_most(self):
        self.assertEqual([short(n) for n in (1_234_567_890, 2_500_000_000, 950_000, 1_000_000, 999, 12_340, 0, -4_200_000)],
                         ["1.23B", "2.5B", "950K", "1M", "999", "12.34K", "0", "-4.2M"])

    def test_volume_keeps_one_decimal_at_most(self):
        self.assertEqual([upto(v, 1) for v in (3.24, 12.0, 0.04)], ["3.2", "12", "0"])


class Lines(unittest.TestCase):
    def test_an_exact_auction_item(self):
        lines = describe(est(lbin=1_234_567, lbin_key="K", median=1_200_000, median_key="K", volume=3.24), 1, NONE, None)
        self.assertEqual(lines, ["§7lbin: §e1,234,567 ", "§7Med: §b1,200,000 §7Vol: §e3.2 ", "§7No Craft Cost found"])

    def test_a_near_match_says_so(self):
        lines = describe(est(lbin=1_000_000, lbin_key="X+HV", median=900_000, median_key="N", value=1_100_000), 3, NONE, None)
        self.assertEqual(lines[0], "§7lbin: ~3,000,000 §8(higher value found)(1,000,000 each)")
        self.assertEqual(lines[1], "§7Med: §b~3,300,000 (1,100,000 each) §7Vol: §e0 ", "the estimate, not the near bucket")
        lines = describe(est(lbin=5, lbin_key="X"), 1, NONE, None)
        self.assertEqual(lines[0], "§7lbin: ~5.0 §8(estimate, no match found)")

    def test_a_bazaar_stack(self):
        row = est(bz_buy=1_234_000, bz_sell=1_200_000, median=1_220_000, median_key="K")
        lines = describe(row, 64, NONE, (12_340, 6_780))
        self.assertEqual(lines[0], "§7Buy: §678.98M (1.23M each) §7Sell: §676.8M (1.2M each)")
        self.assertEqual(lines[1], "§7Med: §b78,080,000 (1,220,000 each) §7Vol: §e12.34K§7/§e6.78K")
        row["bz_price"] = row["median"]
        self.assertEqual(describe(row, 64, NONE, (12_340, 6_780))[1], "§7Vol: §e12.34K§7/§e6.78K", "the bazaar's own price is no median")

    def test_craft_cost_never_beats_buying_the_exact_item(self):
        exact = est(median=5_000_000, median_key="K")
        self.assertEqual(describe(exact, 1, (3_000_000, 8_500_000, True), None)[-1], "§7Full Craft Cost: §e5M ")
        near = est(median=5_000_000, median_key="N")
        self.assertEqual(describe(near, 1, (3_000_000, 8_500_000, True), None)[-1], "§7Full Craft Cost: §e8.5M ")

    def test_what_cannot_be_crafted_shows_its_obtain_cost(self):
        self.assertEqual(describe(est(), 1, (2_000_000, 2_500_000, False), None), ["§7Obtain cost: §f2.5M§7(not craftable)"])

    def test_a_recipe_nobody_can_price(self):
        self.assertEqual(describe(est(), 1, (E.UNAVAILABLE, E.UNAVAILABLE, True), None), ["§7Craft ingredients unavailable"])


class FullCraft(unittest.TestCase):
    RECIPES = {"sword": [{"need": {"ingot": 3}, "yield": 1, "coins": 0, "text": ""}],
               "blade": [{"need": {"goop": 1}, "yield": 1, "coins": 0, "text": ""}]}

    def setUp(self):
        self.engine, _ = make(self.RECIPES)
        self.engine.shop = {"recombobulator_3000": 5_000_000, "hot_potato_book": 100_000}
        for i in range(6):
            sell(self.engine, f"i{i}", "ingot", 1_000_000, T0 + i)
            sell(self.engine, f"s{i}", "sword", 10_000_000, T0 + 10 + i)
            sell(self.engine, f"h{i}", "handle", 2_000_000, T0 + 20 + i)

    def test_the_cheaper_base_plus_every_part_in_full(self):
        sword = from_json(raw("sword", "Sword", "MYTHIC", mods={"recombobulated": True, "hot_potato_books": 5}))
        obtain, summary, craftable = self.engine.full_craft(sword)
        self.assertTrue(craftable)
        self.assertEqual(obtain, 3_000_000, "three ingots beat a 10m clean sword")
        self.assertEqual(summary, 3_000_000 + 5_000_000 + 5 * 100_000)

    def test_no_recipe_is_its_clean_price(self):
        obtain, summary, craftable = self.engine.full_craft(from_json(raw("handle", "Handle", "RARE")))
        self.assertFalse(craftable)
        self.assertEqual((obtain, summary), (2_000_000, 2_000_000))

    def test_a_dear_craft_with_no_sales_is_still_a_price(self):
        # 133b of parts and no sale of the item: the old 20b stand-in won the min() and the hover
        # read "Craft ingredients unavailable".
        self.engine.craft_cost = lambda entry: 133_000_000_000
        try:
            obtain, summary, craftable = self.engine.full_craft(from_json(raw("blade", "Blade", "EPIC")))
        finally:
            del self.engine.craft_cost
        self.assertTrue(craftable)
        self.assertEqual(obtain, 133_000_000_000)
        self.assertEqual(describe(est(), 1, (obtain, summary, craftable), None)[-1].startswith("§7Full Craft Cost: §e133B"), True)

    def test_unpriced_ingredients_are_out_of_reach(self):
        obtain, _, craftable = self.engine.full_craft(from_json(raw("blade", "Blade", "EPIC")))
        self.assertTrue(craftable)
        self.assertEqual(obtain, E.UNAVAILABLE)
