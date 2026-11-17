"""Craft flips, prices off trade receipts, and the chat lines that prove something was made."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_market import DAY, T0, raw                    # noqa: E402
from market import crafts as C                          # noqa: E402
from market import engine as E                          # noqa: E402
from market import fees                                 # noqa: E402
from market.bazaar import Bazaar                        # noqa: E402
from market.discord import short, split                 # noqa: E402
from market.items import Catalog, from_json, from_receipt  # noqa: E402
from market.reported import Reports                     # noqa: E402
from market.tracker import Tracker                      # noqa: E402

ITEMS = {
    "sword": {"name": "Sword", "tier": "LEGENDARY"},
    "blade": {"name": "Blade", "tier": "EPIC"},
    "handle": {"name": "Handle", "tier": "RARE"},
    "plate": {"name": "Plate", "tier": "RARE"},
    "ingot": {"name": "Ingot", "tier": "COMMON"},
    "goop": {"name": "Goop", "tier": "RARE"},
    "canister": {"name": "Canister", "tier": "RARE"},
}
CATALOG = {"items": ITEMS, "reforges": {}, "pets": {}, "itemRules": {"bazaarProducts": {}}}
RECIPES = {
    "sword": [{"need": {"blade": 1, "handle": 1}, "yield": 1, "coins": 0, "kind": "craft", "text": "craft: Blade, Handle"}],
    "blade": [{"need": {"handle": 1}, "yield": 1, "coins": 0, "kind": "craft", "text": "craft: Handle"}],
    "plate": [{"need": {"ingot": 2}, "yield": 1, "coins": 0, "kind": "forge", "hours": 8, "text": "forge: 2x Ingot"}],
}


def make(recipes=RECIPES, shop=None, own=()):
    catalog = Catalog(CATALOG)
    engine = E.Engine(catalog, E.Tuned(), recipes, Bazaar(catalog.npc))
    engine.now = T0
    return engine, C.Crafts(engine, recipes, shop or {}, own)


def list_(engine, lid, entry, price, seller="s"):
    engine.add_listing(lid, from_json(raw(entry, entry)), price, seller, engine.now, engine.now + 14 * DAY, 1)


def sell(engine, sid, entry, price, at):
    engine.now = max(engine.now, at)
    engine.add_sale(sid, from_json(raw(entry, entry, ITEMS[entry]["tier"])), price, "seller" + sid, "buyer" + sid, at, at - 60_000)


class BotPrices(unittest.TestCase):
    """Fear's rule for what nothing traded prices: "it should have a price if the bot has 1"."""

    def test_a_part_that_never_trades_takes_the_bots_price_not_its_npc_price(self):
        # 2026-11-15: Necron's Handle had never been listed, was 120b by hand on the bot and 100m to an
        # NPC. The NPC price stood in, so Hyperion's Necron's Blade came to 404m and Hyperion to 30b.
        items = dict(ITEMS, handle=dict(ITEMS["handle"], npc_sell_price=100_000_000))
        catalog = Catalog(dict(CATALOG, items=items))
        engine = E.Engine(catalog, E.Tuned(), RECIPES, Bazaar(catalog.npc))
        engine.now = T0
        crafts = C.Crafts(engine, RECIPES)
        self.assertEqual(engine.price_of("handle"), (100_000_000, True), "with no bot price the NPC floor is all there is")
        engine.bot = {"handle": 114_000_000_000}
        engine._part_cache.clear()
        engine._craft_cache.clear()
        self.assertEqual(engine.price_of("handle"), (114_000_000_000, True))
        self.assertEqual(engine.craft_cost("blade"), 114_000_000_000, "a recipe prices its part at the bot's price")
        plan = crafts.flip("sword")
        self.assertTrue(plan["estimated"])
        self.assertEqual(plan["cost"], 228_000_000_000, "two handles nobody sells, each at the bot's price")


class Supply(unittest.TestCase):
    def test_a_cheap_listing_only_counts_once(self):
        engine, crafts = make({"plate": [{"need": {"ingot": 2}, "yield": 1, "coins": 0, "kind": "craft", "text": ""}]})
        list_(engine, "a", "ingot", 1_000_000)
        list_(engine, "b", "ingot", 9_000_000)
        self.assertEqual(crafts.cheapest("plate").cost, 10_000_000)

    def test_two_parts_cannot_claim_the_same_listing(self):
        engine, crafts = make()
        list_(engine, "a", "handle", 1_000_000)
        list_(engine, "b", "handle", 9_000_000)
        plan = crafts.cheapest("sword")
        self.assertEqual(plan.cost, 10_000_000, "the blade needs a handle too, so the cheap one cannot be used twice")

    def test_it_cannot_be_made_when_supply_runs_out(self):
        engine, crafts = make()
        list_(engine, "a", "handle", 1_000_000)
        self.assertIsNone(crafts.cheapest("sword"))
        self.assertEqual(crafts.short("sword"), ["handle"])

    def test_a_loop_of_recipes_ends(self):
        loop = {"sword": [{"need": {"blade": 1}, "yield": 1, "coins": 0, "text": ""}], "blade": [{"need": {"sword": 1}, "yield": 1, "coins": 0, "text": ""}]}
        _, crafts = make(loop)
        self.assertIsNone(crafts.cheapest("sword"))

    def test_your_own_listing_is_not_supply(self):
        engine, crafts = make(own=("me",))
        list_(engine, "a", "handle", 1_000_000, "me")
        self.assertEqual(crafts.supply("handle"), [])

    def test_a_shop_never_runs_out(self):
        _, crafts = make({"plate": [{"need": {"canister": 3}, "yield": 1, "coins": 0, "text": ""}]}, shop={"canister": 3_000_000})
        self.assertEqual(crafts.cheapest("plate").cost, 9_000_000)

    def test_a_part_is_bought_when_making_it_barely_saves(self):
        engine, crafts = make({"sword": [{"need": {"plate": 1}, "yield": 1, "coins": 0, "text": ""}], "plate": RECIPES["plate"]})
        list_(engine, "a", "plate", 22_000_000)
        list_(engine, "b", "ingot", 10_000_000)
        list_(engine, "c", "ingot", 10_000_000)
        self.assertEqual(crafts.cheapest("sword").parts[0].method, "buy", "20m and a wait is not worth saving 2m on 22m")
        list_(engine, "d", "plate2", 1)     # noise that must not affect anything
        engine.remove_listing("a")
        list_(engine, "e", "plate", 30_000_000)
        self.assertEqual(crafts.cheapest("sword").parts[0].method, "craft")

    def test_cheap_copies_are_bought_and_the_rest_made(self):
        # Part bought, part made: a plate takes 20m of ingots to make and one is listed at 5m, so of three,
        # one is bought and two are made, where choosing all of one way made all three for 60m.
        engine, crafts = make({"sword": [{"need": {"plate": 3}, "yield": 1, "coins": 0, "text": ""}], "plate": RECIPES["plate"]})
        list_(engine, "p", "plate", 5_000_000)
        for i in range(6):
            list_(engine, f"i{i}", "ingot", 10_000_000)
        plates = crafts.cheapest("sword").parts[0]
        self.assertEqual((plates.method, plates.made, plates.cost), ("craft", 2, 45_000_000))
        self.assertEqual([fill[:3] for fill in plates.fills], [("ah", 1, 5_000_000)])
        self.assertEqual(sum(part.count for part in plates.parts), 4, "ingots for the two that are made")

    def test_copies_dearer_than_making_one_are_not_mixed_in(self):
        engine, crafts = make({"sword": [{"need": {"plate": 3}, "yield": 1, "coins": 0, "text": ""}], "plate": RECIPES["plate"]})
        list_(engine, "p", "plate", 25_000_000)
        for i in range(6):
            list_(engine, f"i{i}", "ingot", 10_000_000)
        plates = crafts.cheapest("sword").parts[0]
        self.assertEqual((plates.method, plates.made, plates.cost, plates.fills), ("craft", 3, 60_000_000, ()))

    def test_only_the_missing_part_uses_its_last_price(self):
        # Goop is for sale nowhere, so it takes its last price. The ingots are listed at 10m, and an
        # old sale at 1k must not price them: that is how OD-655 came out at 2.85b with a 6.9b OD-555 in it.
        engine, crafts = make({"sword": [{"need": {"plate": 1, "goop": 1}, "yield": 1, "coins": 0, "kind": "craft", "text": ""}],
                               "plate": RECIPES["plate"]})
        sell(engine, "g", "goop", 50_000_000, T0)
        sell(engine, "i", "ingot", 1_000, T0)
        list_(engine, "a", "ingot", 10_000_000)
        list_(engine, "b", "ingot", 10_000_000)
        row = crafts.flip("sword")
        self.assertTrue(row["estimated"])
        self.assertEqual(row["cost"], 70_000_000, "50m of goop at its last price, and two real ingots for the plate")

    def test_a_bid_nobody_sells_into_adds_nothing(self):
        engine, crafts = make()
        engine.bazaar.update(T0, "goop", 1, 700_000_000, 1, 1, 0, {"buys": [{"price": 1, "amount": 5}], "sells": [{"price": 700_000_000, "amount": 5}]})
        self.assertEqual([row[2] for row in crafts.supply("goop")], ["bazaar"])

    def test_a_bid_people_sell_into_adds_a_little(self):
        engine, crafts = make()
        engine.bazaar.update(T0, "goop", 100, 120, 1, 1, 0, {"buys": [{"price": 100, "amount": 5}], "sells": [{"price": 120, "amount": 5}]})
        engine.bazaar.add_fill(T0 + 1, "goop", 100, 1000, "sell")
        rows = [row for row in crafts.supply("goop") if row[2] == "order"]
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0][0], 120, "a buy order waits, so it is marked up for the time")


    def test_a_low_bid_does_not_buy_what_sellers_sold_at_a_real_price(self):
        engine, crafts = make()
        engine.bazaar.update(T0, "goop", 5, 20_000, 1, 1, 0, {"buys": [{"price": 5, "amount": 5}], "sells": [{"price": 20_000, "amount": 5}]})
        engine.bazaar.add_fill(T0 + 1, "goop", 19_000, 1000, "sell")
        rows = [row for row in crafts.supply("goop") if row[2] == "order"]
        self.assertGreater(rows[0][0], 19_000, "bought at 7 coins, the craft on top of this would show 700x profit")


class Clean(unittest.TestCase):
    def setUp(self):
        self.engine, self.crafts = make({"sword": [{"need": {"ingot": 1}, "yield": 1, "coins": 0, "text": ""}]})
        self.engine.shop = {"recombobulator_3000": 400_000_000, "hot_potato_book": 20_000_000}

    def sold(self, sid, price, **mods):
        item = from_json(raw("sword", "Sword", "MYTHIC" if mods.get("recombobulated") else "LEGENDARY", mods=mods))
        self.engine.add_sale(sid, item, price, "seller" + sid, "buyer" + sid, self.engine.now, self.engine.now - 60_000)
        return item

    def clean(self):
        item = from_json(raw("sword", "Sword", "LEGENDARY"))
        return self.engine.estimate(item)

    def test_a_clean_copy_is_not_priced_at_what_upgraded_ones_sold_for(self):
        self.sold("a", 4_250_000_000, recombobulated=True, hot_potato_books=10)
        est = self.clean()
        # 400m recomb and 200m of books at the 60% buyers pay back
        self.assertEqual(est["value"], 4_250_000_000 - 360_000_000)
        self.assertTrue(est["stripped"])
        self.assertEqual(est["last_sale"]["price"], 4_250_000_000, "the sale itself still shows as it happened")

    def test_the_parts_are_not_counted_twice_on_an_upgraded_copy(self):
        item = self.sold("a", 4_250_000_000, recombobulated=True, hot_potato_books=10)
        self.assertAlmostEqual(self.engine.estimate(item)["value"], 4_250_000_000, delta=1000, msg="it was 4.61b with the parts on twice")

    def test_a_clean_copy_is_worth_no_more_than_a_better_one_just_went_for(self):
        self.sold("a", 5_000_000_000, recombobulated=True, hot_potato_books=10)
        self.sold("b", 2_400_000_000, hot_potato_books=5)
        self.sold("c", 4_000_000_000, hot_potato_books=10, recombobulated=True, star_level=5)
        # 5 books x 20m at 60%, off the cheapest sale
        self.assertEqual(self.clean()["value"], 2_400_000_000 - 60_000_000)

    def test_a_favour_to_a_friend_does_not_set_the_price(self):
        self.sold("a", 5_000_000_000, recombobulated=True, hot_potato_books=10)
        self.sold("b", 200_000_000, hot_potato_books=5)
        self.sold("c", 4_000_000_000, hot_potato_books=10, recombobulated=True, star_level=5)
        self.assertGreater(self.clean()["value"], 3_000_000_000)

    def capped(self, median, item):
        out = {"median": median}
        self.engine._cap_at_better(out, self.engine.lookups["sword"], self.engine.detailed_key(item))
        return out["median"]

    def test_a_worse_copy_does_not_cap_a_better_one(self):
        self.sold("a", 2_000_000_000)
        rich = self.sold("b", 9_000_000_000, recombobulated=True, hot_potato_books=10)
        self.assertEqual(self.capped(9_000_000_000, rich), 9_000_000_000, "a clean copy says nothing against an upgraded one")

    def test_an_old_sale_does_not_cap_todays_price(self):
        self.sold("a", 1_000_000_000, hot_potato_books=5)
        self.engine.now = T0 + 20 * DAY
        rich = self.sold("b", 4_000_000_000, recombobulated=True)
        clean = from_json(raw("sword", "Sword", "LEGENDARY"))
        self.assertEqual(self.capped(3_760_000_000, clean), 3_760_000_000)
        self.assertEqual(rich.recomb, True)

    def test_a_clean_sale_is_left_alone(self):
        self.sold("a", 3_000_000_000)
        est = self.clean()
        self.assertEqual(est["value"], 3_000_000_000)
        self.assertFalse(est["stripped"])

    def test_the_craft_sells_for_the_clean_price_with_no_markup(self):
        self.sold("a", 4_250_000_000, recombobulated=True, hot_potato_books=10)
        list_(self.engine, "b", "ingot", 1_000_000)
        self.assertEqual(self.crafts.sale("sword")["price"], 3_890_000_000)

    def test_parts_dearer_than_the_sale_do_not_make_it_worthless(self):
        self.engine.shop["recombobulator_3000"] = 90_000_000_000
        self.sold("a", 4_000_000_000, recombobulated=True)
        self.assertEqual(self.clean()["value"], 1_000_000_000)


class Ghosts(unittest.TestCase):
    def test_a_listing_that_arrives_after_its_own_sale_stays_gone(self):
        engine, _ = make()
        item = from_json(raw("handle", "handle", "RARE"))
        engine.add_sale("x", item, 1, "friend", "pal", T0)
        self.assertIsNone(engine.add_listing("x", item, 1, "friend", T0 + 1000, T0 + DAY))
        self.assertEqual(engine.estimate(item)["lbin"], 0, "a sold 1 coin listing must not be the price to beat")

    def test_an_expired_listing_cannot_be_the_lbin(self):
        engine, _ = make()
        item = from_json(raw("handle", "handle", "RARE"))
        engine.add_listing("x", item, 5_000_000, "s", T0, T0 + DAY)
        self.assertEqual(engine.estimate(item)["lbin"], 5_000_000)
        engine.now = T0 + 2 * DAY
        self.assertEqual(engine.estimate(item)["lbin"], 0)


class Selling(unittest.TestCase):
    def test_an_offer_nobody_takes_is_not_a_price(self):
        engine, crafts = make()
        engine.bazaar.update(T0, "goop", 1, 700_000_000, 1, 1, 0, {"buys": [{"price": 1, "amount": 5}], "sells": [{"price": 700_000_000, "amount": 5}]})
        self.assertIsNone(crafts.sale("goop"))

    def test_profit_is_after_the_fee(self):
        engine, crafts = make({"handle": [{"need": {"ingot": 1}, "yield": 1, "coins": 0, "text": ""}]})
        list_(engine, "a", "ingot", 1_000_000)
        for i in range(8):
            sell(engine, f"s{i}", "handle", 200_000_000, T0 + i * 3_600_000)
        row = crafts.flip("handle")
        self.assertEqual(row["net"], fees.net_of_fee(row["sale"]["price"]))
        self.assertEqual(row["profit"], row["net"] - row["cost"])
        self.assertTrue(C.tested(row))

    def test_the_sale_is_not_capped_by_what_it_costs_to_make(self):
        recipes = {"handle": [{"need": {"ingot": 1}, "yield": 1, "coins": 0, "text": ""}]}
        engine, crafts = make(recipes)
        for i in range(6):
            sell(engine, f"i{i}", "ingot", 2_000_000, T0 + i)
        for i in range(8):
            sell(engine, f"s{i}", "handle", 69_000_000, T0 + 10 + i * 3_600_000)
        list_(engine, "a", "handle", 69_000_000)
        list_(engine, "b", "ingot", 2_000_000)
        row = crafts.flip("handle")
        self.assertLess(engine.estimate(from_json(raw("handle", "handle", "RARE")))["median"], 10_000_000, "the median carries the craft cap")
        self.assertEqual(row["sale"]["price"], 68_999_999, "what beats the BIN, not 110% of the capped median")

    def test_the_bazaar_sells_what_the_ah_never_saw(self):
        engine, crafts = make({"goop": [{"need": {"ingot": 1}, "yield": 1, "coins": 0, "text": ""}]})
        list_(engine, "a", "ingot", 1_000_000)
        engine.bazaar.update(T0, "goop", 2_500_000, 20_000_000, 1, 1, 0, {"buys": [{"price": 2_500_000, "amount": 5}], "sells": [{"price": 20_000_000, "amount": 5}]})
        for i in range(12):
            engine.bazaar.add_fill(T0 + i * 3_600_000, "goop", 14_000_000, 10)
        row = crafts.flip("goop")
        self.assertEqual(row["sale"]["via"], "bazaar")
        self.assertTrue(C.tested(row))

    def test_a_friends_one_coin_listing_is_not_the_price_to_beat(self):
        engine, crafts = make({"handle": [{"need": {"ingot": 1}, "yield": 1, "coins": 0, "text": ""}]})
        list_(engine, "a", "ingot", 1_000_000)
        for i in range(8):
            sell(engine, f"s{i}", "handle", 50_000_000, T0 + i * 3_600_000)
        engine.add_listing("gift", from_json(raw("handle", "handle", "RARE")), 1, "pal", engine.now, engine.now + DAY, 1)
        engine.add_listing("real", from_json(raw("handle", "handle", "RARE")), 60_000_000, "shop", engine.now, engine.now + DAY, 1)
        self.assertGreater(crafts.flip("handle")["sale"]["price"], 40_000_000)

    def test_a_part_nobody_is_selling_uses_its_last_price(self):
        engine, crafts = make({"handle": [{"need": {"ingot": 2}, "yield": 1, "coins": 0, "text": ""}]})
        for i in range(6):
            sell(engine, f"i{i}", "ingot", 3_000_000, T0 + i)
        for i in range(8):
            sell(engine, f"s{i}", "handle", 50_000_000, T0 + 100 + i * 3_600_000)
        row = crafts.flip("handle")
        self.assertTrue(row["estimated"])
        self.assertEqual(row["cost"], 6_000_000)
        self.assertFalse(C.tested(row), "it cannot be bought today, so it is not a solid flip")
        list_(engine, "a", "ingot", 1_000_000)
        list_(engine, "b", "ingot", 1_000_000)
        row = crafts.flip("handle")
        self.assertFalse(row["estimated"])
        self.assertEqual(row["cost"], 2_000_000)

    def test_a_part_that_sells_fast_costs_what_it_sells_for(self):
        engine, crafts = make({"handle": [{"need": {"ingot": 100}, "yield": 1, "coins": 0, "text": ""}]})
        for i in range(8):
            sell(engine, f"one{i}", "ingot", 70_000_000, T0 + i)
        for i in range(60):
            engine.now = T0 + 100 + i * 1000
            engine.add_sale(f"s{i}", from_json(raw("ingot", "ingot", "COMMON", count=32)), 70_000_000, f"a{i}", f"b{i}", engine.now, engine.now - 60_000)
        for i in range(20):
            list_(engine, f"l{i}", "ingot", 74_000_000 + i * 800_000)
        plan = crafts.cheapest("handle")
        self.assertEqual(plan.cost, 7_000_000_000, "70m each, not the 74-90m left standing in the listings")
        self.assertEqual(plan.parts[0].fills[0][0], "wait")

    def test_a_part_that_rarely_sells_is_bought_off_the_listings(self):
        engine, crafts = make({"handle": [{"need": {"ingot": 2}, "yield": 1, "coins": 0, "text": ""}]})
        for i in range(6):
            sell(engine, f"i{i}", "ingot", 1_000_000, T0 + i)
        list_(engine, "a", "ingot", 2_000_000)
        list_(engine, "b", "ingot", 3_000_000)
        self.assertEqual(crafts.cheapest("handle").cost, 5_000_000)

    def test_a_part_nobody_sells_is_made_when_it_can_be(self):
        recipes = {"sword": [{"need": {"blade": 1}, "yield": 1, "coins": 0, "text": ""}], "blade": [{"need": {"ingot": 2}, "yield": 1, "coins": 0, "text": ""}]}
        engine, crafts = make(recipes)
        sell(engine, "b", "blade", 1_000_000, T0)       # it sold once, so it has a last price
        sell(engine, "i", "ingot", 3_000_000, T0 + 1)
        list_(engine, "x", "ingot", 3_000_000)
        list_(engine, "y", "ingot", 3_000_000)
        row = crafts.flip("sword")
        part = row["plan"].parts[0]
        self.assertEqual(part.method, "craft", "there is none to buy, so buying it is no plan at all")
        self.assertEqual(part.cost, 6_000_000, "even though its last price was lower")
        self.assertEqual(part.buy_cost, 0, "and it must not claim to be cheaper than buying")
        # With no ingots for sale either, making it is a guess built on guesses. That is what priced
        # OD-655 off 614,400 unlisted Obsidian, so the blade takes its own last price instead.
        engine.remove_listing("x")
        engine.remove_listing("y")
        row = crafts.flip("sword")
        self.assertTrue(row["estimated"])
        self.assertEqual((row["plan"].parts[0].method, row["plan"].parts[0].cost), ("buy", 1_000_000))

    def test_a_listed_part_still_gets_compared_with_making_it(self):
        recipes = {"sword": [{"need": {"blade": 1}, "yield": 1, "coins": 0, "text": ""}], "blade": [{"need": {"ingot": 2}, "yield": 1, "coins": 0, "text": ""}]}
        engine, crafts = make(recipes)
        list_(engine, "a", "blade", 5_000_000)
        list_(engine, "b", "ingot", 3_000_000)
        list_(engine, "c", "ingot", 3_000_000)
        self.assertEqual(crafts.cheapest("sword").parts[0].method, "buy")

    def test_a_part_with_no_price_at_all_still_blocks_it(self):
        _, crafts = make({"handle": [{"need": {"ingot": 2}, "yield": 1, "coins": 0, "text": ""}]})
        self.assertIsNone(crafts.flip("handle"))

    def test_a_dead_item_does_not_look_busy(self):
        engine, crafts = make({"handle": [{"need": {"ingot": 1}, "yield": 1, "coins": 0, "text": ""}]})
        list_(engine, "a", "ingot", 1_000_000)
        sell(engine, "s1", "handle", 200_000_000, T0)
        sell(engine, "s2", "handle", 200_000_000, T0 + DAY)
        engine.now = T0 + 40 * DAY
        list_(engine, "b", "ingot", 1_000_000)
        row = crafts.flip("handle")
        self.assertEqual(row["sale"]["volume"], 0, "two sales a day apart a month ago are not 1 a day today")
        self.assertFalse(C.tested(row))

    def test_more_copies_each_go_a_little_lower(self):
        sale = dict(via="ah", price=1_000_000_000)
        self.assertLess(C.proceeds(sale, 4), fees.net_of_fee(1_000_000_000) * 4)


class Receipts(unittest.TestCase):
    NAMES = {"pet_skin_a": "Purple Sheep Skin", "pet_skin_b": "White Sheep Skin", "dye_aurora": "Aurora Dye", "meow_music_rune": "◆ Meow Music Rune III",
             "phoenix": "Ice Phoenix Skin", "pew": "Pew Pew Bow Sound", "old_baby": "Baby Skin", "new_baby": "Baby Skin"}

    def setUp(self):
        self.catalog = Catalog(CATALOG, {k: {"name": v, "tier": "EPIC"} for k, v in self.NAMES.items()})

    def solve(self, rows, market=None):
        reports = Reports(rows, self.catalog)
        return reports, reports.solve(market)

    def test_a_rune_resolves_with_or_without_its_diamond(self):
        for line in ("◆ Meow Music Rune III", "✦ Meow Music Rune III", "Meow Music Rune III"):
            self.assertEqual(from_receipt(line, self.catalog)[2].entry, "meow_music_rune")

    def test_one_item_for_coins_is_its_price(self):
        _, out = self.solve([{"at": "2026-10-26T00:31", "got": ["Purple Sheep Skin"], "gave": ["Coins: 13,500,000,000"]}])
        self.assertAlmostEqual(out["pet_skin_a"]["october"], 13_500_000_000, delta=1)
        self.assertEqual(out["pet_skin_a"]["how"], "alone")

    def test_a_price_found_alone_is_taken_off_the_next_bundle(self):
        _, out = self.solve([{"at": "2026-10-26T00:31", "got": ["Purple Sheep Skin"], "gave": ["Coins: 10,000,000,000"]},
                             {"at": "2026-10-27T00:31", "gave": ["Purple Sheep Skin", "White Sheep Skin"], "got": ["Coins: 18,000,000,000"]}])
        self.assertAlmostEqual(out["pet_skin_b"]["october"], 8_000_000_000, delta=1)

    def test_a_trade_after_the_drop_prices_an_item_never_seen_before(self):
        reports, out = self.solve([{"at": "2026-10-26T00:31", "got": ["Purple Sheep Skin"], "gave": ["Coins: 10,000,000,000"]},
                                   {"at": "2026-11-04T00:31", "gave": ["Purple Sheep Skin"], "got": ["Coins: 7,000,000,000"]},
                                   {"at": "2026-11-16T05:00", "got": ["Aurora Dye"], "gave": ["Coins: 6,000,000,000"]}])
        self.assertAlmostEqual(reports.ratio, 0.7)
        self.assertAlmostEqual(out["dye_aurora"]["value"], 6_000_000_000, delta=10, msg="today's trade is today's price")
        self.assertEqual(out["dye_aurora"]["how"], "alone")

    def test_a_bundle_alone_prices_nothing(self):
        # Nobody can tell how the traders valued each skin, so a bundle waits for single sales.
        _, out = self.solve([{"at": "2026-10-26T00:31", "got": ["2x Purple Sheep Skin", "White Sheep Skin", "Meow Music Rune III"], "gave": ["Coins: 40,000,000,000"]}])
        self.assertFalse({"pet_skin_a", "pet_skin_b", "meow_music_rune"} & set(out))

    def test_a_bundle_counts_once_every_item_has_sold_alone(self):
        rows = [{"at": "2026-10-20T00:00", "got": ["Purple Sheep Skin"], "gave": ["Coins: 10,000,000,000"]},
                {"at": "2026-10-21T00:00", "got": ["White Sheep Skin"], "gave": ["Coins: 5,000,000,000"]},
                {"at": "2026-10-26T00:31", "got": ["Purple Sheep Skin", "White Sheep Skin"], "gave": ["Coins: 18,000,000,000"]}]
        _, out = self.solve(rows)
        # The bundle splits 2:1 like the single sales, 12b and 6b, and votes beside them.
        self.assertAlmostEqual(out["pet_skin_a"]["october"], (10e9 * 12e9) ** .5, delta=1000)
        self.assertAlmostEqual(out["pet_skin_b"]["october"], (5e9 * 6e9) ** .5, delta=1000)
        self.assertEqual(out["pet_skin_b"]["how"], "alone")

    def test_a_bundle_with_one_item_never_sold_alone_still_waits(self):
        rows = [{"at": "2026-10-20T00:00", "got": ["Purple Sheep Skin"], "gave": ["Coins: 10,000,000,000"]},
                {"at": "2026-10-26T00:31", "got": ["Purple Sheep Skin", "White Sheep Skin", "Meow Music Rune III"], "gave": ["Coins: 40,000,000,000"]}]
        _, out = self.solve(rows)
        self.assertAlmostEqual(out["pet_skin_a"]["october"], 10e9, delta=1, msg="the bundle must not move a price while part of it is unknown")
        self.assertNotIn("pet_skin_b", out)

    def test_a_swap_with_coins_on_top(self):
        rows = [{"at": "2026-10-28T20:50", "got": ["Pew Pew Bow Sound"], "gave": ["Ice Phoenix Skin", "Coins: 3,000,000,000"], "overpaid": 1_000_000_000}]
        _, out = self.solve(rows, lambda entry: 7_500_000_000 if entry == "pew" else 0)
        self.assertAlmostEqual(out["phoenix"]["october"], 5_500_000_000, delta=1)
        self.assertEqual(out["pew"]["how"], "ah")
        self.assertEqual(out["pew"]["value"], 7_500_000_000)

    def test_market_prices_do_not_run_away_with_the_ratio(self):
        market = lambda entry: 8_000_000_000 if entry == "pet_skin_a" else 0
        reports, out = self.solve([{"at": "2026-10-26T00:31", "gave": ["Purple Sheep Skin", "White Sheep Skin"], "got": ["Coins: 18,000,000,000"]},
                                   {"at": "2026-11-04T00:31", "gave": ["Purple Sheep Skin", "White Sheep Skin"], "got": ["Coins: 9,000,000,000"]}], market)
        self.assertAlmostEqual(reports.ratio, 0.5)
        self.assertAlmostEqual(out["pet_skin_b"]["october"], 10_000_000_000, delta=1, msg="scaling the 8b by the ratio left this at 2b")

    def test_a_stated_price_wins(self):
        rows = [{"at": "2026-10-28T20:50", "got": ["Purple Sheep Skin"], "gave": ["Coins: 3,000,000,000"], "stated": {"Purple Sheep Skin": 9_000_000_000}}]
        self.assertEqual(self.solve(rows)[1]["pet_skin_a"]["how"], "said")

    def test_every_copy_of_a_shared_name_gets_the_price(self):
        _, out = self.solve([{"at": "2026-10-28T20:50", "got": ["Baby Skin"], "gave": ["Coins: 3,000,000,000"]}])
        self.assertEqual(out["old_baby"]["october"], out["new_baby"]["october"])

    def test_trades_after_the_drop_set_what_it_is_worth_now(self):
        reports, out = self.solve([{"at": "2026-10-26T00:31", "got": ["Purple Sheep Skin"], "gave": ["Coins: 10,000,000,000"]},
                                   {"at": "2026-11-04T00:31", "gave": ["Purple Sheep Skin"], "got": ["Coins: 7,000,000,000"]}])
        self.assertAlmostEqual(reports.ratio, 0.7)
        self.assertEqual(reports.since, 1)
        self.assertAlmostEqual(out["pet_skin_a"]["value"], 7_000_000_000, delta=10)
        self.assertAlmostEqual(out["pet_skin_a"]["october"], 10_000_000_000, delta=1)

    def test_a_reported_price_stays_rough_and_gives_way_to_the_market(self):
        catalog = self.catalog
        engine = E.Engine(catalog, E.Tuned(), {}, Bazaar(catalog.npc))
        engine.now = T0
        engine.reported = {"dye_aurora": {"value": 2_000_000_000}}
        item = from_json(raw("dye_aurora", "Aurora Dye", "MYTHIC"))
        est = engine.estimate(item)
        self.assertEqual(est["value"], 2_000_000_000)
        self.assertEqual(est["confidence"], "low")
        self.assertEqual(est["median_key"], "reported")
        self.assertIsNone(E.offers(est) if est["confidence"] != "low" else None)
        self.assertEqual(engine.price_of("dye_aurora")[0], 2_000_000_000)
        for i in range(6):
            engine.now = T0 + i * 3_600_000
            engine.add_sale(f"s{i}", item, 3_000_000_000, f"a{i}", f"b{i}", engine.now, engine.now - 60_000)
        self.assertEqual(engine.estimate(item)["value"], 3_000_000_000)


class Made(unittest.TestCase):
    def setUp(self):
        catalog = Catalog(CATALOG)
        recipes = {"sword": [{"need": {"blade": 1, "handle": 4}, "yield": 1, "coins": 0, "text": ""}]}
        self.engine = E.Engine(catalog, E.Tuned(), recipes, Bazaar(catalog.npc))
        self.engine.now = T0
        self.tracker = Tracker(self.engine, ("SampleOwner",))
        self.price("handle", 100_000_000)
        self.price("blade", 100_000_000)

    def price(self, entry, coins):
        for i in range(6):
            self.engine.add_sale(f"{entry}{i}", from_json(raw(entry, entry)), coins, "x", "y", T0 + i, T0)

    def buy(self, entry):
        self.engine.now = T0 + DAY
        self.tracker.on_sale("buy" + entry, from_json(raw(entry, entry)), 100_000_000, 1, "other", "Maker", T0 + DAY, T0, None, None)

    def sale(self):
        self.engine.now = T0 + 2 * DAY
        est = self.engine.estimate(from_json(raw("sword", "Sword")))
        return self.tracker.on_sale("sold", from_json(raw("sword", "Sword")), 2_000_000_000, 1, "Maker", "z", T0 + 2 * DAY, T0 + 2 * DAY - 1000, None, est)

    def test_a_small_part_is_not_a_craft_without_proof(self):
        self.buy("blade")
        self.assertIsNone(self.sale())

    def test_the_game_saying_so_is_proof(self):
        self.buy("blade")
        self.tracker.on_chat("Maker", T0 + DAY + 1000, "§aSupercrafted §fSword§a.")
        flip = self.sale()
        self.assertIn("craft", flip["flags"])
        self.assertIn("uncertain_cost", flip["flags"], "the handles were priced at market, and it should say so")

    def test_farmed_materials_stay_out(self):
        self.tracker.on_chat("Maker", T0 + DAY + 1000, "§aCollected §9Sword §afrom the Forge.")
        self.assertIsNone(self.sale(), "nothing was bought, so a profit here would be invented")

    def test_one_message_can_hold_two_crafts(self):
        self.tracker.on_chat("Maker", T0, "§aSupercrafted §fBlade§a. §aSupercrafted §fSword§a.")
        made = self.tracker.stocks["Maker"].made
        self.assertEqual(sorted(made), ["blade", "sword"])

    def test_one_line_proves_one_sale(self):
        self.buy("blade")
        self.tracker.on_chat("Maker", T0 + DAY + 1000, "§aSupercrafted §fSword§a.")
        self.sale()
        self.assertEqual([r[1] for r in self.tracker.stocks["Maker"].made["sword"]], [True])


class Discord(unittest.TestCase):
    def test_prices_read_the_way_they_get_typed(self):
        self.assertEqual([short(n) for n in (2_500_000_000, 190_000_000, 1_500_000, 950_000, 12)], ["2.5b", "190m", "1.5m", "950k", "12"])

    def test_long_lists_split_under_the_limit(self):
        parts = split(["x" * 300] * 20)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 2000 for part in parts))
        self.assertEqual(sum(part.count("x" * 300) for part in parts), 20)

    def test_nothing_to_say_sends_nothing(self):
        self.assertEqual(split([]), [])


class Pets(unittest.TestCase):
    def test_the_level_is_not_part_of_a_pets_name_and_rarities_are_one_litter(self):
        catalog = Catalog(CATALOG, {"bal_pet_epic": {"name": "[Lvl 1] Bal", "tier": "EPIC"}, "bal_pet_legendary": {"name": "[Lvl 1] Bal", "tier": "LEGENDARY"}})
        self.assertEqual(catalog.name("bal_pet_epic"), "Bal")
        self.assertEqual(sorted(catalog.litter("bal_pet_legendary")), ["bal_pet_epic", "bal_pet_legendary"])
        self.assertEqual(catalog.litter("sword"), [])


class TradeSplits(unittest.TestCase):
    """How a trade's money is shared between the kinds in it, for flip costs."""
    def shares(self, rows, values, total):
        from market.tracker import Tracker
        return Tracker._shares(None, rows, values, total)

    def test_one_kind_needs_no_split(self):
        units, unknown = self.shares([("skin", 3)], [0], 30e9)
        self.assertEqual(units, [10e9])
        self.assertFalse(unknown)

    def test_priced_kinds_split_by_value(self):
        units, unknown = self.shares([("a", 1), ("b", 1)], [2e9, 1e9], 6e9)
        self.assertEqual(units, [4e9, 2e9])
        self.assertFalse(unknown)

    def test_an_unpriced_kind_makes_the_cost_unknown(self):
        units, unknown = self.shares([("a", 1), ("b", 1)], [2e9, 0], 6e9)
        self.assertTrue(unknown, "an even split would invent what each skin cost")


if __name__ == "__main__":
    unittest.main()
