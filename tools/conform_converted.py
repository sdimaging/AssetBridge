"""Write asset manifests for converted models (tools/max/run_batch.py results), grouping variations.

    py -3.13 tools/conform_converted.py [--report cache/max/results.jsonl] [--vendor Maxtree] [--dry-run]

Files named <Species>_<NN>.max/.fbx become one asset "<Species>" with variations Var1..VarN, written as
<Species>.<manifest> next to them (manifest name from brand.json). Paths inside are relative.
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
from brand import BRAND  # noqa: E402

VARIANT_RE = re.compile(r"^(?P<species>.+?)_(?P<n>\d{2})(?:_(?P<lod>[HL]))?$")  # Vol 7: _01_H / _01_L = high / low
IMG = (".jpg", ".jpeg", ".png", ".webp")


def pretty(stem):
    s = re.sub(r"^(MT_PM_V\d+_|MT_PM_|MT_)", "", stem)
    s = re.sub(r"\s+", " ", s.replace("_", " ")).strip()
    if re.fullmatch(r"[\d ]+", s):  # volumes that only number their species (MT_PM_V5_01)
        v = re.match(r"^MT_PM_(V\d+)_", stem)
        s = "%sPlant %s" % (v.group(1) + " " if v else "", s)
    return s


def volume_of(path, stem=""):
    """'Vol 39' from the file name (MT_PM_V39_...), else from the folders; free samples share one category."""
    if "sample" in path.lower():
        return "Samples"
    m = re.search(r"MT_PM_V0*(\d+)", stem) or re.search(r"MT_PM_V0*(\d+)_", path) or re.search(r"Vol(?:ume)?\.? ?0*(\d+)", path, re.I)
    return "Vol %d" % int(m.group(1)) if m else None


_IMG_INDEX = {}


def is_sample(path):
    return "sample" in path.lower()


def vendor_root(path):
    """The collection folder (D:\\...\\MAXTREE) a volume lives in."""
    parts = path.replace("/", "\\").split("\\")
    for i, p in enumerate(parts):
        if p.upper() in ("MAXTREE", "CGAXIS SCENES"):
            return "\\".join(parts[:i + 1])
    return None


def find_preview(folder, stem, prefix=False):
    """<stem>.jpg next to the file, else anywhere under the volume (thumbnail/preview folders up to 2 levels up)."""
    for e in IMG:
        if os.path.exists(os.path.join(folder, stem + e)):
            return os.path.join(folder, stem + e)
    up = os.path.dirname(os.path.dirname(folder)) if os.path.basename(os.path.dirname(folder)).lower().startswith("mt_") else os.path.dirname(folder)
    if up not in _IMG_INDEX:
        idx = {}
        for dp, dn, fn in os.walk(up):
            if dp.count(os.sep) - up.count(os.sep) > 3:
                dn[:] = []
            for f in fn:
                b, e = os.path.splitext(f)
                if e.lower() in IMG and not f.startswith("._"):
                    idx.setdefault(b.lower().replace(" ", "_"), os.path.join(dp, f))
        _IMG_INDEX[up] = idx
    key = stem.lower().replace(" ", "_")
    if prefix:
        hits = sorted(k for k in _IMG_INDEX[up] if k.startswith(key))
        return _IMG_INDEX[up][hits[0]] if hits else None
    return _IMG_INDEX[up].get(key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", nargs="+", default=[os.path.join(ROOT, "cache", "max", "results.jsonl")])
    ap.add_argument("--vendor", default="Maxtree")
    ap.add_argument("--type", default="plant")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    latest = {}
    for report in args.report:
        with open(report, "r", encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                if r.get("ok") and os.path.exists(r.get("fbx", "")):
                    latest[os.path.normcase(r["file"])] = r
    groups = collections.defaultdict(list)
    lows = {}
    for r in latest.values():
        stem = os.path.splitext(os.path.basename(r["file"]))[0]
        objs = r.get("objects", [])
        inner = [(o, re.match(r"^%s_(\d{2})$" % re.escape(stem), o["name"])) for o in objs]
        if sum(1 for _, m in inner if m) > 1:  # vendor FBX holding every variation side by side
            for o, m in inner:
                if m:
                    groups[(os.path.dirname(r["file"]), stem)].append((int(m.group(1)), r, o))
            continue
        m = VARIANT_RE.match(stem)
        key = (os.path.dirname(r["file"]), m.group("species") if m else stem)
        if m and m.group("lod") == "L":  # low-poly twin of a variation: an extra LOD, not another variation
            lows[(key, int(m.group("n")))] = r
            continue
        groups[key].append((int(m.group("n")) if m else 1, r, None))

    # free samples repeat species the full volumes already have: keep the full-volume asset only
    full_names = {pretty(sp) for (f, sp) in groups if not is_sample(f)}
    for root in {vendor_root(f) for (f, _) in groups} - {None}:
        for dp, dn, fn in os.walk(root):
            if is_sample(dp):
                dn[:] = []
                continue
            for f in fn:
                if f.endswith("." + BRAND["manifest"]):
                    try:
                        with open(os.path.join(dp, f), encoding="utf-8") as fh:
                            full_names.add(json.load(fh).get("name"))
                    except (OSError, ValueError):
                        pass
    written = skipped = 0
    for (folder, species), items in sorted(groups.items()):
        items.sort(key=lambda x: x[0])
        rel = lambda p: os.path.relpath(p, folder).replace("\\", "/")
        variations, materials, uv, values, aliases = {}, {}, {}, {}, {}
        biggest = 0.0
        for n, r, obj in items:
            objs = [obj] if obj else r.get("objects", [])
            stem = obj["name"] if obj else os.path.splitext(os.path.basename(r["file"]))[0]
            size = max((max(o["size_cm"]) for o in objs), default=0)
            biggest = max(biggest, size)
            prev = find_preview(folder, stem)
            variations["Var%d" % n] = {"fbx": rel(r["fbx"]), "max": rel(r["file"]) if r["file"].lower().endswith(".max") else None,
                                       "object": obj["name"] if obj else None, "preview": rel(prev) if prev else None,
                                       "size_cm": max((o["size_cm"] for o in objs), key=max, default=None)}
            low = lows.get(((folder, species), n))
            if low and os.path.exists(low.get("fbx", "")):
                variations["Var%d" % n]["lods"] = {"LOD1": rel(low["fbx"])}
            if not prev:
                stem_h = re.sub(r"_H$", "", stem)
                prev = find_preview(folder, stem_h)
                variations["Var%d" % n]["preview"] = rel(prev) if prev else None
            aliases.update(r.get("aliases") or {})
            for part, info in r.get("parts", {}).items():
                materials.setdefault(part, {k: rel(v) for k, v in info["maps"].items()})
                if info.get("values"):
                    values.setdefault(part, info["values"])
                uv.setdefault(part, info.get("uv_channels", [1]))
        first = variations[sorted(variations, key=lambda v: int(v[3:]))[0]]
        if not first["preview"]:  # one image per species (MT_PM_Aglaonema_modestum_01.jpg, ..._01_01------03.jpg)
            prev = find_preview(folder, species) or find_preview(folder, species, prefix=True)
            first["preview"] = rel(prev) if prev else None
        if is_sample(folder) and pretty(species) in full_names:
            stale = os.path.join(folder, species + "." + BRAND["manifest"])
            if os.path.exists(stale) and not args.dry_run:
                os.remove(stale)
            skipped += 1
            continue
        vol = volume_of(folder, species)
        manifest = {
            "format_version": 1, "type": args.type, "name": pretty(species),
            "category": " ".join(x for x in (args.vendor, vol) if x),
            "tags": [args.vendor.lower(), "plant", "vegetation"] + [t.lower() for t in pretty(species).split()],
            "preview": first["preview"], "real_size_m": round(biggest / 100.0, 3) if biggest else None,
            "files": {"variations": variations, "materials": materials},
            "material_values": values,
            "material_aliases": aliases,
            "uv_channels": uv,
            "source": {"vendor": args.vendor, "converted_with": "3ds Max", "date": datetime.date.today().isoformat()},
        }
        path = os.path.join(folder, species + "." + BRAND["manifest"])
        if args.dry_run:
            print(path, len(variations), "variations,", len(materials), "parts")
            continue
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=1)
        os.replace(path + ".tmp", path)
        written += 1
    print("%d asset manifest(s) %s, %d sample duplicate(s) skipped" % (written if not args.dry_run else len(groups) - skipped,
                                                                     "planned" if args.dry_run else "written", skipped))


if __name__ == "__main__":
    main()
