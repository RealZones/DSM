"""Pictures of Dragsim's own items, drawn from its resource pack the way an inventory slot shows them.

dragsim.co has no picture for an item it does not list, and a plain Minecraft item is a poor stand-in
for the new gear: Colossus armour is iron underneath and every weapon skin is paper, but in game each
one is a model from Dragsim's pack (https://dragsim.co/textures/dragsim.zip). The pack names an item
definition after the item (skeleton_em:colossus_helmet, dragsim:f1/mana_draught), so an item is matched
by its id, and a skin by the item it is a skin for. A name two definitions share is skipped, not guessed.

Flat items (item/generated) are their texture. The rest are cuboids, drawn here through the model's own
inventory view (display.gui) with nothing but Pillow: no game and no GPU. Good enough to tell items
apart, not pixel-exact: the lighting is approximate, and faces are painted far to near by their
middles, so two faces that cross can overlap the wrong way.
"""
import collections
import io
import json
import math
import os
import re
import zipfile

try:
    from PIL import Image, ImageChops, ImageDraw
except ImportError:         # without Pillow the site runs with no pack pictures rather than not at all
    Image = None

SIZE = 96           # 16 x 6, so the site's 48 and 32 px icons are whole steps of it
SUPER = 4           # cuboids are drawn this much larger, then shrunk, for smooth edges
SAFE = re.compile(r"^[a-z0-9_]+$")      # an id that is also a file name
FLAT = {"item/generated", "item/handheld", "item/handheld_rod", "builtin/generated"}
LIGHT = (-0.6, 1.0, 0.6)    # from above and the left, as the inventory lights a block

Model = collections.namedtuple("Model", "textures elements gui light flat")


def _mode(group, name):
    """Pillow 9.1 moved its constants into enums; the VPS has 9.0."""
    return getattr(getattr(Image, group, Image), name)


def _rot(axis, angle):
    c, s = math.cos(angle), math.sin(angle)
    return {"x": ((1, 0, 0), (0, c, -s), (0, s, c)),
            "y": ((c, 0, s), (0, 1, 0), (-s, 0, c)),
            "z": ((c, -s, 0), (s, c, 0), (0, 0, 1))}[axis]


def _matmul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def _apply(m, v):
    return [sum(m[i][k] * v[k] for k in range(3)) for i in range(3)]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _corners(side, low, high):
    """A face's corners in model units, as seen from outside it: top-left, bottom-left, bottom-right,
    top-right. That order is what its uv [u1, v1, u2, v2] runs along."""
    (x0, y0, z0), (x1, y1, z1) = low, high
    return {"north": [(x1, y1, z0), (x1, y0, z0), (x0, y0, z0), (x0, y1, z0)],
            "south": [(x0, y1, z1), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1)],
            "west": [(x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1)],
            "east": [(x1, y1, z1), (x1, y0, z1), (x1, y0, z0), (x1, y1, z0)],
            "up": [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)],
            "down": [(x0, y0, z1), (x0, y0, z0), (x1, y0, z0), (x1, y0, z1)]}[side]


def _default_uv(side, low, high):
    """The game's uv for a face that names none: the part of the texture where the face sits."""
    (x0, y0, z0), (x1, y1, z1) = low, high
    return {"down": (x0, 16 - z1, x1, 16 - z0), "up": (x0, z0, x1, z1),
            "north": (16 - x1, 16 - y1, 16 - x0, 16 - y0), "south": (x0, 16 - y1, x1, 16 - y0),
            "west": (z0, 16 - y1, z1, 16 - y0), "east": (16 - z1, 16 - y1, 16 - z0, 16 - y0)}[side]


def _spin(point, spin):
    """An element's own rotation, about one axis through its origin."""
    axis = spin.get("axis", "y")
    if axis not in ("x", "y", "z"):
        return point
    angle = math.radians(float(spin.get("angle") or 0))
    origin = spin.get("origin") or (8, 8, 8)
    p = _apply(_rot(axis, angle), [point[i] - origin[i] for i in range(3)])
    if spin.get("rescale") and abs(math.cos(angle)) > 1e-6:
        p = [v if "xyz"[i] == axis else v / math.cos(angle) for i, v in enumerate(p)]
    return [p[i] + origin[i] for i in range(3)]


def _shade(normal, light):
    """How bright a face is by where it points. A model marked "front" is lit from the viewer, like a
    flat item; the rest from above and the left, so a block's top is brighter than its sides."""
    if light == "front":
        return 0.6 + 0.4 * max(0.0, normal[2])
    size = math.sqrt(sum(v * v for v in LIGHT))
    return min(1.0, 0.6 + 0.45 * max(0.0, sum(normal[i] * LIGHT[i] for i in range(3)) / size))


def _affine(p0, p1, p2, q0, q1, q2):
    """Coefficients taking a spot on the picture (x, y) to its texel (s, t), from three corners, for
    Pillow's AFFINE transform, which asks that way round."""
    (x0, y0), (x1, y1), (x2, y2) = p0, p1, p2
    det = x0 * (y1 - y2) - y0 * (x1 - x2) + (x1 * y2 - x2 * y1)
    if abs(det) < 1e-9:
        return None
    out = []
    for k in (0, 1):
        s0, s1, s2 = q0[k], q1[k], q2[k]
        out += [(s0 * (y1 - y2) + s1 * (y2 - y0) + s2 * (y0 - y1)) / det,
                (s0 * (x2 - x1) + s1 * (x0 - x2) + s2 * (x1 - x0)) / det,
                (s0 * (x1 * y2 - x2 * y1) + s1 * (x2 * y0 - x0 * y2) + s2 * (x0 * y1 - x1 * y0)) / det]
    return tuple(out)


def _model_ref(node):
    """The model an item shows at rest: a condition's false side, a select's fallback. Special
    models (heads, shields) are drawn by the game itself, so they have no model to draw here."""
    if not isinstance(node, dict):
        return None
    kind = str(node.get("type", "")).removeprefix("minecraft:")
    if kind == "model":
        return node.get("model") if isinstance(node.get("model"), str) else None
    if kind == "condition":
        return _model_ref(node.get("on_false")) or _model_ref(node.get("on_true"))
    if kind in ("select", "range_dispatch"):
        found = _model_ref(node.get("fallback"))
        for case in node.get("cases") or node.get("entries") or ():
            found = found or _model_ref(case.get("model"))
        return found
    if kind == "composite":
        for part in node.get("models") or ():
            found = _model_ref(part)
            if found:
                return found
    return None


class Pack:
    def __init__(self, path):
        self.zip = zipfile.ZipFile(path)
        self.names = set(self.zip.namelist())
        self._textures = {}
        found = collections.defaultdict(set)
        for name in self.names:
            m = re.match(r"^assets/[^/]+/items/(.+)\.json$", name)
            if m and not re.search(r"/\d+$", m.group(1)):       # not the numbered frames of an effect
                # By its own name, and by its folders joined with _: the Furfsky skins sit at
                # furfsky/hyperion for the id furfsky_hyperion_skin.
                for key in {m.group(1).rsplit("/", 1)[-1], m.group(1).replace("/", "_")}:
                    found[key].add(name)
        self.items = {key: next(iter(names)) for key, names in found.items() if len(names) == 1}

    def close(self):
        self.zip.close()

    def definition(self, entry):
        """The pack's item definition for an item: its own, or for a skin the item it is a skin for."""
        for name in (entry, entry[:-len("_skin")] if entry.endswith("_skin") else None):
            if name and name in self.items:
                return self.items[name]
        return None

    @staticmethod
    def _path(ref, kind, ext):
        ns, _, path = ref.rpartition(":")
        return f"assets/{ns or 'minecraft'}/{kind}/{path}{ext}"

    def model(self, ref):
        """The model with its parents folded in: the nearest elements and inventory view, and every
        texture name. A vanilla parent the pack does not carry ends the chain, so a model with layer
        textures and no cuboids counts as flat whatever its parent: Furfsky's Terminator skin sits on
        vanilla's bow model, which reaches item/generated only inside the game."""
        textures, elements, gui, light = {}, None, None, None
        for _ in range(16):
            if not ref:
                break
            ns, _, path = ref.rpartition(":")
            if ns in ("", "minecraft") and path in FLAT:
                return Model(textures, elements, gui, light, elements is None)
            file = self._path(ref, "models", ".json")
            if file not in self.names:
                break
            data = json.loads(self.zip.read(file))
            for key, value in (data.get("textures") or {}).items():
                textures.setdefault(key, value)
            if elements is None and data.get("elements"):
                elements = data["elements"]
            gui = gui or (data.get("display") or {}).get("gui")
            light = light or data.get("gui_light")
            ref = data.get("parent")
        return Model(textures, elements, gui, light, elements is None and "layer0" in textures)

    def texture(self, ref, textures):
        for _ in range(8):                      # "#0" can name "#layer0", which names the file
            if not ref.startswith("#"):
                break
            ref = textures.get(ref[1:], "")
        file = self._path(ref, "textures", ".png") if ref else None
        if file not in self.names:
            return None
        if file not in self._textures:
            img = Image.open(io.BytesIO(self.zip.read(file))).convert("RGBA")
            if img.height > img.width and img.height % img.width == 0:
                img = img.crop((0, 0, img.width, img.width))        # an animation strip: its first frame
            self._textures[file] = img
        return self._textures[file]

    def picture(self, definition):
        ref = _model_ref(json.loads(self.zip.read(definition)).get("model"))
        if not ref:
            return None
        model = self.model(ref)
        if model.flat:
            return self._flat(model)
        return self._cuboids(model) if model.elements else None

    def _flat(self, model):
        layers = [self.texture(f"#layer{i}", model.textures) for i in range(8) if f"layer{i}" in model.textures]
        layers = [layer for layer in layers if layer is not None]
        if not layers:
            return None
        side = max(layer.width for layer in layers)
        out = Image.new("RGBA", (side, side))
        for layer in layers:
            out.alpha_composite(layer.resize((side, side), _mode("Resampling", "NEAREST")))
        return out.resize((SIZE, SIZE), _mode("Resampling", "NEAREST" if side <= SIZE else "BOX"))

    def _cuboids(self, model):
        gui = model.gui or {}
        rx, ry, rz = (math.radians(a) for a in (gui.get("rotation") or (0, 0, 0)))
        # The game turns by x, then y, then z as written (JOML rotationXYZ), so z acts on the model first.
        turn = _matmul(_rot("x", rx), _matmul(_rot("y", ry), _rot("z", rz)))
        move = [max(-80, min(80, t)) / 16 for t in (gui.get("translation") or (0, 0, 0))]
        grow = [max(-4, min(4, s)) for s in (gui.get("scale") or (1, 1, 1))]

        def place(v):       # model units (0-16) to the slot: middle at 0, a block across, y up, z toward you
            p = _apply(turn, [(v[i] / 16 - 0.5) * grow[i] for i in range(3)])
            return [p[i] + move[i] for i in range(3)]

        faces = []
        for element in model.elements:
            low, high = element["from"], element["to"]
            for side, face in (element.get("faces") or {}).items():
                tex = self.texture(face.get("texture", ""), model.textures) if side in ("north", "south", "west", "east", "up", "down") else None
                if tex is None:
                    continue
                corners = _corners(side, low, high)
                if element.get("rotation"):
                    corners = [_spin(c, element["rotation"]) for c in corners]
                points = [place(c) for c in corners]
                normal = _cross([points[1][i] - points[0][i] for i in range(3)], [points[3][i] - points[0][i] for i in range(3)])
                length = math.sqrt(sum(n * n for n in normal))
                if length < 1e-9 or normal[2] <= 0:
                    continue        # edge-on, or facing away: the game culls these too
                normal = [n / length for n in normal]
                shade = 1.0 if element.get("shade") is False else _shade(normal, model.light)
                screen = [(8 + 16 * p[0], 8 - 16 * p[1]) for p in points]
                uv = face.get("uv") or _default_uv(side, low, high)
                faces.append((sum(p[2] for p in points) / 4, screen, uv, int(face.get("rotation") or 0) // 90 % 4, tex, shade))
        if not faces:
            return None
        xs = [x for f in faces for x, _ in f[1]]
        ys = [y for f in faces for _, y in f[1]]
        scale, dx, dy = 1.0, 0.0, 0.0
        if min(xs) < 0 or min(ys) < 0 or max(xs) > 16 or max(ys) > 16:
            # The game lets a big model spill over its slot. A picture would be cut instead, so shrink it.
            scale = 16 / max(max(xs) - min(xs), max(ys) - min(ys))
            dx, dy = 8 - (min(xs) + max(xs)) / 2 * scale, 8 - (min(ys) + max(ys)) / 2 * scale
        unit = SIZE * SUPER / 16
        canvas = Image.new("RGBA", (SIZE * SUPER, SIZE * SUPER))
        for _, screen, uv, turns, tex, shade in sorted(faces, key=lambda f: f[0]):        # far to near
            self._paint(canvas, [((x * scale + dx) * unit, (y * scale + dy) * unit) for x, y in screen], uv, turns, tex, shade)
        return canvas.resize((SIZE, SIZE), _mode("Resampling", "BOX"))

    @staticmethod
    def _paint(canvas, pts, uv, turns, tex, shade):
        """One face: its piece of the texture laid onto the four spots its corners land on."""
        w, h = tex.size
        u1, v1, u2, v2 = uv[0] * w / 16, uv[1] * h / 16, uv[2] * w / 16, uv[3] * h / 16
        corners = [(u1, v1), (u1, v2), (u2, v2), (u2, v1)]
        uvs = [corners[(i + turns) % 4] for i in range(4)]     # "rotation" turns the texture clockwise
        # Only the face's own texels, or the rest of the texture bleeds in along its edges.
        left, top = math.floor(min(u1, u2)), math.floor(min(v1, v2))
        right, bottom = math.ceil(max(u1, u2)), math.ceil(max(v1, v2))
        if right <= left or bottom <= top:
            return
        piece = tex.crop((left, top, right, bottom))
        uvs = [(u - left, v - top) for u, v in uvs]
        x0, y0 = max(0, math.floor(min(p[0] for p in pts))), max(0, math.floor(min(p[1] for p in pts)))
        x1, y1 = min(canvas.width, math.ceil(max(p[0] for p in pts))), min(canvas.height, math.ceil(max(p[1] for p in pts)))
        if x1 <= x0 or y1 <= y0:
            return
        local = [(x - x0, y - y0) for x, y in pts]
        coeffs = _affine(local[0], local[1], local[3], uvs[0], uvs[1], uvs[3])
        if coeffs is None:
            return
        patch = piece.transform((x1 - x0, y1 - y0), _mode("Transform", "AFFINE"), coeffs, resample=_mode("Resampling", "NEAREST"))
        mask = Image.new("L", patch.size)
        ImageDraw.Draw(mask).polygon(local, fill=255)
        alpha = ImageChops.multiply(patch.getchannel("A"), mask)
        if shade < 0.999:
            r, g, b, _ = patch.split()
            patch = Image.merge("RGBA", [band.point(lambda v: int(v * shade)) for band in (r, g, b)] + [alpha])
        else:
            patch.putalpha(alpha)
        canvas.alpha_composite(patch, (x0, y0))


def draw(pack_path, entries, folder):
    """Writes folder/<entry>.png for each entry the pack has a model for, and returns those entries.
    A picture newer than the pack is kept, so only new items cost anything after the first run.
    One odd model is printed and skipped; it never stops the rest."""
    if Image is None:
        return set()
    os.makedirs(folder, exist_ok=True)
    stamp = os.path.getmtime(pack_path)
    done = set()
    pack = Pack(pack_path)
    try:
        for entry in entries:
            definition = pack.definition(entry) if SAFE.match(entry) else None
            if not definition:
                continue
            out = os.path.join(folder, entry + ".png")
            if os.path.exists(out) and os.path.getmtime(out) >= stamp:
                done.add(entry)
                continue
            try:
                picture = pack.picture(definition)
            except (KeyError, ValueError, TypeError, OSError, ZeroDivisionError) as e:
                print("picture failed", entry, repr(e), flush=True)
                continue
            if picture is None:
                continue
            temp = out + ".new"
            picture.save(temp, format="PNG", optimize=True)
            os.replace(temp, out)
            done.add(entry)
    finally:
        pack.close()
    return done
