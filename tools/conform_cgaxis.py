"""Write asset manifests for CGAxis kit models (one <model>_c4d/ folder per model: <model>.c4d + tex/).

    py -3.13 tools/conform_cgaxis.py <kit models folder> [...] [--vendor CGAxis] [--dry-run]

Each model folder becomes one asset: the .c4d is the mesh, tex/ maps are grouped into texture sets by part
(forge_furnace_coal_basecolor_diffuse.png + mat_forge_furnace_coal_bump.png -> part "forge_furnace_coal"),
so the receiver swaps the file's materials (mat_<part>) for Octane / Principled ones built from those maps.
The kit's preview (<model>.jpg next to the folder) is used as the thumbnail. Manifest: <model>.<manifest>.
"""
import argparse
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
from brand import BRAND  # noqa: E402
from scanners import classify_image  # noqa: E402

IMG = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr")
COLOR_TOKENS = {"basecolor", "basecolour", "diffuse", "albedo", "color", "colour"}
SKIP_TYPES = set()  # every classified map is used; ior / unknown maps classify as "other" and are dropped


def part_key(key):
    """'mat forge furnace coal basecolor' -> 'forge_furnace_coal'."""
    toks = key.lower().split()
    if toks and toks[0] == "mat":
        toks = toks[1:]
    while toks and toks[-1] in COLOR_TOKENS:  # basecolor_diffuse: the inner token is a type too ("metal" is a part)
        toks = toks[:-1]
    return "_".join(toks)


def kit_name(models_dir):
    """...\\Medieval_Forge\\MedievalForge3dModels_c4d -> 'Medieval Forge'."""
    parts = re.split(r"[\\/]+", models_dir.rstrip("\\/"))
    low = [x.lower() for x in parts]
    kit = parts[low.index("cgaxis scenes") + 1] if "cgaxis scenes" in low[:-1] else parts[-2]
    kit = re.sub(r"\s*\(.*?\)", "", kit)  # "Abandoned Factory (...)" -> "Abandoned Factory"
    kit = re.sub(r"^gfx-hub?\.co_", "", kit, flags=re.I)  # uploader prefix on some packs
    kit = re.sub(r"^CGAxis\s+", "", kit, flags=re.I)  # "CGAxis PBR (v37) Animals" -> "PBR Animals"
    kit = re.sub(r"(3d(Models|Scene)|PBRMaterials|_8K).*$", "", kit, flags=re.I)  # FuturisticApartment3dModels_c4d
    kit = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", kit)  # CamelCase -> words
    return re.sub(r"[_\s]+", " ", kit).strip()


def pretty(name):
    return re.sub(r"\s+", " ", name.replace("_", " ")).strip().title()


def model_manifest(mdir, kit, vendor):
    name = re.sub(r"_c4d$", "", os.path.basename(mdir), flags=re.I)
    c4d_files = [f for f in os.listdir(mdir) if f.lower().endswith(".c4d") and not f.startswith("._")]
    if not c4d_files:
        return None
    rel = lambda p: os.path.relpath(p, mdir).replace("\\", "/")
    sets = {}
    tdir = os.path.join(mdir, "tex")
    for f in sorted(os.listdir(tdir)) if os.path.isdir(tdir) else []:
        stem, ext = os.path.splitext(f)
        if ext.lower() not in IMG or f.startswith("._"):
            continue
        kind, key, info = classify_image(stem)
        if kind != "map" or info.get("type") in SKIP_TYPES:
            continue
        part = part_key(key)
        if not part:
            continue
        sets.setdefault(part, {}).setdefault(info["type"], rel(os.path.join(tdir, f)))
    prev = next((os.path.join(os.path.dirname(mdir), name + e) for e in (".jpg", ".png")
                 if os.path.exists(os.path.join(os.path.dirname(mdir), name + e))), None)
    return name, {
        "format_version": 1, "type": "3d", "name": pretty(name),
        "category": "%s %s" % (vendor, kit),
        "tags": [vendor.lower(), kit.lower()] + [t.lower() for t in pretty(name).split()],
        "preview": rel(prev) if prev else None, "real_size_m": None,
        "files": {"mesh": {"c4d": rel(os.path.join(mdir, c4d_files[0]))}, "materials": sets},
        "source": {"vendor": vendor, "kit": kit, "format": "c4d + PBR tex", "date": datetime.date.today().isoformat()},
    }


def material_manifest(mdir, kit, vendor):
    """<name>_8K/ PBR folder -> surface manifest (OpenGL normal preferred, ior / duplicate maps dropped)."""
    m = re.match(r"^(?P<name>.+?)_(?P<res>\d+K)$", os.path.basename(mdir), re.I)
    name, res = (m.group("name"), m.group("res").upper()) if m else (os.path.basename(mdir), None)
    rel = lambda p: os.path.relpath(p, mdir).replace("\\", "/")
    maps, prefer = {}, {}
    for f in sorted(os.listdir(mdir)):
        stem, ext = os.path.splitext(f)
        if ext.lower() not in IMG or f.startswith("._"):
            continue
        kind, key, info = classify_image(stem)
        if kind != "map":
            continue
        t = info["type"]
        score = 2 if "opengl" in stem.lower() or "_gl" in stem.lower() else 1
        if t not in maps or score > prefer[t]:
            maps[t], prefer[t] = rel(os.path.join(mdir, f)), score
    if "albedo" not in maps:
        return None
    parent = os.path.dirname(mdir)
    cands = [os.path.join(parent, n) for n in (name + "_1.jpg", name + ".jpg")]
    cands += [os.path.join(parent, d, name + ".jpg") for d in os.listdir(parent)  # !Perview! / previews folders
              if os.path.isdir(os.path.join(parent, d)) and ("perview" in d.lower() or "prev" in d.lower())]
    prev = next((c for c in cands if os.path.exists(c)), None)
    return name, {
        "format_version": 1, "type": "surface", "name": pretty(name), "category": "%s %s Materials" % (vendor, kit),
        "tags": [vendor.lower(), kit.lower(), "material", "pbr"] + [t for t in name.lower().split("_") if not t.isdigit()],
        "preview": rel(prev) if prev else None, "resolution": res,
        "files": {"maps": maps},
        "source": {"vendor": vendor, "kit": kit, "format": "PBR %s" % (res or ""), "date": datetime.date.today().isoformat()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="+", help="kit model folders, e.g. ...\\Medieval_Forge\\MedievalForge3dModels_c4d")
    ap.add_argument("--vendor", default="CGAxis")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    written = 0
    for top in args.folders:
        kit = kit_name(top)
        for d in sorted(os.listdir(top)):
            mdir = os.path.join(top, d)
            if not os.path.isdir(mdir):
                continue
            if d.lower().endswith("_c4d"):
                res = model_manifest(mdir, kit, args.vendor)
            elif re.search(r"_\d+K$", d, re.I) or re.search(r"_\d+_\d+$", d):  # name_8K / name_37_97 PBR folders
                res = material_manifest(mdir, kit, args.vendor)
            else:
                continue
            if not res:
                continue
            name, manifest = res
            path = os.path.join(mdir, name + "." + BRAND["manifest"])
            if args.dry_run:
                f = manifest["files"]
                print(path, {k: sorted(v) for k, v in f["materials"].items()} if "materials" in f else sorted(f["maps"]))
            else:
                with open(path + ".tmp", "w", encoding="utf-8") as fh:
                    json.dump(manifest, fh, indent=1)
                os.replace(path + ".tmp", path)
            written += 1
    print("%d asset manifest(s) %s" % (written, "planned" if args.dry_run else "written"))


if __name__ == "__main__":
    main()
