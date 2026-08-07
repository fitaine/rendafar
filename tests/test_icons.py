"""
The app icons, and that they are drawn rather than enlarged.

Reported live: "the phone app logo is blurry". It was — icon-512.png was an
upscale of a 256px bitmap, and logo.psd is 256px too, so there was no larger
original to fall back on.

The way out was that the artwork is not really a picture: it is a circle and
two letters, and the PSD still carries a LIVE TEXT LAYER naming the font
(Bahnschrift-Bold) and the string ("Rd"). So it can be redrawn from the
outlines at any size. tools/make_logo_vector.py does that; these checks are
that the results are right.

    python tests\test_icons.py
"""

import json
import os
import struct
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from PIL import Image

STATIC = os.path.join(APP, "static")
results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


def png(name):
    return Image.open(os.path.join(STATIC, name)).convert("RGBA")


DISC = (234, 118, 1)
INK = (38, 108, 135)

print("\n=== 1. the sizes exist and are square ===")
for name, size in (("icon-1080.png", 1080), ("icon-512.png", 512),
                   ("icon-256.png", 256), ("icon-192.png", 192),
                   ("icon-512-maskable.png", 512)):
    p = os.path.join(STATIC, name)
    ok = os.path.isfile(p)
    check(f"{name} exists", ok)
    if ok:
        im = png(name)
        check(f"...and is {size}x{size}", im.size == (size, size), str(im.size))

print("\n=== 2. the rebuild matches the original artwork ===")
# Drawn from the font, but it has to land on the SAME logo — same colours, same
# letter position — not merely something similar.
orig = png("icon-256.png")
new = png("icon-1080.png").resize((256, 256), Image.LANCZOS)


def ink_box(im):
    px = im.load()
    xs, ys = [], []
    for y in range(256):
        for x in range(256):
            r, g, b, a = px[x, y]
            if a > 200 and all(abs(c - t) < 60 for c, t in zip((r, g, b), INK)):
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


a, b = ink_box(orig), ink_box(new)
check("the letters land in exactly the same place", a == b,
      f"original {a}  rebuilt {b}")

# Compared on a common background: the transparent corners flatten differently
# otherwise and would swamp the result.
flat = lambda im: Image.alpha_composite(
    Image.new("RGBA", im.size, (0, 0, 0, 255)), im).convert("RGB")
fo, fn = flat(orig).load(), flat(new).load()
differing = [(x, y) for y in range(256) for x in range(256)
             if max(abs(c1 - c2) for c1, c2 in zip(fo[x, y], fn[x, y])) > 120]
check("the two are near enough identical",
      len(differing) < 65,             # 0.1% of the image
      f"{len(differing)} of 65536 pixels")


def on_boundary(x, y):
    c = fo[x, y]
    return any(max(abs(a - b) for a, b in zip(c, fo[x + dx, y + dy])) > 60
               for dx in (-1, 0, 1) for dy in (-1, 0, 1)
               if 0 <= x + dx < 256 and 0 <= y + dy < 256)


# The assertion that carries the weight. Demanding literally zero differing
# pixels is unrealistic for art rasterised twice by different means, but every
# difference must be sub-pixel antialiasing along an edge. Anything structural
# — wrong letter, wrong spacing, a counter filled the wrong colour — would put
# differences in the middle of a flat area, and this would catch it.
strays = [p for p in differing if not on_boundary(*p)]
check("...and every difference is edge antialiasing, nothing structural",
      not strays, f"{len(strays)} pixels differ away from any boundary: "
                  f"{strays[:5]}")

print("\n=== 3. the bowls of the R and d are WHITE ===")
# The counters are painted white, not punched through to the disc. A plain
# glyph render gets this wrong, which is how the first two attempts looked
# obviously incorrect.
px = png("icon-1080.png").load()
white = sum(1 for y in range(0, 1080, 3) for x in range(0, 1080, 3)
            if px[x, y][3] > 200 and all(c > 230 for c in px[x, y][:3]))
check("there is a meaningful amount of white", white > 500, f"{white} sampled")

print("\n=== 4. the maskable icon is full bleed ===")
# Android crops a maskable icon to the launcher's shape, so only the middle 80%
# survives. A disc with transparent corners loses its edge and can show the
# corners as black.
m = png("icon-512-maskable.png")
corners = [m.getpixel(p) for p in ((2, 2), (509, 2), (2, 509), (509, 509))]
check("every corner is opaque", all(c[3] == 255 for c in corners), str(corners))
check("...and is the disc colour",
      all(all(abs(c[i] - DISC[i]) < 8 for i in range(3)) for c in corners),
      "so any crop shape lands on orange rather than on nothing")

plain = png("icon-512.png")
check("the plain icon still has its transparent corners",
      plain.getpixel((2, 2))[3] == 0,
      "the two are for different jobs and must not be the same file")

print("\n=== 5. the manifest points at them ===")
man = json.load(open(os.path.join(STATIC, "manifest.webmanifest"), encoding="utf-8"))
srcs = {i["src"]: i for i in man["icons"]}
check("the maskable entry uses the full-bleed file",
      srcs.get("/static/icon-512-maskable.png", {}).get("purpose") == "maskable",
      "it used to point at the transparent-cornered icon")
check("...and no 'any' entry uses the maskable file",
      all(i["purpose"] != "any" for i in man["icons"]
          if "maskable" in i["src"]))
for want in ("/static/icon-192.png", "/static/icon-512.png"):
    check(f"{want} is still listed", want in srcs)

print("\n=== 6. sharper than what it replaced ===")
# The honest measure of "blurry": how many pixels an ink/disc boundary takes to
# transition. A drawn edge is ~1px; an upscale smears it wider.
def edge_width(im):
    im = im.convert("RGB").resize((512, 512), Image.LANCZOS)
    p = im.load()
    near = lambda c, t: all(abs(x - y) < 40 for x, y in zip(c, t))
    runs, run = [], 0
    for y in range(140, 380, 4):
        for x in range(60, 460):
            c = p[x, y]
            if near(c, INK) or near(c, DISC) or near(c, (255, 255, 255)):
                if run:
                    runs.append(run)
                    run = 0
            else:
                run += 1
    return sum(runs) / len(runs)


w = edge_width(png("icon-512.png"))
check(f"edges are hard, not smeared ({w:.2f} px)", w < 1.35,
      "the upscaled icon this replaced measured 1.66 px")

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
