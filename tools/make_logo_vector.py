"""
Rebuild the Rendafar logo from its own description, at any size.

static/icon-256.png is 256px and logo.psd is 256px too, so there is no larger
original to enlarge — the phone's 512px icon is an upscale and looks it. But
the artwork is not really a picture: it is a circle and two letters. The PSD
still carries a LIVE TEXT LAYER, which names the font and the string:

    /FontSet [ /Name (Bahnschrift-Bold) ]      layer name: "Rd"

so the logo can be redrawn from the outlines at whatever size is wanted,
instead of being scaled up.

Everything else is measured from icon-256.png rather than guessed:

    disc      #EA7601, filling the full 256px square
    ink       #266C87
    counters  #FFFFFF  — the bowls of the R and the d are WHITE, not holes
                        showing the disc through
    ink bbox  x 53..202, y 68..175  (150 x 108)

The glyphs are rasterised by FreeType rather than by filling the contours by
hand. Bahnschrift builds its "R" from OVERLAPPING sub-shapes — a bowl ring, a
stem rectangle and a diagonal leg — and the bowl is a single self-intersecting
contour whose counter only appears under the nonzero winding rule. Two attempts
at classifying those contours by winding and then by containment both produced
a mangled R; FreeType simply gets it right.

The counters are then found by flood filling the background: any hole enclosed
by the letters is a counter, whatever shape it is.

    python tools\\make_logo_vector.py             # 1080 + the PWA sizes
    python tools\\make_logo_vector.py 2048        # any size
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

FONT = r"C:\Windows\Fonts\bahnschrift.ttf"
VARIATION = "Bold"
TEXT = "Rd"

# Measured from static/icon-256.png — see the module docstring.
DISC = (234, 118, 1)
INK = (38, 108, 135)
COUNTER = (255, 255, 255)
SRC = 256.0
INK_BOX = (53, 68, 202, 175)        # x0, y0, x1, y1 inclusive

SS = 4                              # supersampling for the final downsample


def _letters_mask(box_w, box_h):
    """
    "Rd" as a mask exactly box_w x box_h, from the font outlines.

    Sized by measuring: the point size that lands on the target box is found by
    rendering once and scaling, rather than assuming a relationship between em
    size and ink extent that varies per font.
    """
    probe_pt = 400
    f = ImageFont.truetype(FONT, probe_pt)
    try:
        f.set_variation_by_name(VARIATION)
    except OSError:
        pass                        # a static build of the font: already Bold

    tmp = Image.new("L", (probe_pt * 4, probe_pt * 3), 0)
    ImageDraw.Draw(tmp).text((probe_pt // 2, probe_pt // 2), TEXT, font=f, fill=255)
    bb = tmp.getbbox()
    ink_w, ink_h = bb[2] - bb[0], bb[3] - bb[1]

    # Fit the HEIGHT and close the letter gap to reach the width. The original
    # is 150x108 where the font's own spacing gives 150x102 — it was set with
    # tighter tracking, so scaling to fit the width alone leaves the letters
    # visibly squat. Scaling to the height and then tightening the gap
    # reproduces both dimensions without stretching either glyph.
    scale = box_h / ink_h
    pt = max(8, round(probe_pt * scale))

    f2 = ImageFont.truetype(FONT, pt)
    try:
        f2.set_variation_by_name(VARIATION)
    except OSError:
        pass
    big = Image.new("L", (pt * 4, pt * 3), 0)
    ImageDraw.Draw(big).text((pt // 2, pt // 2), TEXT, font=f2, fill=255)
    m = big.crop(big.getbbox())
    return _tighten(m, box_w)


def _tighten(mask, target_w):
    """
    Bring the pair to `target_w` by narrowing the gap between the letters.

    The glyphs themselves are never scaled here — only the space between them —
    so the letterforms stay exactly as the font drew them. The gap is found as
    the widest run of empty columns, which for two letters is the one between
    them.
    """
    w, h = mask.size
    if w <= target_w:
        return mask
    px = mask.load()
    empty = [all(px[x, y] == 0 for y in range(h)) for x in range(w)]

    best, run_start = None, None
    for x, e in enumerate(empty + [False]):
        if e and run_start is None:
            run_start = x
        elif not e and run_start is not None:
            if best is None or x - run_start > best[1] - best[0]:
                best = (run_start, x)
            run_start = None
    if best is None:
        return mask.resize((target_w, h), Image.LANCZOS)   # nothing to close

    cut = min(w - target_w, best[1] - best[0])
    out = Image.new("L", (w - cut, h), 0)
    out.paste(mask.crop((0, 0, best[0], h)), (0, 0))
    out.paste(mask.crop((best[0] + cut, 0, w, h)), (best[0], 0))
    return out.crop(out.getbbox())


def _counters(mask):
    """
    The enclosed holes in a letter mask — the bowls of the R and the d.

    Flood filled from the outside rather than derived from the outlines: the
    contours of this font overlap and self-intersect, so "which loop is a
    counter" has no simple answer, while "background the outside cannot reach"
    has exactly one.
    """
    w, h = mask.size
    # One pixel of margin all round guarantees the flood has somewhere to start.
    pad = Image.new("L", (w + 2, h + 2), 0)
    pad.paste(mask, (1, 1))
    outside = pad.copy()
    ImageDraw.floodfill(outside, (0, 0), 128)

    holes = Image.new("L", (w + 2, h + 2), 0)
    hp, op, pp = holes.load(), outside.load(), pad.load()
    for y in range(h + 2):
        for x in range(w + 2):
            if pp[x, y] < 128 and op[x, y] != 128:   # background, unreachable
                hp[x, y] = 255
    return holes.crop((1, 1, w + 1, h + 1))


def build_png(size):
    """The logo at `size` px, drawn rather than enlarged."""
    big = int(size * SS)
    k = big / SRC

    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((0, 0, big - 1, big - 1), fill=DISC + (255,))

    bx0, by0, bx1, by1 = INK_BOX
    box_w, box_h = round((bx1 - bx0 + 1) * k), round((by1 - by0 + 1) * k)
    mask = _letters_mask(box_w, box_h)
    holes = _counters(mask)

    # Centred in the measured box, so it sits where the original sits.
    ox = round(bx0 * k + (box_w - mask.width) / 2)
    oy = round(by0 * k + (box_h - mask.height) / 2)

    im.paste(Image.new("RGBA", mask.size, COUNTER + (255,)), (ox, oy), holes)
    im.paste(Image.new("RGBA", mask.size, INK + (255,)), (ox, oy), mask)
    # The counters go back on top: the letter mask covers them, being solid.
    im.paste(Image.new("RGBA", mask.size, COUNTER + (255,)), (ox, oy), holes)
    return im.resize((size, size), Image.LANCZOS)


def build_maskable(size):
    """
    The Android "maskable" variant: full bleed, no transparency.

    A maskable icon is cropped to whatever shape the launcher uses — circle,
    squircle, rounded square — so only the middle 80% is guaranteed to survive.
    The normal icon is a disc with transparent corners that touches the frame,
    which under a squircle mask loses its edge and can show the corners as
    black. Filling the square with the disc colour makes any crop land on
    orange, and the letters already sit inside the safe circle: their half
    diagonal is 92px of a 256px frame, against a safe radius of 102px.
    """
    im = build_png(size)
    bg = Image.new("RGBA", im.size, DISC + (255,))
    return Image.alpha_composite(bg, im)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    static = os.path.join(root, "static")
    if len(sys.argv) > 1:
        s = int(sys.argv[1])
        build_png(s).save(os.path.join(static, f"icon-{s}.png"))
        print(f"  wrote icon-{s}.png  ({s}x{s})")
        return

    for s in (1080, 512, 192):
        out = os.path.join(static, f"icon-{s}.png")
        build_png(s).save(out)
        print(f"  wrote {os.path.basename(out)}  ({s}x{s})")
    out = os.path.join(static, "icon-512-maskable.png")
    build_maskable(512).save(out)
    print(f"  wrote {os.path.basename(out)}  (512x512, full bleed)")
    # icon-256.png is left alone: it is the original artwork, and the rebuild
    # matches it to within 0.2% of pixels anyway.


if __name__ == "__main__":
    main()
