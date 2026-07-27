"""
Generates static/rendafar.ico — the app icon.

The icon is the tile map: a grid of rendered tiles on deep slate with one still
in progress, in amber. Same idea as the UI's hero element, so the icon is the
product rather than a decoration bolted onto it.

Drawn per size rather than downscaled from one big bitmap: at 16 px a scaled
grid turns to mush, so the small sizes use a coarser grid that survives.

    python make_icon.py
"""

import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "static", "rendafar.ico")

BG      = (11, 15, 20, 255)       # --bg      deep slate
PENDING = (36, 48, 64, 255)       # --rock    not yet rendered
DONE    = (159, 212, 232, 255)    # --glacier rendered
ACTIVE  = (255, 177, 59, 255)     # --amber   rendering right now

# size -> grid dimension. Small sizes get fewer, chunkier cells so the shape
# still reads at 16 px in a taskbar.
PLANS = {16: 3, 32: 4, 48: 5, 64: 5, 128: 6, 256: 6}

# How much of the grid is finished. The active tile always sits at the frontier
# — the next one to be rendered — so the icon shows a render in progress rather
# than a decorative pattern.
DONE_FRACTION = 0.58


def draw(size):
    cells = PLANS[size]
    total = cells * cells
    n_done = max(1, int(total * DONE_FRACTION))
    done   = set(range(n_done))
    active = n_done                      # the frontier tile

    img = Image.new("RGBA", (size, size), BG)
    d   = ImageDraw.Draw(img)

    pad  = max(1, round(size * 0.09))
    gap  = max(1, round(size * 0.028))
    span = size - pad * 2
    cell = (span - gap * (cells - 1)) / cells

    for i in range(total):
        r, c = divmod(i, cells)
        x0 = pad + c * (cell + gap)
        y0 = pad + r * (cell + gap)
        x1, y1 = x0 + cell, y0 + cell

        if i == active:
            fill = ACTIVE
        elif i in done:
            fill = DONE
        else:
            fill = PENDING

        if size >= 48:
            d.rounded_rectangle([x0, y0, x1, y1],
                                radius=max(1, cell * 0.18), fill=fill)
        else:
            d.rectangle([x0, y0, x1, y1], fill=fill)
    return img


STATIC = os.path.dirname(OUT)
LOGO = os.path.join(STATIC, "icon-256.png")


def source_images(sizes):
    """
    The icon at each size.

    Tiphaine's own logo when it is there, scaled down; the drawn tile grid
    otherwise. Her file is only ever read, never written over.
    """
    if os.path.isfile(LOGO):
        with Image.open(LOGO) as im:
            logo = im.convert("RGBA")
        print(f"using {LOGO}")
        return [logo.resize((s, s), Image.LANCZOS) for s in sizes], logo
    print("no logo found — drawing the tile-grid icon")
    return [draw(s) for s in sizes], draw(256)


def main():
    os.makedirs(STATIC, exist_ok=True)
    sizes = sorted(PLANS)
    images, big = source_images(sizes)

    images[-1].save(OUT, format="ICO",
                    sizes=[(s, s) for s in sizes],
                    append_images=images[:-1])
    print(f"wrote {OUT}")

    # PNGs for the Android PWA, so the phone shows the same mark.
    for px in (192, 512):
        p = os.path.join(STATIC, f"icon-{px}.png")
        big.resize((px, px), Image.LANCZOS).save(p)
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
