"""Render the app icon everywhere it's used, from one SVG.

    py -3.13 tools/make_icons.py                       # brand.json "icon_svg"
    py -3.13 tools/make_icons.py --svg path/to/mark.svg
    py -3.13 tools/make_icons.py --concept P3 --accent violet   # one of the built-in marks (icons/concepts.py)

Writes:
  icons/app.ico               16-256 px, each size rendered natively (desktop app, shortcuts)
  icons/app_<size>.png        16, 32, 64, 128, 256, 1024
  web/icon.svg                browser tab + sidebar mark
  c4d/Bridge2/res/app.png     64 px (C4D Extensions menu)
The SVG should be square (viewBox 0 0 64 64 works well) and include its own background tile.
"""
import argparse
import io
import os
import shutil
import sys

from PIL import Image
import resvg_py

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "icons"))
from brand import BRAND  # noqa: E402

SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def render(svg_text, size):
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg_text, width=size, height=size))
    return Image.open(io.BytesIO(png)).convert("RGBA")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--svg")
    ap.add_argument("--concept")
    ap.add_argument("--accent", default=BRAND["accent"])
    args = ap.parse_args()
    if args.concept:
        import concepts
        src = concepts.svg(args.concept, args.accent)
    else:
        path = args.svg or os.path.join(ROOT, BRAND["icon_svg"])
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
    out = os.path.join(ROOT, "icons")
    with open(os.path.join(ROOT, "web", "icon.svg"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(src)
    imgs = {s: render(src, s) for s in SIZES}
    for s in (16, 32, 64, 128, 256):
        imgs[s].save(os.path.join(out, "app_%d.png" % s))
    render(src, 1024).save(os.path.join(out, "app_1024.png"))
    imgs[256].save(os.path.join(out, "app.ico"), sizes=[(s, s) for s in SIZES],
                   append_images=[imgs[s] for s in SIZES if s != 256])
    res = os.path.join(ROOT, "c4d", "Bridge2", "res")
    os.makedirs(res, exist_ok=True)
    imgs[64].save(os.path.join(res, "app.png"))
    print("Icon set written: icons/app.ico, icons/app_*.png, web/icon.svg, c4d/Bridge2/res/app.png")


if __name__ == "__main__":
    main()
