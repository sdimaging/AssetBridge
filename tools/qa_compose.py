"""Join QA tiles (index written by qa_contact_sheet.py) into one labelled sheet.

    py -3.13 tools/qa_compose.py <index.txt> <out.png> [--cols 6] [--width 1600]
"""
import math
import sys

from PIL import Image, ImageDraw, ImageFont

idx, out = sys.argv[1], sys.argv[2]
cols = int(sys.argv[sys.argv.index("--cols") + 1]) if "--cols" in sys.argv else 6
width = int(sys.argv[sys.argv.index("--width") + 1]) if "--width" in sys.argv else 1600
rows_ = [(l.rstrip("\n").split("\t") + ["", ""])[:4] for l in open(idx, encoding="utf-8") if l.strip()]
pair = "--pair" in sys.argv  # vendor preview (left) next to our render (right)
cell = width // cols
lab = 20
rows = math.ceil(len(rows_) / cols)
sheet = Image.new("RGB", (cols * cell, rows * (cell + lab)), (20, 20, 21))
d = ImageDraw.Draw(sheet)
try:
    font = ImageFont.truetype("segoeui.ttf", 13)
except OSError:
    font = ImageFont.load_default()
for i, (name, png, err, prev) in enumerate(rows_):
    x, y = (i % cols) * cell, (i // cols) * (cell + lab)
    if pair and prev:
        try:
            pv = Image.open(prev).convert("RGB")
            pv.thumbnail((cell // 2, cell))
            sheet.paste(pv, (x, y + (cell - pv.height) // 2))
        except OSError:
            pass
    if png:
        im = Image.open(png).convert("RGB")
        if pair and prev:
            im = im.resize((cell, cell), Image.LANCZOS).crop((cell // 4, 0, cell // 4 + cell // 2, cell))
            sheet.paste(im, (x + cell // 2, y))
        else:
            sheet.paste(im.resize((cell, cell), Image.LANCZOS), (x, y))
    else:
        d.text((x + 8, y + cell // 2), err[:40], fill=(240, 120, 120), font=font)
    d.text((x + 6, y + cell + 2), name[:34], fill=(220, 220, 224), font=font)
sheet.save(out)
print("sheet", out, sheet.size)
