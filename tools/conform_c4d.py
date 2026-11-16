"""Write asset manifests for vendor Cinema 4D volumes (one .c4d per variation, materials already Octane).

    py -3.13 tools/conform_c4d.py <folder> [...] [--vendor Maxtree] [--type plant] [--dry-run]

Groups <Species>_<NN>.c4d into one asset with variations, uses <file>.jpg as the preview, and writes
<Species>.<manifest> next to them. These assets are C4D-only (no mesh Blender can open).
"""
import argparse
import collections
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from brand import BRAND  # noqa: E402
from conform_converted import pretty, volume_of, VARIANT_RE, IMG  # noqa: E402

SKIP = ("scene", "fieldscene", "growfx")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--vendor", default="Maxtree")
    ap.add_argument("--type", default="plant")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    written = 0
    for top in args.folders:
        for dp, dn, fn in os.walk(top):
            dn[:] = [d for d in dn if d.lower() not in ("tex", "textures") and not d.startswith(".")]
            groups = collections.defaultdict(list)
            for f in fn:
                if not f.lower().endswith(".c4d") or f.startswith("._") or any(s in f.lower() for s in SKIP):
                    continue
                stem = os.path.splitext(f)[0]
                m = VARIANT_RE.match(stem)
                groups[m.group("species") if m else stem].append((int(m.group("n")) if m else 1, f))
            for species, items in sorted(groups.items()):
                items.sort()
                variations = {}
                for n, f in items:
                    stem = os.path.splitext(f)[0]
                    prev = next((stem + e for e in IMG if os.path.exists(os.path.join(dp, stem + e))), None)
                    if not prev:  # thumbnails shipped in a sibling folder (MT_PM_V139_Thumbnails)
                        for sub in sorted(os.listdir(dp)):
                            sp = os.path.join(dp, sub)
                            if os.path.isdir(sp) and ("thumb" in sub.lower() or "preview" in sub.lower()):
                                hit = next((stem + e for e in IMG if os.path.exists(os.path.join(sp, stem + e))), None)
                                if hit:
                                    prev = sub + "/" + hit
                                    break
                    variations["Var%d" % n] = {"c4d": f, "preview": prev}
                first = variations[sorted(variations, key=lambda v: int(v[3:]))[0]]
                vol = volume_of(dp, species)
                manifest = {
                    "format_version": 1, "type": args.type, "name": pretty(species),
                    "category": " ".join(x for x in (args.vendor, vol) if x),
                    "tags": [args.vendor.lower(), "plant", "vegetation", "octane"] + [t.lower() for t in pretty(species).split()],
                    "preview": first["preview"], "real_size_m": None,
                    "files": {"variations": variations, "materials": {}},
                    "source": {"vendor": args.vendor, "format": "c4d (Octane)", "date": datetime.date.today().isoformat()},
                }
                path = os.path.join(dp, species + "." + BRAND["manifest"])
                if args.dry_run:
                    print(path, len(variations))
                else:
                    with open(path + ".tmp", "w", encoding="utf-8") as fh:
                        json.dump(manifest, fh, indent=1)
                    os.replace(path + ".tmp", path)
                written += 1
    print("%d asset manifest(s) %s" % (written, "planned" if args.dry_run else "written"))


if __name__ == "__main__":
    main()
