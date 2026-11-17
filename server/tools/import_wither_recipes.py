"""Import all available Wither Blade routes from the official wiki, including hidden conversions.

Run on the server, then run make_recipes.py on the PC to retain these on later scan imports.
The same normalized override file can also be consumed by the legacy networth index builder.
"""
import json
from pathlib import Path
import urllib.request

BASE = "https://dragsim.co"
BLADES = {"hyperion", "valkyrie", "scylla", "astraea"}


def normalize(content, catalog, source):
    recipes = {}
    for row in content["recipes"]:
        entry = row["out"]
        if entry not in BLADES or not row.get("available") or row.get("displayOnly"):
            continue
        need = row["inputs"]
        # Wiki 'visible' controls browsing, not whether the recipe works in game.
        carrier = next((e for e in need if e in BLADES), None)
        kind = "conversion" if carrier else "craft"
        text = ", ".join(f"{n}× {catalog['items'][e]['name']}" for e, n in need.items())
        recipes.setdefault(entry, []).append({
            "yield": row["count"], "need": need, "coins": 0, "missing": [],
            "kind": kind, "text": f"{kind}: {text}", "source": source,
            "wiki_id": row["id"], **({"carrier": carrier} if carrier else {}),
        })
    for entry in BLADES:
        rows = recipes.get(entry, [])
        if len(rows) != 4 or sum(r["kind"] == "conversion" for r in rows) != 3:
            raise ValueError(f"Expected direct recipe and three conversions for {entry}")
    return {"source": source, "recipes": recipes}


def merge(recipes, overrides):
    for entry, incoming in overrides["recipes"].items():
        rows = recipes.setdefault(entry, [])
        for row in incoming:
            # Enrich a matching scanned route; never lose its provenance/carrier on rebuild.
            match = next((r for r in rows if r.get("need") == row["need"]
                          and (r.get("yield") or 1) == row["yield"]
                          and (r.get("coins") or 0) == row["coins"]), None)
            if match is None:
                rows.append(dict(row))
            else:
                match.update(row)
    return recipes


def main():
    data = Path(__file__).resolve().parents[1] / "data"
    def get(path):
        with urllib.request.urlopen(BASE + path, timeout=60) as response:
            return json.load(response)
    index = get("/data/wiki/index.json")
    source = BASE + "/data/wiki/" + index["content"]
    overrides = normalize(get("/data/wiki/" + index["content"]),
                          json.loads((data / "catalog.json").read_text(encoding="utf-8")), source)
    target = data / "recipe_overrides.json"
    target.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")
    recipes = json.loads((data / "recipes.json").read_text(encoding="utf-8"))
    merge(recipes, overrides)
    (data / "recipes.json").write_text(json.dumps(recipes, ensure_ascii=False), encoding="utf-8")
    print("Imported four direct recipes and twelve conversions from", source)


if __name__ == "__main__":
    main()
