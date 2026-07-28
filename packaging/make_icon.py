"""Generate docs/pyess.ico from the app's own visual language.

The icon is the zone map: dark ink field, the amber ESS ring, the cool movement
ring outside it, and the N64 octagon gate. Same palette as the live map, so the
taskbar icon and the thing on screen read as the same product.

Rendered per size rather than downscaled once - a 16px icon needs thicker strokes
and fewer elements than a 256px one or it turns to mush.

    python packaging/make_icon.py
"""
import math
import os

from PIL import Image, ImageDraw

INK = (27, 31, 39, 255)        # MAP_NEUTRAL  #1b1f27
AMBER = (240, 169, 46, 255)    # MAP_ESS      #f0a92e
TEAL = (168, 230, 212, 255)    # MAP_WALK     #a8e6d4
DEEP = (21, 69, 107, 255)      # MAP_FULLRUN  #15456b

SIZES = (16, 24, 32, 48, 64, 128, 256)


def octagon(cx, cy, r):
    """N64 gate: 8 points, flat edges, rotated so flats face the cardinals."""
    return [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
            for a in range(22, 360, 45)]


def render(px):
    # 4x supersample then downscale - Pillow has no built-in AA for draw primitives
    s = px * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = s / 2
    small = px <= 24                       # drop detail that would smear at tiny sizes

    # rounded dark field, so the icon reads as a tile rather than a bare circle
    pad = s * 0.02
    d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=s * 0.22, fill=INK)

    # outer movement ring (deep) -> the "fast" end of the map
    d.ellipse([c - s * 0.40, c - s * 0.40, c + s * 0.40, c + s * 0.40], fill=DEEP)
    if not small:
        d.ellipse([c - s * 0.33, c - s * 0.33, c + s * 0.33, c + s * 0.33], fill=TEAL)

    # the ESS band - the thing the whole app exists for, so it gets the accent colour
    r_ess = 0.28 if small else 0.25
    d.ellipse([c - s * r_ess, c - s * r_ess, c + s * r_ess, c + s * r_ess], fill=AMBER)

    # dead centre
    r_dz = 0.13 if small else 0.11
    d.ellipse([c - s * r_dz, c - s * r_dz, c + s * r_dz, c + s * r_dz], fill=INK)

    # octagon gate on top - the N64 signature. Too fine to survive 16px, so skip it.
    if not small:
        d.line(octagon(c, c, s * 0.44) + [octagon(c, c, s * 0.44)[0]],
               fill=(255, 255, 255, 90), width=max(1, int(s * 0.018)))

    return img.resize((px, px), Image.LANCZOS)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(os.path.dirname(here), "docs", "pyess.ico")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    frames = [render(p) for p in SIZES]
    # Pillow writes every supplied size into the .ico when sizes= is given
    frames[-1].save(out, format="ICO", sizes=[(p, p) for p in SIZES],
                    append_images=frames[:-1])
    print(f"wrote {out}")
    for p in SIZES:
        frames[SIZES.index(p)].save(
            os.path.join(os.path.dirname(here), "docs", f"_icon_{p}.png"))
    print("wrote preview PNGs")


if __name__ == "__main__":
    main()
