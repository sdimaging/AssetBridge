"""Write the textures embedded in a binary FBX to disk - only the ones not already present elsewhere.

    py -3.13 tools/fbx_extract_media.py <scene.fbx> [--out <folder>] [--search <folder> ...] [--dry-run]

Each FBX Video node carries RelativeFilename + Content (the file bytes). Textures whose file name already exists
under any --search folder (default: the kit folder above the FBX) are skipped, so nothing is duplicated; the
rest go to <fbx folder>/tex_embedded/. Prints a JSON summary.
"""
import argparse
import json
import mmap
import os
import re
import struct

IMG = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".tga")


def embedded(path):
    """[(file name, offset, length)] for every Video node with content."""
    out = []
    with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        j = mm.find(b"RelativeFilename")
        while j != -1:
            m = re.search(rb"([\x20-\x7e]{4,250}\.(?:png|jpg|jpeg|tif|tiff|exr|tga))", mm[j + 16:j + 316], re.I)
            k = mm.find(b"\x07Content", j, j + 4096)
            if m and k != -1:
                p = k + 8
                if mm[p:p + 1] == b"R":
                    n = struct.unpack("<I", mm[p + 1:p + 5])[0]
                    if n:
                        name = m.group(1).decode("ascii", "replace").replace("\\", "/").split("/")[-1]
                        out.append((name, p + 5, n))
            j = mm.find(b"RelativeFilename", j + 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fbx")
    ap.add_argument("--out")
    ap.add_argument("--search", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    out_dir = a.out or os.path.join(os.path.dirname(a.fbx), "tex_embedded")
    roots = a.search or [os.path.dirname(os.path.dirname(a.fbx))]
    have = set()
    for r in roots:
        for dp, dn, fn in os.walk(r):
            if os.path.normcase(dp).startswith(os.path.normcase(out_dir)):
                continue
            have.update(f.lower() for f in fn if f.lower().endswith(IMG))
    items = embedded(a.fbx)
    seen, wrote, skipped, bytes_ = set(), [], 0, 0
    with open(a.fbx, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        for name, off, n in items:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            if key in have:
                skipped += 1
                continue
            dst = os.path.join(out_dir, name)
            if not a.dry_run:
                os.makedirs(out_dir, exist_ok=True)
                if not (os.path.exists(dst) and os.path.getsize(dst) == n):
                    with open(dst + ".part", "wb") as w:
                        w.write(mm[off:off + n])
                    os.replace(dst + ".part", dst)
            wrote.append(name)
            bytes_ += n
    print(json.dumps({"embedded": len(seen), "already_on_disk": skipped, "written": len(wrote),
                      "mb": round(bytes_ / 1048576, 1), "out": out_dir, "dry_run": a.dry_run}))


if __name__ == "__main__":
    main()
