"""One model of an item, from any of the three places one can be seen.

The market feed, the game's own NBT and a trade receipt describe the same item in different
words, and flip tracking only works if all three end up as the same thing:

    market feed   full JSON, `extra.item_skin.skinId`, nothing on when it was applied
    in-game NBT   the same JSON plus `appliedAt` and `applierUuid` on a skin or dye
    trade receipt only text: "§dRenowned Warden Helmet §6✪✪✪✪✪"

No item has an id of its own on Dragsim. The nearest thing is that `appliedAt`, which pins one
exact copy, and only the mod can see it because the feed strips it. So identity is built from
what a player chose, with `identity()` as the strict form and `Item.base` as the loose one.
"""
import collections
import json
import os
import re

RARITIES = ["NONE", "COMMON", "UNCOMMON", "RARE", "EPIC", "LEGENDARY", "MYTHIC", "DIVINE", "SPECIAL",
            "VERY_SPECIAL", "SUPREME", "UNOBTAINABLE"]
RANK = {name: i for i, name in enumerate(RARITIES)}
COLOUR_RARITY = {"f": "COMMON", "a": "UNCOMMON", "9": "RARE", "5": "EPIC", "6": "LEGENDARY", "d": "MYTHIC",
                 "b": "DIVINE", "c": "SPECIAL"}
STAR = "✪"
SYMBOLS = "◆✦ "
_COLOUR = re.compile(r"§.")
_PET_NAME = re.compile(r"^\[Lvl (\d+)\] (.+)$")
_LEVEL = re.compile(r"^\[Lvl \d+\] ")
_PET_ENTRY = re.compile(r"^(.+)_pet_(common|uncommon|rare|epic|legendary|mythic|divine|special)$")
# dragsim.co's picture of a plain Minecraft item: flat ("item-bow.png") or as a block ("block-blue_ice.png").
_VANILLA_ICON = re.compile(r"^/assets/vanilla/(?:item|block)-([a-z0-9_]+)\.png$")
HEADS = {"player_head", "skull_item"}
WITHER_BLADES = frozenset({"necron_blade", "hyperion", "scylla", "valkyrie", "astraea"})
WITHER_SCROLLS = {"implosion": "implosion_scroll", "shadow_warp": "shadow_warp_scroll",
                 "wither_shield": "wither_shield_scroll"}


def applied_scrolls(raw, entry=None):
    """The feed flattens abilities; in-game custom_data wraps the same list in a compound.

    Wither Impact replaces the three individual abilities on a fully scrolled blade.
    Do not infer scrolls from the blade's catalog/default abilities or unrelated weapons.
    """
    if (entry or raw.get("entry") or "").lower() not in WITHER_BLADES:
        return ()
    abilities = raw.get("abilities")
    if isinstance(abilities, dict):
        abilities = abilities.get("abilities")
    if not isinstance(abilities, (list, tuple)):
        abilities = ()
    names = {a.lower() for a in abilities if isinstance(a, str)}
    extra = raw.get("extra")
    scrolls = extra.get("wither_scrolls") if isinstance(extra, dict) else None
    if (isinstance(scrolls, dict) and scrolls.get("mask") in (7, "7")) or "wither_impact" in names:
        return tuple(WITHER_SCROLLS.values())
    return tuple(value for name, value in WITHER_SCROLLS.items() if name in names)


def plain(text):
    return _COLOUR.sub("", text or "").strip()


FILTERS = ("rarity", "stars", "reforge", "recomb", "hpb", "ghpb", "aow", "sync", "enrich", "dye", "skin", "pet_level", "pet_item", "pet_skin", "engine", "tank", "module")
LEVELS = ((1, 99, "1-99"), (100, 100, "100"), (101, 199, "101-199"), (200, 200, "200"))


def field(item, key):
    """One item's answer for one filter, as the text the page sends back."""
    if key == "rarity":
        return RARITIES[item.rarity] if item.rarity < len(RARITIES) else ""
    if key == "recomb":
        return "yes" if item.recomb else "no"
    if key == "aow":
        return "yes" if item.aow else "no"
    if key == "pet_level":
        return next((label for low, high, label in LEVELS if low <= (item.pet_level or 0) <= high), "")
    if key in ("stars", "hpb", "ghpb", "sync"):
        return str(getattr(item, key) or 0)
    return str(getattr(item, key) or "none")


def passes(item, wanted):
    """Item filters, for everything that moves a price on Dragsim."""
    return all(field(item, key) == value for key, value in wanted.items())


LEARNED = "learned_items.json"
STAR_GLYPHS = "✪➊➋➌➍➎"


def read_extra(catalog_path):
    """What the published list leaves out. learned_items.json is the service's own record of ids
    the AH showed before the list had them; extra_items.json comes from tools/harvest_items.py and
    wins where both have an id, since it took the commonest name over many copies."""
    folder = os.path.dirname(os.path.abspath(catalog_path))
    out = {}
    for name in (LEARNED, "extra_items.json"):
        path = os.path.join(folder, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                out.update(json.load(f))
    return out


def material_of(raw):
    """A copy's base Minecraft item without its namespace: "minecraft:bow" is "bow". An id that is
    itself a Minecraft one (minecraft:lime_terracotta) is its own base item."""
    value = raw.get("material")
    if not isinstance(value, str) or not value:
        entry = str(raw.get("entry") or "")
        value = entry if entry.startswith("minecraft:") else ""
    return value.rsplit(":", 1)[-1].lower() or None


def picture_fields(raw):
    """What the site draws an unlisted item with, read off one of its copies: a skull's head texture,
    and the base Minecraft item, since dragsim.co's pictures of the items it lists are those drawn
    plainly. Rows kept only the head until 2026-11-10, so 80-odd learned items showed a letter."""
    out = {}
    skin = raw.get("item_skin")
    value = skin.get("value") if isinstance(skin, dict) else skin
    if isinstance(value, str) and value:
        out["skin"] = {"value": value}
    material = material_of(raw)
    if material:
        out["material"] = material
    return out


def learned_row(raw):
    """A catalog row for an id the published list does not have, read off one of its own copies:
    the name without its reforge or stars, the rarity before any recombobulator, the feed's own
    type (COSMETIC, PET, ...), and what to draw it with (picture_fields)."""
    d = raw.get("item_data") or {}
    name = plain(d.get("item_name"))
    reforge = plain(d.get("reforge"))
    if reforge and name.lower().startswith(reforge.lower() + " "):
        name = name[len(reforge) + 1:]
    name = name.strip(STAR_GLYPHS + SYMBOLS)
    if raw["entry"].lower().endswith("_rune"):
        # A rune's copy names its level ("◆ Meow Music Rune III"); the id is every level.
        name = re.sub(r" (?:I|II|III|IV|V)$", "", name)
    name = name or raw["entry"].replace("_", " ").title()
    rank = RANK.get(d.get("rarity") or "NONE", 0)
    if (raw.get("item_modifiers") or {}).get("recombobulated") and rank > 1:
        rank -= 1
    row = {"name": name, "tier": RARITIES[rank] if rank else None, "category": str(d.get("item_type") or "ITEM").upper(),
           "learned": True}
    row.update(picture_fields(raw))
    return row


class Catalog:
    """Dragsim's item list, with the lookups the rest of the code keeps needing. `extra` holds what
    the published list leaves out, which is most skins and runes (see read_extra)."""

    def __init__(self, data, extra=None):
        self.data = data
        self.extra = extra or {}
        self.items = {k.lower(): v for k, v in data["items"].items()}
        self.published = set(self.items)
        for entry, item in self.extra.items():
            self.items.setdefault(entry, item)
        # Each plain Minecraft item dragsim.co has a picture of, read off its own rows, which also
        # settles item- or block- for it. A hand-drawn one (/assets/items/lever.png) is not counted.
        self.vanilla = {}
        for entry in self.published:
            m = _VANILLA_ICON.match(self.items[entry].get("icon") or "")
            if m:
                self.vanilla.setdefault(m.group(1), m.group(0))
        self.names = {}
        self.named = collections.defaultdict(list)     # display name -> every entry that shows it
        self.litters = collections.defaultdict(list)    # a pet -> its entry at each rarity
        self.ambiguous = set()
        for entry, item in self.items.items():
            name = plain(item.get("name"))
            if not name:
                continue
            if name in self.names and self.names[name] != entry:
                self.ambiguous.add(name)
            self.names.setdefault(name, entry)
            # A rune's diamond comes and goes between the item and a receipt, so it never decides.
            self.names.setdefault(name.lstrip(SYMBOLS), entry)
            self.named[name.lstrip(SYMBOLS)].append(entry)
            pet = _PET_ENTRY.match(entry)
            if pet:
                self.litters[pet.group(1)].append(entry)
        self.pets = data.get("pets") or {}
        self.reforge_stone = {}
        for entry, item in self.items.items():
            for line in item.get("description") or ():
                m = re.search(r"Applies the (?:§.)+([A-Za-z' ]+?)(?:§.)+ ?reforge", line)
                if m:
                    self.reforge_stone[m.group(1).strip().lower()] = entry
        self.reforges = {r.lower() for r in (data.get("reforges") or {})} | set(self.reforge_stone)
        # Longest first, because "Very Wise" must beat "Wise" when a name is split.
        self._prefixes = sorted(self.reforges, key=len, reverse=True)
        self.cosmetics = {e: i["cosmetic"] for e, i in self.items.items() if i.get("cosmetic")}
        self.bazaar = {k.lower() for k in (data.get("itemRules") or {}).get("bazaarProducts", {})}

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        extra = read_extra(path)
        metadata_path = os.path.join(os.path.dirname(path), 'pricing_metadata.json')
        metadata = {}
        if os.path.exists(metadata_path):
            with open(metadata_path, encoding='utf-8') as f:
                metadata = json.load(f)
            # Official wiki definitions fill absent catalog entries without replacing
            # learned or user-audited fields already in this catalog.
            for entry, definition in metadata.get('items', {}).items():
                if entry not in data['items']:
                    extra.setdefault(entry, definition)
        result = cls(data, extra)
        result.reforge_stones = {name: list(ids) for name, ids in metadata.get('reforge_stones', {}).items()}
        for name, entry in result.reforge_stone.items():
            if entry not in result.reforge_stones.setdefault(name, []):
                result.reforge_stones[name].append(entry)
        return result

    def vanilla_picture(self, entry):
        """For an item dragsim.co does not list, its picture of the item's base Minecraft item (Death
        Bow is a bow), plus a second path to try when it has never drawn that base item and so
        whether it is item- or block- is unknown. Listed items stay as dragsim.co shows them, and a
        head is drawn from its skin instead. Returns (path, other path or None), or None."""
        if entry in self.published:
            return None
        material = str((self.items.get(entry) or {}).get("material") or "").lower()
        if not material or material in HEADS:
            return None
        if material in self.vanilla:
            return self.vanilla[material], None
        return f"/assets/vanilla/item-{material}.png", f"/assets/vanilla/block-{material}.png"

    def tier(self, entry):
        item = self.items.get(entry) or {}
        return item.get("tier")

    def npc(self, entry):
        item = self.items.get(entry) or {}
        return item.get("npc_sell_price") or 0

    def name(self, entry):
        item = self.items.get(entry) or {}
        return _LEVEL.sub("", plain(item.get("name"))) or entry.replace("_", " ").title()

    def litter(self, entry):
        """Every rarity of this pet, or nothing if it is not a pet."""
        pet = _PET_ENTRY.match(entry)
        return self.litters.get(pet.group(1), [entry]) if pet else []

    def dye_entry(self, dye_id):
        """Applied dyes are named after their item, in either word order."""
        if not dye_id:
            return None
        dye_id = dye_id.lower()
        for option in (dye_id, "dye_" + dye_id.removesuffix("_dye"), dye_id.removeprefix("dye_") + "_dye"):
            if option in self.items:
                return option
        return dye_id

    def skin_entry(self, skin_id):
        if not skin_id:
            return None
        skin_id = skin_id.lower()
        for option in (skin_id, "skin_" + skin_id, skin_id + "_skin"):
            if option in self.items:
                return option
        return skin_id

    def split_name(self, name):
        """Takes "Renowned Warden Helmet" apart. The catalog decides, so a reforge word that is
        part of the item's own name ("Ancient Rose") is never stripped by mistake."""
        name = plain(name).lstrip(SYMBOLS)
        if name in self.names:
            return None, self.names[name]
        low = name.lower()
        for prefix in self._prefixes:
            if low.startswith(prefix + " "):
                rest = name[len(prefix) + 1:]
                if rest in self.names:
                    return prefix, self.names[rest]
        return None, None


class Item:
    __slots__ = ("entry", "count", "name", "rarity", "reforge", "stars", "recomb", "hpb", "ghpb", "sync", "aow",
                 "enrich", "skin", "dye", "pet", "pet_level", "pet_item", "pet_skin", "pet_candy", "engine", "tank",
                 "module", "coated", "applied_at", "partial", "npc", "attunement", "scrolls", "rune")

    def __init__(self, entry):
        self.entry = entry
        self.count = 1
        self.name = None
        self.rarity = 0
        self.reforge = ""
        self.stars = 0
        self.recomb = False
        self.hpb = 0
        self.ghpb = 0
        self.sync = 0
        self.aow = 0
        self.enrich = ""
        self.skin = None
        self.dye = None
        self.pet = False
        self.pet_level = None
        self.pet_item = None
        self.pet_skin = None
        self.pet_candy = 0
        self.engine = None
        self.tank = None
        self.module = None
        self.coated = False
        self.applied_at = ()
        self.partial = False    # only a name was seen, so what is unset here is unknown, not absent
        self.npc = 0
        self.attunement = None
        self.scrolls = ()
        self.rune = None

    @property
    def base(self):
        return (self.entry, self.rarity if self.pet else 0)

    def identity(self):
        """Legacy tracker matching shape; changing it requires proven exact-copy identity."""
        return (self.entry, self.rarity, self.reforge, self.stars, self.recomb, self.hpb, self.ghpb, self.sync,
                self.aow, self.enrich, self.skin, self.dye, self.pet_item, self.pet_skin, self.engine, self.tank,
                self.module, self.coated)

    def pricing_identity(self):
        """Price grouping includes applied identities without changing trade matching rules."""
        original = self.identity()
        return original + (self.scrolls, self.rune) if self.scrolls or self.rune else original

    def upgrades(self):
        """Things on this item that cost something, as {name: amount}."""
        out = {}
        for key in ("stars", "hpb", "ghpb", "sync", "aow"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if self.recomb:
            out["recomb"] = 1
        for key in ("enrich", "skin", "dye", "pet_item", "pet_skin", "engine", "tank", "module"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if self.reforge:
            out["reforge"] = self.reforge
        if self.coated:
            out["coated"] = 1
        if self.scrolls:
            out["scrolls"] = "+".join(self.scrolls)
        if self.rune:
            out["rune"] = self.rune
        return out

    def label(self):
        return self.name or self.entry.replace("_", " ").title()

    def bare(self):
        """The same item with everything a player added taken off, what a part-by-part value is
        built on. A recomb takes its rarity step with it."""
        out = Item(self.entry)
        out.count = self.count
        out.name = self.name
        out.rarity = max(self.rarity - 1, 0) if self.recomb else self.rarity
        out.pet = self.pet
        out.pet_level = self.pet_level
        out.npc = self.npc
        return out


def resolve_entry(raw, catalog):
    """In-game NBT has no entry field, only the base name, so the catalog supplies it. The base
    name never holds a reforge, so an exact match cannot pick the wrong item. Pets are not in the
    item list at all: their id is built from the name and rarity, as the market feed writes it."""
    if catalog is None:
        return ""
    data = raw.get("item_data") or {}
    name = plain(data.get("item_name"))
    pet = _PET_NAME.match(name)
    if pet:
        low = pet.group(2).strip().lower().replace(" ", "_").replace("'", "")
        return f"{low}_pet_{(data.get('rarity') or 'legendary').lower()}"
    return catalog.names.get(name, "")


def from_json(raw, catalog=None):
    """Market feed and profile JSON. In-game NBT reaches here too, in the same shape, because
    the mod sends its custom_data as it stands."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    d = raw.get("item_data") or {}
    m = raw.get("item_modifiers") or {}
    ex = raw.get("extra") or {}
    item = Item((raw.get("entry") or resolve_entry(raw, catalog)).lower())
    item.count = raw.get("count") or 1
    item.name = plain(d.get("item_name"))
    item.rarity = RANK.get(d.get("rarity") or "NONE", 0)
    item.reforge = (d.get("reforge") or "").lower()
    item.stars = int(m.get("star_level") or 0)
    item.recomb = bool(m.get("recombobulated"))
    item.hpb = int(m.get("hot_potato_books") or 0)
    item.ghpb = int(m.get("golden_hot_potato_books") or 0)
    item.sync = int(m.get("final_synchronizers") or 0)
    item.aow = int(m.get("art_of_war") or 0)
    item.enrich = str(m.get("enrichment") or "")
    item.npc = d.get("npc_sell_price") or 0
    item.attunement = ex.get("attunement")
    item.scrolls = applied_scrolls(raw, item.entry)
    rune = m.get("rune_id")
    item.rune = rune.lower() if isinstance(rune, str) and re.fullmatch(r"[a-z0-9_]{1,128}", rune.lower()) else None
    # A real sale used item_skin while the site's renderer reads item_skin_applied, so both.
    skin = ex.get("item_skin_applied") or ex.get("item_skin") or {}
    dye = ex.get("item_dye") or {}
    item.skin = skin.get("skinId")
    item.dye = dye.get("dyeId")
    stamps = [x.get("appliedAt") for x in (skin, dye) if x.get("appliedAt")]
    item.applied_at = tuple(sorted(stamps))
    pet = ex.get("pet_data")
    if pet is not None:
        item.pet = True
        item.pet_item = pet.get("equippedItem")
        item.pet_skin = pet.get("skinOverride")
        item.pet_candy = pet.get("candiesUsed") or 0
        name = _PET_NAME.match(item.name or "")
        if name:
            item.pet_level = int(name.group(1))
    drill = ex.get("drill") or ex.get("drill_data") or {}
    item.engine = drill.get("engine")
    item.tank = drill.get("tank")
    item.module = drill.get("module")
    item.coated = bool(drill.get("coated") or (ex.get("coating") or {}).get("coated"))
    return item


def from_receipt(text, catalog):
    """A line off a trade receipt, colour codes intact, because the colour is the rarity. Returns
    (kind, amount, item): ("coins", n, None), ("gems", n, None) or ("item", n, Item)."""
    bare = plain(text)
    money = re.match(r"^(Coins|Gems): ([\d,]+)$", bare)
    if money:
        return money.group(1).lower(), int(money.group(2).replace(",", "")), None
    count = 1
    stack = re.match(r"^(\d+)x (.+)$", bare)
    if stack:
        count, bare = int(stack.group(1)), stack.group(2)
    stars = bare.count(STAR)
    bare = bare.replace(STAR, "").strip()
    colour = re.search(r"§([0-9a-f])", text)
    # A pet line starts grey for its [Lvl n] tag, so the last colour is the one on the name.
    colours = re.findall(r"§([0-9a-f])", text.replace("§6" + STAR, ""))
    rarity = COLOUR_RARITY.get(colours[-1] if colours else (colour.group(1) if colour else ""), None)
    pet = _PET_NAME.match(bare)
    if pet:
        name = pet.group(2).strip()
        low = name.lower().replace(" ", "_").replace("'", "")
        entry = f"{low}_pet_{(rarity or 'legendary').lower()}"
        item = Item(entry)
        item.pet = True
        item.pet_level = int(pet.group(1))
        item.rarity = RANK.get(rarity or "LEGENDARY", 0)
        item.name = bare
        item.partial = True
        return "item", count, item
    reforge, entry = catalog.split_name(bare)
    item = Item(entry or bare.lstrip(SYMBOLS).lower().replace(" ", "_").replace("'", ""))
    item.reforge = reforge or ""
    item.stars = stars
    item.count = count
    item.name = bare + (" " + STAR * stars if stars else "")
    item.rarity = RANK.get(rarity, 0) if rarity else 0
    base = catalog.tier(item.entry) if entry else None
    if base and rarity and RANK.get(rarity, 0) == RANK.get(base, 0) + 1:
        item.recomb = True   # one tier over the catalog's tier is what a recomb does
    item.partial = True
    return "item", count, item
