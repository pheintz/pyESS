"""Load and render EM Skin Editor / NintendoSpy controller skins.

A skin is a folder holding skin.xml plus PNGs. Skins are made with the EM Skin
Editor (https://electromodder.co.uk/skinedit/skinedit); none are bundled here.

This module knows nothing about pyESS. Callers pass a `state` dict keyed by the
SOURCE names the skin itself uses ("a", "z", "stick_x", "trig_l", ...), so the
pad-to-skin mapping stays outside.
"""
import os
import xml.etree.ElementTree as ET

from PIL import Image

MANIFEST = "skin.xml"
# A float source (a trigger) driving a digital <button> needs a threshold, or a
# resting trigger at 0.02 would light the button.
BUTTON_ON = 0.5


class SkinError(Exception):
    pass


def _num(el, attr, default=0.0):
    try:
        return float(el.get(attr, default))
    except (TypeError, ValueError):
        return float(default)


def _rel(root, raw):
    """Resolve an image path from the manifest; skins may use Windows separators."""
    return os.path.join(root, *raw.replace("\\", "/").split("/"))


class Skin:
    """Parsed skin.xml. Geometry is in the skin's own pixel space."""

    def __init__(self, root, el):
        self.root = root
        self.name = el.get("name") or os.path.basename(root)
        self.author = el.get("author", "")
        self.type = el.get("type", "")

        self.backgrounds = []          # (name, image path, fill colour or None)
        for b in el.findall("background"):
            img = b.get("image")
            self.backgrounds.append((b.get("name") or "default",
                                     _rel(root, img) if img else None,
                                     b.get("color") or None))
        if not self.backgrounds:
            raise SkinError(f"{MANIFEST} has no <background>")

        self.buttons, self.sticks, self.analogs, self.ranges, self.details = [], [], [], [], []
        for b in el.findall("button"):
            self.buttons.append({
                "name": b.get("name", ""), "image": _rel(root, b.get("image", "")),
                "x": _num(b, "x"), "y": _num(b, "y"),
                "w": _num(b, "width"), "h": _num(b, "height")})
        for s in el.findall("stick"):
            self.sticks.append({
                "xname": s.get("xname", ""), "yname": s.get("yname", ""),
                "image": _rel(root, s.get("image", "")),
                "x": _num(s, "x"), "y": _num(s, "y"),
                "w": _num(s, "width"), "h": _num(s, "height"),
                "xrange": _num(s, "xrange", 10), "yrange": _num(s, "yrange", 10)})
        for a in el.findall("analog"):
            self.analogs.append({
                "name": a.get("name", ""), "image": _rel(root, a.get("image", "")),
                "x": _num(a, "x"), "y": _num(a, "y"),
                "w": _num(a, "width"), "h": _num(a, "height"),
                "direction": (a.get("direction") or "right").lower(),
                "reverse": (a.get("reverse") or "false").lower() == "true"})
        for r in el.findall("rangebutton"):
            self.ranges.append({
                "name": r.get("name", ""), "image": _rel(root, r.get("image", "")),
                "x": _num(r, "x"), "y": _num(r, "y"),
                "w": _num(r, "width"), "h": _num(r, "height"),
                "from": _num(r, "from", -1.0), "to": _num(r, "to", 1.0)})
        for d in el.findall("detail"):
            self.details.append({
                "image": _rel(root, d.get("image", "")),
                "x": _num(d, "x"), "y": _num(d, "y"),
                "w": _num(d, "width"), "h": _num(d, "height")})

    @classmethod
    def load(cls, folder):
        path = os.path.join(folder, MANIFEST)
        if not os.path.isfile(path):
            raise SkinError(f"no {MANIFEST} in {folder}")
        try:
            el = ET.parse(path).getroot()
        except ET.ParseError as e:
            raise SkinError(f"{MANIFEST} will not parse: {e}") from e
        return cls(folder, el)

    def source_names(self):
        """Every input name the skin refers to, for building a mapping UI."""
        names = [b["name"] for b in self.buttons]
        for s in self.sticks:
            names += [s["xname"], s["yname"]]
        names += [a["name"] for a in self.analogs]
        names += [r["name"] for r in self.ranges]
        return sorted({n for n in names if n})


class Renderer:
    """Composites a skin at a requested pixel size.

    Images are scaled once per size and cached, so a redraw only pastes.
    """

    def __init__(self, skin, background=0):
        self.skin = skin
        self.background = background
        self._raw = {}
        self._scaled = {}
        self._size = None
        self._base = None
        base_img = self._load(skin.backgrounds[self._bg_index()][1])
        self.native = base_img.size if base_img else (640, 480)

    def _bg_index(self):
        n = len(self.skin.backgrounds)
        return max(0, min(n - 1, self.background))

    def _load(self, path):
        if not path:
            return None
        if path not in self._raw:
            try:
                self._raw[path] = Image.open(path).convert("RGBA")
            except Exception:
                self._raw[path] = None
        return self._raw[path]

    def _at(self, path, w, h):
        """Scaled copy of an image, cached per (path, size)."""
        key = (path, int(w), int(h))
        if key not in self._scaled:
            img = self._load(path)
            if img is None or w < 1 or h < 1:
                self._scaled[key] = None
            else:
                self._scaled[key] = img.resize((int(w), int(h)), Image.LANCZOS)
        return self._scaled[key]

    def set_background(self, index):
        if index != self.background:
            self.background = index
            self._size = None          # geometry may differ between variants

    def render(self, state, size, fill="#000000"):
        """Return an RGBA image of the skin at `size` for the given input state."""
        tw, th = max(1, int(size[0])), max(1, int(size[1]))
        nw, nh = self.native
        sx, sy = tw / nw, th / nh

        if self._size != (tw, th):
            self._size = (tw, th)
            self._base = None

        if self._base is None:
            frame = Image.new("RGBA", (tw, th), fill)
            bg = self._at(self.skin.backgrounds[self._bg_index()][1], tw, th)
            if bg is not None:
                frame.alpha_composite(bg)
            for d in self.skin.details:
                self._paste(frame, d, sx, sy)
            self._base = frame
        frame = self._base.copy()

        for b in self.skin.buttons:
            if self._pressed(state.get(b["name"])):
                self._paste(frame, b, sx, sy)

        for r in self.skin.ranges:
            v = state.get(r["name"])
            # bool is an int in Python; a button value must not satisfy a range.
            numeric = isinstance(v, (int, float)) and not isinstance(v, bool)
            if numeric and r["from"] <= v <= r["to"]:
                self._paste(frame, r, sx, sy)

        for a in self.skin.analogs:
            self._paste_analog(frame, a, state.get(a["name"], 0.0), sx, sy)

        for s in self.skin.sticks:
            dx = float(state.get(s["xname"], 0.0) or 0.0) * s["xrange"]
            dy = float(state.get(s["yname"], 0.0) or 0.0) * s["yrange"]
            self._paste(frame, s, sx, sy, dx, -dy)      # screen y grows downward

        return frame

    @staticmethod
    def _pressed(v):
        return v >= BUTTON_ON if isinstance(v, float) else bool(v)

    def _paste(self, frame, spec, sx, sy, dx=0.0, dy=0.0):
        img = self._load(spec["image"])
        if img is None:
            return
        w = spec["w"] or img.width
        h = spec["h"] or img.height
        scaled = self._at(spec["image"], round(w * sx), round(h * sy))
        if scaled is not None:
            frame.alpha_composite(scaled,
                                  (round((spec["x"] + dx) * sx),
                                   round((spec["y"] + dy) * sy)))

    def _paste_analog(self, frame, spec, value, sx, sy):
        """Trigger bar: crop the image to the fraction the axis is pressed."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        v = max(0.0, min(1.0, v))
        if spec["reverse"]:
            v = 1.0 - v
        if v <= 0.0:
            return
        img = self._load(spec["image"])
        if img is None:
            return
        w = round((spec["w"] or img.width) * sx)
        h = round((spec["h"] or img.height) * sy)
        scaled = self._at(spec["image"], w, h)
        if scaled is None:
            return
        d = spec["direction"]
        if d in ("left", "right"):
            cw = max(1, round(w * v))
            box = (w - cw, 0, w, h) if d == "left" else (0, 0, cw, h)
        else:
            ch = max(1, round(h * v))
            box = (0, h - ch, w, h) if d == "up" else (0, 0, w, ch)
        part = scaled.crop(box)
        frame.alpha_composite(part, (round(spec["x"] * sx) + box[0],
                                     round(spec["y"] * sy) + box[1]))
