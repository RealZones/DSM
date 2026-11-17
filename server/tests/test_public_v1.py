"""The public API at /api/v1: fixed shapes, real errors, and nothing personal."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from market import web  # noqa: E402
from test_live import Service_  # noqa: E402


class PublicV1(Service_, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.v1 = web.PublicV1(web.PublicApi(cls.service))

    def test_item_has_only_the_documented_fields(self):
        code, body = self.v1.handle("item/dye_eclipse", {})
        self.assertEqual(code, 200)
        self.assertEqual(set(body), {"entry", "name", "tier", "npc", "bazaar", "estimate"})
        self.assertEqual(set(body["estimate"]), {"value", "median", "lbin", "lbin_live", "last_sale", "volume", "time_to_sell",
                                                 "insta_sell", "confidence"})
        self.assertGreater(body["estimate"]["value"], 0)

    def test_unknown_item_is_a_404_not_an_empty_price(self):
        code, body = self.v1.handle("item/no_such_item", {})
        self.assertEqual(code, 404)
        self.assertIn("no_such_item", body["error"])
        self.assertEqual(self.v1.handle("item/../../etc", {})[0], 404)

    def test_sold_lists_sales_newest_first_with_a_limit(self):
        code, body = self.v1.handle("item/dye_eclipse/sold", {"limit": ["3"]})
        self.assertEqual(code, 200)
        self.assertEqual(len(body["auctions"]), 3)
        times = [a["at"] for a in body["auctions"]]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(set(body["auctions"][0]), {"id", "unit", "count", "seller", "buyer", "at", "parts"})

    def test_bad_parameters_are_400s(self):
        self.assertEqual(self.v1.handle("item/dye_eclipse/history", {"range": ["year"]})[0], 400)
        self.assertEqual(self.v1.handle("item/dye_eclipse/sold", {"limit": ["many"]})[0], 400)
        self.assertEqual(self.v1.handle("search", {})[0], 400)
        self.assertEqual(self.v1.handle("prices", {})[0], 400)
        self.assertEqual(self.v1.handle("prices", {"ids": [",".join(f"x{i}" for i in range(51))]})[0], 400)

    def test_prices_answer_for_known_ids_only(self):
        code, body = self.v1.handle("prices", {"ids": ["dye_eclipse,no_such_item"]})
        self.assertEqual(code, 200)
        self.assertEqual(list(body), ["dye_eclipse"])
        self.assertEqual(set(body["dye_eclipse"]), {"now", "recent", "monthly", "volume", "bazaar"})

    def test_personal_pages_are_not_reachable(self):
        for path in ("flips", "trades", "watches", "notification-settings", "player/seller1", "scan/seller1", "home"):
            self.assertEqual(self.v1.handle(path, {})[0], 404, path)

    def test_status_says_whether_prices_are_current(self):
        code, body = self.v1.handle("status", {})
        self.assertEqual(code, 200)
        self.assertEqual(set(body), {"market_time", "listings", "ok"})


if __name__ == "__main__":
    unittest.main()
