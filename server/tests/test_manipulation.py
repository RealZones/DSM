"""Large price jumps need independent confirmation across comparable stack sizes."""
import unittest

from market import engine as E
from market.items import Catalog, from_json

DAY = E.DAY
NOW = 1_794_500_000_000


class PriceSpikes(unittest.TestCase):
    def setUp(self):
        catalog = Catalog({"items": {"rift_goo": {"name": "Rift Goo", "tier": "RARE", "npc_sell_price": 50_000}},
                           "pets": {}, "reforges": {}, "itemRules": {}})
        self.engine = E.Engine(catalog, E.Tuned())
        self.index = 0

    def item(self, count=16, rarity="RARE"):
        return from_json({"entry": "rift_goo", "count": count,
                          "item_data": {"item_name": "Rift Goo", "rarity": rarity, "npc_sell_price": 50_000}})

    def sale(self, unit, count, seller, buyer, at):
        self.index += 1
        self.engine.add_sale(str(self.index), self.item(count), unit, seller, buyer, at)

    def history(self):
        # Captured Rift Goo prices and parties are represented by synthetic names.
        self.sale(1_562_500, 64, "supplier", "buyer1", NOW - 20 * DAY)
        self.sale(1_562_500, 64, "supplier", "buyer2", NOW - 19 * DAY)
        for i in range(5):
            self.sale(1_000_000, 64, "supplier", "buyer3", NOW - 18 * DAY + i)
        self.sale(1_000_000, 38, "supplier", "buyer4", NOW - 17 * DAY)
        self.sale(1_000_000, 64, "supplier", "buyer4", NOW - 17 * DAY + 1)
        self.sale(20_833, 48, "friend1", "friend2", NOW - 14 * DAY)
        self.sale(937_500, 64, "supplier2", "buyer5", NOW - 9 * DAY)
        self.sale(1_562_500, 16, "supplier3", "buyer6", NOW - 9 * DAY + 1)
        self.sale(1 / 64, 64, "spike_seller", "friend3", NOW - 2 * DAY)
        self.sale(400_000_000, 10, "spike_seller", "spike_buyer", NOW)

    def test_one_recent_sale_cannot_raise_other_stack_sizes_by_hundreds_of_times(self):
        self.history()
        quote = self.engine.estimate(self.item())
        self.assertGreater(quote["value"], 500_000)
        self.assertLess(quote["value"], 2_000_000)
        self.assertLess(quote["last_sale"]["price"], 2_000_000)

    def test_repeat_sales_by_one_seller_do_not_confirm_the_jump(self):
        self.history()
        for i in range(4):
            self.sale(400_000_000, 10, "spike_seller", f"extra_buyer{i}", NOW + i + 1)
        self.assertLess(self.engine.estimate(self.item())["value"], 2_000_000)

    def test_independent_sales_can_establish_a_changed_market(self):
        self.history()
        self.sale(400_000_000, 10, "new_seller1", "new_buyer1", NOW + 1)
        self.sale(410_000_000, 10, "new_seller2", "new_buyer2", NOW + 2)
        self.assertGreater(self.engine.estimate(self.item())["value"], 200_000_000)

    def test_comparison_does_not_mix_different_rarities(self):
        self.history()
        for i in range(2):
            self.index += 1
            self.engine.add_sale(str(self.index), self.item(rarity="MYTHIC"), 500_000_000,
                                 f"rare_seller{i}", f"rare_buyer{i}", NOW + i + 1)
        self.assertGreater(self.engine.estimate(self.item(rarity="MYTHIC"))["median"], 100_000_000)
