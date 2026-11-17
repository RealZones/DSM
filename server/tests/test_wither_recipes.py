"""Official conversion coverage, finite cost plans and preserved purchase cost."""
import copy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_wither_recipes import merge, normalize, BLADES
import make_recipes
from test_crafts import make, list_
from test_market import T0, DAY, raw
from market.items import from_json
from market.tracker import Tracker, Lot


def route(need, kind="craft", carrier=None):
    row = {"need": need, "yield": 1, "coins": 0, "missing": [], "kind": kind, "text": kind + ": example"}
    if carrier:
        row["carrier"] = carrier
    return row


class WitherRecipes(unittest.TestCase):
    def recipes(self):
        return {"sword": [route({"ingot": 100}), route({"blade": 1, "handle": 8}, "conversion", "blade")],
                "blade": [route({"ingot": 2}), route({"sword": 1, "goop": 8}, "conversion", "sword")]}

    def test_cheaper_blade_can_be_crafted_then_converted(self):
        engine, crafts = make(self.recipes())
        list_(engine, "base", "ingot", 10)
        # Supply is intentionally bounded, as live AH listings are.
        load = lambda e: {"ingot": [[10, 100, "shop", None]], "handle": [[1, 8, "shop", None]]}.get(e, [])
        plan = crafts.cheapest("sword", load=load)
        self.assertEqual(plan.cost, 28)
        blade = next(p for p in plan.parts if p.entry == "blade")
        self.assertEqual(blade.method, "craft")
        self.assertEqual(blade.cost, 20)

    def test_buying_another_blade_can_be_cheaper(self):
        _, crafts = make(self.recipes())
        load = lambda e: {"ingot": [[10, 100, "shop", None]], "blade": [[5, 1, "ah", "seller"]],
                          "handle": [[1, 8, "shop", None]]}.get(e, [])
        plan = crafts.cheapest("sword", load=load)
        self.assertEqual(plan.cost, 13)
        self.assertEqual(next(p for p in plan.parts if p.entry == "blade").method, "buy")

    def test_cycle_cannot_buy_its_own_output_and_convert_it_back(self):
        _, crafts = make({"sword": [route({"blade": 1, "handle": 1})],
                          "blade": [route({"sword": 1, "handle": 1})]})
        self.assertIsNone(crafts.cheapest("sword", load=lambda e: [[1, 100, "shop", None]] if e in {"sword", "handle"} else []))

    def test_cyclic_estimate_cache_depends_on_ancestors(self):
        recipes = {"sword": [route({"blade": 1}), route({"ingot": 10})],
                   "blade": [route({"sword": 1}), route({"ingot": 100})]}
        engine, _ = make(recipes)
        engine.shop = {"ingot": 1}
        self.assertEqual(engine.craft_cost("blade", frozenset({"sword"})), 100)
        self.assertEqual(engine.craft_cost("blade"), 10)
        self.assertEqual(engine.craft_cost("sword"), 10)

    def test_conversion_cost_can_make_the_carrier_despite_a_higher_market_price(self):
        engine, _ = make(self.recipes())
        engine.shop = {"ingot": 10, "handle": 1, "blade": 500}
        self.assertEqual(engine.craft_cost("sword"), 28)

    def test_a_loop_back_to_the_blade_being_made_is_not_priced_at_npc(self):
        # Live 2026-11-16: the loop fell through to the catalog NPC price, so a "100m Astraea"
        # made every Hyperion 3-7b. With an NPC price of 1 the loop made this sword 17.
        recipes = {"sword": [route({"ingot": 100}), route({"blade": 1, "handle": 8}, "conversion", "blade")],
                   "blade": [route({"ingot": 90}), route({"sword": 1, "goop": 8}, "conversion", "sword")]}
        engine, _ = make(recipes)
        engine.shop = {"ingot": 1, "handle": 1, "goop": 1}
        engine.catalog.npc = lambda entry: 1
        self.assertEqual(engine.craft_cost("sword"), 98)

    def test_official_data_has_all_twelve_conversions_and_four_directs(self):
        data = Path(__file__).resolve().parents[1] / "data" / "recipe_overrides.json"
        overrides = json.loads(data.read_text(encoding="utf-8"))
        self.assertTrue(BLADES <= set(overrides["recipes"]))
        for entry, rows in overrides["recipes"].items():
            if entry not in BLADES:
                continue
            self.assertEqual(len(rows), 4)
            self.assertEqual({r["carrier"] for r in rows if r["kind"] == "conversion"}, BLADES - {entry})
        hyperion = overrides["recipes"]["hyperion"]
        self.assertIn({"maxor_wand": 8, "valkyrie": 1}, [r["need"] for r in hyperion])
        first = merge({}, overrides)
        self.assertEqual(merge(copy.deepcopy(first), overrides), first)

    def test_hidden_available_routes_are_imported_but_disabled_routes_are_not(self):
        rows = []
        for blade in BLADES:
            rows.append({"out": blade, "count": 1, "inputs": {"material": 1}, "id": blade,
                         "available": True, "visible": True})
            for carrier in BLADES - {blade}:
                rows.append({"out": blade, "count": 1, "inputs": {carrier: 1}, "id": blade + carrier,
                             "available": True, "visible": False})
        rows.append(dict(rows[0], available=False))
        rows.append(dict(rows[0], displayOnly=True))
        catalog = {"items": {e: {"name": e} for e in BLADES | {"material"}}}
        self.assertEqual(sum(map(len, normalize({"recipes": rows}, catalog, "wiki")["recipes"].values())), 16)

    def test_scan_rebuild_keeps_alternative_routes_without_duplicates(self):
        from test_crafts import CATALOG
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "catalog.json").write_text(json.dumps(CATALOG))
            (root / "forge.txt").write_text("")
            (root / "recipes.txt").write_text("Sword\t1\t1*Blade\nSword\t1\t8*Handle\t1*Blade\n")
            existing = {"sword": [route({"blade": 1})]}
            (root / "recipes.json").write_text(json.dumps(existing))
            override = route({"blade": 1, "handle": 8}, "conversion", "blade")
            (root / "recipe_overrides.json").write_text(json.dumps({"recipes": {"sword": [override]}}))
            with patch.object(make_recipes, "DATA", folder), patch.object(make_recipes, "CONFIG", folder), contextlib.redirect_stdout(io.StringIO()):
                make_recipes.main()
                make_recipes.main()
            rows = json.loads((root / "recipes.json").read_text())["sword"]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["kind"], "conversion")
            self.assertEqual(rows[1]["carrier"], "blade")

    def test_converted_blade_keeps_actual_purchase_cost_in_flip(self):
        recipes = {"sword": [route({"blade": 1, "handle": 8}, "conversion", "blade")]}
        engine, _ = make(recipes)
        engine.shop = {"blade": 100, "handle": 2}
        tracker = Tracker(engine, ("SampleOwner",))
        stock = tracker.stocks["Maker"]
        # Upgrades on the bought carrier are already included in these 60 coins.
        carrier = from_json(raw("blade", "Blade", mods={"recombobulated": True, "star_level": 3}))
        stock.lots["blade"].append(Lot(carrier, 1, 60, T0, "trade", "seller", "buy"))
        stock.lots["handle"].append(Lot(from_json(raw("handle", "Handle")), 8, 1, T0, "ah", "s", "parts"))
        output = from_json(raw("sword", "Sword", mods={"recombobulated": True, "star_level": 3}))
        flip = tracker._close("Maker", output, 1, 200, T0 + DAY, "ah", "buyer", "sell")
        self.assertEqual(flip["profit"], 132)
        self.assertEqual(sum(c["amount"] for c in flip["changes"]), -68)
        self.assertEqual(stock.lots["blade"], [])
        self.assertEqual(stock.lots["handle"], [])


if __name__ == "__main__":
    unittest.main()
