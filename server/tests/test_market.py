"""Checks built from what really happened, so a change that breaks one breaks a known case.

    python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from market import engine as E, fees                    # noqa: E402
from market.bazaar import Bazaar                        # noqa: E402
from market.items import Catalog, Item, RANK, from_json, from_receipt  # noqa: E402
from market.tape import Tape                            # noqa: E402
from market.tracker import Tracker                      # noqa: E402

DAY = 86_400_000
T0 = 1_793_000_000_000
CATALOG = {
    "items": {
        "warden_helmet": {"id": "WARDEN_HELMET", "name": "Warden Helmet", "tier": "LEGENDARY"},
        "giants_sword": {"id": "GIANTS_SWORD", "name": "Giant's Sword", "tier": "LEGENDARY"},
        "dragon_horn": {"id": "DRAGON_HORN", "name": "Dragon Horn", "tier": "EPIC",
                        "description": ["§7Applies the §9Renowned §7reforge when"]},
        "dye_eclipse": {"id": "dye_eclipse", "name": "Eclipse Dye", "tier": "MYTHIC", "cosmetic": {"type": "armor_dye"}},
        "bottled_wisp": {"id": "BOTTLED_WISP", "name": "Bottled Wisp", "tier": "EPIC"},
        "recombobulator_3000": {"id": "RECOMBOBULATOR_3000", "name": "Recombobulator 3000", "tier": "LEGENDARY"},
        "ritual_residue": {"id": "RITUAL_RESIDUE", "name": "Ritual Residue", "tier": "EPIC"},
        "necron_chestplate": {"id": "NECRON_CHESTPLATE", "name": "Necron Chestplate", "tier": "LEGENDARY"},
        "frozen_frag": {"id": "FROZEN_FRAG", "name": "Frozen Fragment", "tier": "EPIC"},
        "ancient_rose": {"id": "ANCIENT_ROSE", "name": "Ancient Rose", "tier": "RARE"},
    },
    "reforges": {"ancient": [], "renowned": []},
    "pets": {},
    "itemRules": {"bazaarProducts": {}},
}


def raw(entry, name=None, rarity="LEGENDARY", reforge="", mods=None, extra=None, count=1):
    return {"entry": entry, "count": count, "item_data": {"item_name": name or entry, "rarity": rarity, "reforge": reforge},
            "item_modifiers": mods or {}, "extra": extra or {}}


def service():
    catalog = Catalog(CATALOG)
    engine = E.Engine(catalog, E.Tuned(), {}, Bazaar(catalog.npc))
    return catalog, engine, Tracker(engine, ("SampleOwner",))


class Fees(unittest.TestCase):
    def test_server_message(self):
        # "You don't have enough coins for the listing fee of 26,266,800 coins!" on a 14 day list
        self.assertEqual(fees.listing_fee(1_050_000_000, 336), 26_266_800)

    def test_seven_billion(self):
        # "Price set to 7.4B" then "listing fee of 185.1M"
        self.assertAlmostEqual(fees.listing_fee(7_404_000_000, 336) / 1e6, 185.1, 0)

    def test_bracket_round_trip(self):
        for net in (5_000_000, 9_999_999, 95_000_000, 2_000_000_000):
            self.assertGreaterEqual(fees.net_of_fee(fees.price_to_net(net)), net)


class Items(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog(CATALOG)

    def test_receipt_money(self):
        self.assertEqual(from_receipt("§6Coins: 630,000,000", self.catalog)[:2], ("coins", 630_000_000))
        self.assertEqual(from_receipt("§aGems: 1,000", self.catalog)[:2], ("gems", 1000))

    def test_receipt_reforge_and_stars(self):
        kind, n, item = from_receipt("§dRenowned Warden Helmet §6✪✪✪✪✪", self.catalog)
        self.assertEqual((item.entry, item.reforge, item.stars), ("warden_helmet", "renowned", 5))
        self.assertTrue(item.recomb, "mythic colour on a legendary item means a recomb")

    def test_receipt_pet_rarity_from_name_colour(self):
        _, _, item = from_receipt("§6§7[Lvl 100] §6Phoenix", self.catalog)
        self.assertEqual(item.entry, "phoenix_pet_legendary")

    def test_reforge_word_inside_a_name_stays(self):
        self.assertEqual(self.catalog.split_name("Ancient Rose"), (None, "ancient_rose"))

    def test_stone_read_from_description(self):
        self.assertEqual(self.catalog.reforge_stone["renowned"], "dragon_horn")

    def test_skin_read_from_either_field(self):
        a = from_json(raw("warden_helmet", extra={"item_skin": {"skinId": "true_warden"}}))
        b = from_json(raw("warden_helmet", extra={"item_skin_applied": {"skinId": "true_warden"}}))
        self.assertEqual(a.identity(), b.identity())

    def test_nbt_stamp_is_kept(self):
        item = from_json(raw("warden_helmet", extra={"item_dye": {"dyeId": "marine_dye", "appliedAt": 5}}))
        self.assertEqual(item.applied_at, (5,))


class TradeTape(unittest.TestCase):
    """Each case is one of the things the book can do between two looks."""

    def look(self, tape, ts, traded, buys=(), sells=()):
        product = {"id": "goop", "traded": traded, "buys": [{"price": p, "amount": a, "orders": o} for p, a, o in buys],
                   "sells": [{"price": p, "amount": a, "orders": o} for p, a, o in sells]}
        return tape.feed(ts, [product])

    def test_serial_fill_is_a_sale_at_that_price(self):
        tape = Tape()
        self.look(tape, 1, 100, sells=[(50, 10, 1), (60, 5, 1)])
        found = self.look(tape, 2, 103, sells=[(50, 7, 1), (60, 5, 1)])
        self.assertEqual([(f.side, f.price, f.amount, f.proof) for f in found], [("buy", 50, 3, "exact")])

    def test_order_gone_and_count_rose_is_a_full_fill(self):
        tape = Tape()
        self.look(tape, 1, 100, sells=[(50, 10, 1), (60, 5, 1)])
        found = self.look(tape, 2, 110, sells=[(60, 5, 1)])
        self.assertEqual([(f.price, f.amount, f.proof) for f in found], [(50, 10, "exact")])

    def test_order_gone_and_count_flat_is_a_cancel(self):
        tape = Tape()
        self.look(tape, 1, 100, sells=[(50, 10, 1), (60, 5, 1)])
        self.assertEqual(self.look(tape, 2, 100, sells=[(60, 5, 1)]), [])

    def test_instant_sell_hits_the_highest_order(self):
        tape = Tape()
        self.look(tape, 1, 100, buys=[(40, 5, 1), (30, 5, 1)])
        found = self.look(tape, 2, 102, buys=[(40, 3, 1), (30, 5, 1)])
        self.assertEqual([(f.side, f.price, f.amount) for f in found], [("sell", 40, 2)])

    def test_a_cancel_beside_a_fill_is_not_counted(self):
        tape = Tape()
        self.look(tape, 1, 100, sells=[(50, 10, 1), (60, 5, 1), (70, 9, 1)])
        # The 70 level left too, but only 4 traded, and it could not be reached past 50 anyway.
        found = self.look(tape, 2, 104, sells=[(50, 6, 1), (60, 5, 1)])
        self.assertEqual([(f.price, f.amount) for f in found], [(50, 4)])

    def test_serial_level_wins_when_the_count_cannot_cover_everything(self):
        tape = Tape()
        self.look(tape, 1, 100, buys=[(40, 10, 1)], sells=[(50, 10, 1)])
        found = self.look(tape, 2, 103, buys=[], sells=[(50, 7, 1)])
        self.assertEqual([(f.side, f.price, f.amount, f.proof) for f in found], [("buy", 50, 3, "serial")])

    def test_bought_and_refilled_keeps_volume_without_a_price(self):
        tape = Tape()
        self.look(tape, 1, 100, sells=[(50, 10, 1)])
        found = self.look(tape, 2, 105, sells=[(50, 10, 1)])
        self.assertEqual([(f.proof, f.amount, f.price) for f in found], [("unseen", 5, 0)])

    def test_first_look_claims_nothing(self):
        self.assertEqual(self.look(Tape(), 1, 500, sells=[(50, 10, 1)]), [])

    def test_a_long_hole_in_the_feed_claims_nothing(self):
        tape = Tape()
        self.look(tape, 1_000, 100, sells=[(50, 10, 1)])
        self.assertEqual(self.look(tape, 1_000 + 3_600_000, 200, sells=[(50, 1, 1)]), [])

    def test_tape_prices_a_product_with_no_five_minute_guess(self):
        bazaar = Bazaar(lambda e: 0)
        bazaar.update(T0, "goop", 90, 120, 1, 1, 10, None)
        for i in range(12):
            bazaar.add_fill(T0 + i, "goop", 110, 20)
        self.assertEqual(bazaar.price("goop"), 110)

    def test_an_order_with_no_price_never_sets_one(self):
        bazaar = Bazaar(lambda e: 0)
        bazaar.add_fill(T0, "goop", 0, 50)
        self.assertEqual(bazaar.books.get("goop"), None)


class FromTheGame(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog(CATALOG)

    def test_nbt_has_no_entry_so_the_catalog_supplies_it(self):
        raw_item = raw("", "Warden Helmet")
        raw_item.pop("entry")
        self.assertEqual(from_json(raw_item, self.catalog).entry, "warden_helmet")

    def test_pet_id_is_built_from_name_and_rarity(self):
        raw_item = raw("", "§7[Lvl 100] §6Phoenix", extra={"pet_data": {}})
        raw_item.pop("entry")
        self.assertEqual(from_json(raw_item, self.catalog).entry, "phoenix_pet_legendary")

    def test_window_upgrades_a_receipt_to_the_real_item(self):
        _, engine, tracker = service()
        stamped = raw("warden_helmet", "Warden Helmet", reforge="Renowned", mods={"hot_potato_books": 10, "star_level": 5},
                      extra={"item_dye": {"dyeId": "marine_dye", "appliedAt": 77}})
        tracker.on_trade(dict(player="SampleOwner", other="x", at=T0, got=["§6Renowned Warden Helmet §6✪✪✪✪✪"], gave=["§6Coins: 2,000,000,000"],
                              detail={"items": [{"name": "§6Renowned Warden Helmet §6✪✪✪✪✪", "item": stamped}]}))
        lot = tracker.stocks["SampleOwner"].lots["warden_helmet"][0]
        self.assertEqual((lot.item.hpb, lot.item.dye, lot.item.partial, lot.item.applied_at), (10, "marine_dye", False, (77,)))


class Pricing(unittest.TestCase):
    def refs(self, rows):
        return [E.Ref(i, p, T0 + i * 3_600_000, s, b) for i, (p, s, b) in enumerate(rows)]

    def test_lower_median_leans_low(self):
        self.assertEqual(E.lower_median([10, 20, 30, 40]), 20)

    def test_back_and_forth_pair_is_dropped(self):
        rows = self.refs([(900, "a", "b"), (950, "b", "a"), (100, "c", "d"), (110, "e", "f"), (120, "g", "h")])
        self.assertNotIn(900, [r.price for r in E.amm(rows, 60)])

    def test_one_seller_walking_the_price_up_is_halved(self):
        rows = self.refs([(100, "x", str(i)) for i in range(4)] + [(50, str(i), "y" + str(i)) for i in range(8)])
        self.assertTrue(any(r.seller == "x" and r.price == 50 for r in E.amm(rows, 60)))

    def test_middle_needs_two_to_agree(self):
        self.assertEqual(E.middle(100, 1, 110), 100, "a lone 1 coin gift must not set the price")
        self.assertEqual(E.middle(100, 0, 90), 90, "with two signals take the cautious one")

    def test_stack_keeps_its_own_bucket(self):
        self.assertNotEqual(E.stack_band(1), E.stack_band(8))

    def test_pet_level_does_not_split_a_bucket(self):
        _, engine, _ = service()
        low = from_json(raw("wolf_pet_legendary", "§7[Lvl 1] §6Wolf", extra={"pet_data": {}}))
        high = from_json(raw("wolf_pet_legendary", "§7[Lvl 100] §6Wolf", extra={"pet_data": {}}))
        self.assertEqual(engine.detailed_key(low).key, engine.detailed_key(high).key)

    def test_troll_offer_is_not_a_price(self):
        bazaar = Bazaar(lambda e: 400)
        bazaar.update(T0, "obsidian", 2, 500_000_000, 1, 1, 10, {"buys": [{"price": 2, "amount": 1}], "sells": [{"price": 500_000_000, "amount": 1}]})
        self.assertEqual(bazaar.price("obsidian"), 0)
        self.assertEqual(bazaar.ask("obsidian"), 0)

    def test_deal_needs_a_gap_and_some_trust(self):
        est = {"value": 120, "lbin": 150, "confidence": "high"}
        self.assertIsNone(E.deal(dict(est, value=95_000_000, lbin=100_000_000), 95_000_000), "5% gap is under the 10% floor")
        self.assertIsNotNone(E.deal(dict(est, value=120_000_000, lbin=125_000_000), 100_000_000))
        self.assertIsNone(E.deal(dict(est, value=120_000_000, lbin=125_000_000, confidence="low"), 100_000_000))
        self.assertIsNone(E.deal(dict(est, value=900_000_000, lbin=900_000_000), 100_000_000), "over 4x is a troll or another item")

    def test_a_deal_is_under_what_it_sells_for(self):
        # 2026-11-15: 18 Warden Catalysts at 111.1m each, their median 111.5m, were flagged +528m because
        # the next stack was listed at 156m and one stack had sold at 137m.
        est = {"value": 137_187_500, "lbin": 156_250_000, "confidence": "high", "median": 111_481_481}
        self.assertIsNone(E.deal(est, 111_111_111, 18, 0.15), "at its median it is no deal, whatever the next BIN says")
        self.assertIsNotNone(E.deal(dict(est, median=125_000_000), 111_111_111, 18, 0.15), "11% under its median it is")

    def test_offer_undercuts_and_pays_the_fee(self):
        got = E.offers({"value": 1_000_000_000, "median": 1_000_000_000, "lbin": 900_000_000})
        self.assertEqual(got["resale"], 899_999_999)
        self.assertLess(got["net"], got["resale"])
        self.assertAlmostEqual(got["mid"] / got["net"], 0.82)


class Duplicates(unittest.TestCase):
    def test_same_trade_from_log_and_mod_is_stored_once(self):
        import tempfile
        from market.service import Service
        folder = tempfile.mkdtemp()
        store = Service.__new__(Service)
        store.own_path = os.path.join(folder, "own.db")
        store.tape_path = os.path.join(folder, "bazaar.db")
        store._own()
        first = dict(player="SampleOwner", other="SampleBuyer", at=T0, got=["§dEclipse Dye"], gave=["§6Coins: 300,000,000"], source="log")
        self.assertTrue(store.add_trade(first)[1])
        self.assertFalse(store.add_trade(dict(first, at=T0 + 900, source="mod"))[1])
        self.assertTrue(store.add_trade(dict(first, at=T0 + 3_600_000))[1], "a real repeat an hour later still counts")


class Flips(unittest.TestCase):
    def setUp(self):
        self.catalog, self.engine, self.tracker = service()

    def sale(self, entry, price, seller, buyer, at, sid=None, count=1, **kw):
        item = from_json(raw(entry, count=count, **kw))
        self.engine.now = max(self.engine.now, at)
        return self.tracker.on_sale(sid or f"{entry}{at}", item, price // count, count, seller, buyer, at, at - 60_000, at + DAY, None)

    def trade(self, at, other, got=(), gave=()):
        return self.tracker.on_trade(dict(player="SampleOwner", other=other, at=at, got=list(got), gave=list(gave)))

    def test_bal_flip_pays_the_fee(self):
        self.sale("bal_pet_legendary", 100_000_000, "SampleBuyer2", "SampleOwner", T0)
        flip = self.sale("bal_pet_legendary", 300_000_000, "SampleOwner", "SampleSeller2", T0 + DAY)
        self.assertEqual(flip["profit"], 300_000_000 - 100_000_000 - fees.listing_fee(300_000_000, 24))

    def test_trade_buy_then_ah_sale(self):
        self.trade(T0, "SampleBuyer", got=["§dEclipse Dye", "§dEclipse Dye"], gave=["§6Coins: 630,000,000"])
        flip = self.sale("dye_eclipse", 450_000_000, "SampleOwner", "x", T0 + DAY)
        self.assertEqual(flip["buy"]["via"], "trade")
        self.assertEqual(flip["buy"]["price"], 315_000_000)
        self.assertIn("via_trade", flip["flags"])
        self.assertIn("multi_item_trade", flip["flags"])

    def test_loan_comes_back_without_a_flip(self):
        self.trade(T0, "SampleBuyer", got=["§5Bottled Wisp"], gave=["§6Coins: 150,000,000"])
        self.trade(T0 + 1000, "SampleFriend", gave=["§5Bottled Wisp"])
        self.assertEqual(self.tracker.positions("SampleOwner"), [])
        record = self.trade(T0 + 2000, "SampleFriend", got=["§5Bottled Wisp"])
        self.assertEqual(record["kind"], "returned")
        self.assertEqual(self.tracker.positions("SampleOwner")[0]["unit"], 150_000_000, "the loan keeps its cost basis")
        self.assertEqual(self.tracker.flips, [])

    def test_moneyless_swap_is_two_loans(self):
        self.trade(T0, "SampleFriend", gave=["§6Renowned Warden Helmet"])
        record = self.trade(T0 + 1000, "SampleFriend", got=["§6Renowned Warden Helmet"], gave=["§dSuspicious Giant's Sword §6✪✪✪✪"])
        self.assertEqual(record["kind"], "returned")
        self.assertEqual(self.tracker.flips, [])

    def test_loan_with_coins_as_collateral(self):
        # 10-07: gave a Golden Dragon and 15b, got the pet and 14.7b back 40 minutes later
        self.trade(T0 - 1000, "SampleFriend", got=["§6§7[Lvl 200] §6Golden Dragon"], gave=["§6Coins: 40,000,000,000"])
        self.trade(T0, "DontSkipBlaze", gave=["§6§7[Lvl 200] §6Golden Dragon", "§6Coins: 15,000,000,000"])
        record = self.trade(T0 + 2_400_000, "DontSkipBlaze", got=["§6§7[Lvl 200] §6Golden Dragon", "§6Coins: 14,700,000,000"])
        self.assertEqual(record["kind"], "returned")
        self.assertEqual(self.tracker.flips, [])
        self.assertEqual(self.tracker.positions("SampleOwner")[0]["unit"], 40_000_000_000)

    def test_gift_on_the_ah_is_not_a_flip(self):
        self.engine.now = T0
        self.sale("warden_helmet", 3_000_000_000, "a", "SampleOwner", T0)
        item = from_json(raw("warden_helmet"))
        flip = self.tracker.on_sale("g", item, 1_000_000, 1, "SampleOwner", "Friend", T0 + 1000, T0 + 500, T0 + DAY,
                                    {"value": 3_000_000_000, "confidence": "high"})
        self.assertIsNone(flip)
        self.assertEqual(self.tracker.positions("SampleOwner"), [], "the seller's stake ended")

    def test_failed_listing_is_charged_to_the_next_sale(self):
        self.sale("warden_helmet", 1_000_000_000, "a", "SampleOwner", T0)
        item = from_json(raw("warden_helmet"))
        self.tracker.on_unlisted("SampleOwner", item, 2_000_000_000, T0 + DAY, T0 + DAY, T0 + 1000)
        flip = self.sale("warden_helmet", 1_500_000_000, "SampleOwner", "b", T0 + 2 * DAY)
        self.assertTrue(any("did not sell" in c["label"] for c in flip["changes"]))

    def test_an_old_purchase_is_not_paired_with_a_later_sale(self):
        # Fear, 2026-11-16: a drop sold today was paired with a copy bought two weeks earlier.
        self.sale("warden_helmet", 3_000_000_000, "a", "SampleOwner", T0)
        flip = self.sale("warden_helmet", 2_000_000_000, "SampleOwner", "b", T0 + 5 * DAY)
        self.assertIsNone(flip, "listed five days after the purchase: his own item, not a flip")
        self.assertEqual(self.tracker.flips, [])
        self.assertEqual(self.tracker.sales_without_buy[-1]["entry"], "warden_helmet")
        self.sale("warden_helmet", 2_500_000_000, "c", "SampleOwner", T0 + 6 * DAY)
        quick = self.sale("warden_helmet", 2_900_000_000, "SampleOwner", "d", T0 + 6 * DAY + 3_600_000)
        self.assertEqual(quick["buy"]["price"], 2_500_000_000, "the fresh purchase, never the stale one")

    def test_a_flip_listed_at_once_and_relisted_later_still_counts(self):
        self.sale("warden_helmet", 1_000_000_000, "a", "SampleOwner", T0)
        item = from_json(raw("warden_helmet"))
        self.tracker.on_unlisted("SampleOwner", item, 2_000_000_000, T0 + 2 * DAY, T0 + 2 * DAY, T0 + 3_600_000)
        flip = self.sale("warden_helmet", 1_500_000_000, "SampleOwner", "b", T0 + 6 * DAY)
        self.assertEqual(flip["buy"]["price"], 1_000_000_000)

    def test_a_listing_from_before_the_purchase_does_not_make_it_recent(self):
        item = from_json(raw("warden_helmet"))
        self.tracker.on_unlisted("SampleOwner", item, 2_000_000_000, T0 - DAY, T0 - DAY, T0 - 2 * DAY)
        self.sale("warden_helmet", 1_000_000_000, "a", "SampleOwner", T0)
        self.assertIsNone(self.sale("warden_helmet", 1_500_000_000, "SampleOwner", "b", T0 + 6 * DAY))

    def test_part_bought_by_the_player_costs_what_they_paid(self):
        self.engine.now = T0
        self.sale("necron_chestplate", 5_000_000_000, "a", "SampleOwner", T0)
        self.sale("recombobulator_3000", 300_000_000, "b", "SampleOwner", T0 + 1000)
        flip = self.sale("necron_chestplate", 7_000_000_000, "SampleOwner", "c", T0 + DAY, mods={"recombobulated": 1})
        self.assertIn("changed", flip["flags"])
        recomb = next(c for c in flip["changes"] if c["label"] == "Recombobulator")
        self.assertEqual((recomb["amount"], recomb["source"]), (-300_000_000, "bought"))
        self.assertEqual(self.tracker.stocks["SampleOwner"].lots.get("recombobulator_3000"), [], "it left stock")

    def test_cost_split_by_value_not_evenly(self):
        for i in range(6):
            self.sale("frozen_frag", 100_000_000, f"s{i}", f"b{i}", T0 + i * 1000)
            self.engine.add_sale(f"fa{i}", from_json(raw("frozen_frag")), 100_000_000, f"s{i}", f"b{i}", T0 + i * 1000, T0)
            self.engine.add_sale(f"wa{i}", from_json(raw("bottled_wisp")), 300_000_000, f"t{i}", f"c{i}", T0 + i * 1000, T0)
        self.engine.now = T0 + DAY
        self.trade(T0 + DAY, "z", got=["§5Frozen Fragment", "§5Bottled Wisp"], gave=["§6Coins: 400,000,000"])
        lots = {p["entry"]: p["unit"] for p in self.tracker.positions("SampleOwner")}
        self.assertLess(lots["frozen_frag"], lots["bottled_wisp"])

    def test_unknown_cost_stays_out_of_totals(self):
        self.trade(T0, "SampleTrader", got=["§5Bottled Wisp"])
        flip = self.sale("bottled_wisp", 200_000_000, "SampleOwner", "x", T0 + DAY)
        self.assertIn("unknown_cost", flip["flags"])
        self.assertEqual(self.tracker.summary("SampleOwner")["flips"], 0)


if __name__ == "__main__":
    unittest.main()
