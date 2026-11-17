"""Pictures drawn from Dragsim's resource pack (market/pictures.py), against a tiny pack built here."""
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from PIL import Image                      # noqa: E402
from market import pictures                # noqa: E402

RED, BLUE, GREEN = (200, 30, 30, 255), (30, 30, 200, 255), (30, 200, 30, 255)


def png(colour, size=(16, 16), bottom=None):
    img = Image.new("RGBA", size, colour)
    if bottom:
        img.paste(Image.new("RGBA", (size[0], size[1] // 2), bottom), (0, size[1] // 2))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def definition(model):
    return json.dumps({"model": {"type": "minecraft:model", "model": model}})


# A red 8-block cube in the inventory's usual view, like a block item.
CUBE = {"textures": {"0": "test:item/red"},
        "elements": [{"from": [4, 4, 4], "to": [12, 12, 12],
                      "faces": {side: {"uv": [0, 0, 16, 16], "texture": "#0"} for side in ("north", "south", "east", "west", "up", "down")}}],
        "display": {"gui": {"rotation": [30, 225, 0], "scale": [0.625, 0.625, 0.625]}}}


def build_pack(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("assets/test/items/test_helm.json", definition("test:item/test_helm"))
        z.writestr("assets/test/models/item/test_helm.json", json.dumps(CUBE))
        z.writestr("assets/test/textures/item/red.png", png(RED))
        z.writestr("assets/test/items/gear/flat_thing.json", definition("test:item/flat_thing"))
        z.writestr("assets/test/models/item/flat_thing.json",
                   json.dumps({"parent": "minecraft:item/generated", "textures": {"layer0": "test:item/strip"}}))
        z.writestr("assets/test/textures/item/strip.png", png(BLUE, (16, 32), bottom=GREEN))     # two animation frames
        z.writestr("assets/test/textures/item/strip.png.mcmeta", json.dumps({"animation": {}}))
        z.writestr("assets/a/items/twice.json", definition("test:item/flat_thing"))
        z.writestr("assets/b/items/twice.json", definition("test:item/flat_thing"))
        z.writestr("assets/test/items/effect/7.json", definition("test:item/flat_thing"))       # a frame of an effect
        z.writestr("assets/test/items/headlike.json", json.dumps({"model": {"type": "minecraft:special", "model": {"type": "minecraft:head"}}}))
        z.writestr("assets/test/items/broken.json", definition("test:item/missing"))
        z.writestr("assets/test/items/furfsky/blade.json", definition("test:item/flat_thing"))     # Dragsim's Furfsky skins
        z.writestr("assets/test/items/furfsky/bow.json", definition("test:item/bow"))
        z.writestr("assets/test/models/item/bow.json",          # on vanilla's bow model, which the pack does not carry
                   json.dumps({"parent": "minecraft:item/bow", "textures": {"layer0": "test:item/strip"}}))


class Pictures(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.pack = os.path.join(self.folder, "pack.zip")
        build_pack(self.pack)
        self.out = os.path.join(self.folder, "pictures")

    def picture(self, entry):
        return Image.open(os.path.join(self.out, entry + ".png")).convert("RGBA")

    def test_an_item_or_its_skin_gets_the_packs_model_and_nothing_is_guessed(self):
        done = pictures.draw(self.pack, ["test_helm", "test_helm_skin", "flat_thing", "furfsky_blade_skin", "furfsky_bow_skin",
                                         "twice", "7", "headlike", "broken", "nothing", "../evil", "minecraft:stone"], self.out)
        self.assertEqual(done, {"test_helm", "test_helm_skin", "flat_thing", "furfsky_blade_skin", "furfsky_bow_skin"},
                         "a skin shows what it is a skin for, found by name or by folder, on any flat parent; a shared "
                         "name, an effect frame, a special model, a missing model and an unsafe file name are skipped")
        self.assertEqual(len(os.listdir(self.out)), 5)

    def test_a_flat_item_is_its_first_frame(self):
        pictures.draw(self.pack, ["flat_thing"], self.out)
        img = self.picture("flat_thing")
        self.assertEqual(img.size, (pictures.SIZE, pictures.SIZE))
        self.assertEqual({colour for _, colour in img.getcolors(1 << 16)}, {BLUE}, "an animation strip shows its top frame only")

    def test_a_cuboid_is_drawn_as_the_inventory_lights_it(self):
        pictures.draw(self.pack, ["test_helm"], self.out)
        img = self.picture("test_helm")
        self.assertEqual(img.getpixel((0, 0))[3], 0, "around the model is clear")
        # Where the faces land in the usual block view: the top above the middle, two sides below.
        top, left, right = img.getpixel((48, 35)), img.getpixel((37, 53)), img.getpixel((59, 53))
        self.assertTrue(all(p[3] == 255 for p in (top, left, right)), (top, left, right))
        self.assertGreater(top[0], left[0], "the top is lit most")
        self.assertGreater(left[0], right[0], "and the left side more than the right, as in the inventory")
        self.assertEqual(top[:3], RED[:3], "the top is the texture at full brightness")

    def test_a_picture_is_drawn_again_only_for_a_newer_pack(self):
        pictures.draw(self.pack, ["flat_thing"], self.out)
        path = os.path.join(self.out, "flat_thing.png")
        stamp = os.path.getmtime(self.pack)
        os.utime(path, (stamp + 5, stamp + 5))
        pictures.draw(self.pack, ["flat_thing"], self.out)
        self.assertEqual(os.path.getmtime(path), stamp + 5, "newer than the pack: kept")
        os.utime(path, (stamp - 5, stamp - 5))
        pictures.draw(self.pack, ["flat_thing"], self.out)
        self.assertGreater(os.path.getmtime(path), stamp - 5, "older than the pack: drawn again")

    def test_without_pillow_there_are_no_pictures_and_no_error(self):
        with patch.object(pictures, "Image", None):
            self.assertEqual(pictures.draw(self.pack, ["test_helm"], self.out), set())


if __name__ == "__main__":
    unittest.main()
