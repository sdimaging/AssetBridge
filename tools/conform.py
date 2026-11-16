"""Conform assets to the asset format (see the format doc; manifest name from brand.json). Splats first.

    py -3.13 tools/conform.py "D:\\Assets\\MySplats"                        # dry run: print the plan
    py -3.13 tools/conform.py "D:\\Assets\\MySplats" --execute              # write manifests
    py -3.13 tools/conform.py "D:\\Assets\\MySplats" --execute --generate spz,ply   # also fill missing formats
    py -3.13 tools/conform.py <folder> --real-scale "D:\\Assets\\MySplats\\real_scale.json"

Never deletes or overwrites asset files. Existing manifests are rewritten only with --force.
Every action is logged to cache/migrations/conform_<timestamp>.jsonl.
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
import scanners  # noqa: E402
from brand import BRAND  # noqa: E402
MANIFEST = BRAND["manifest"]

FRAMES = {"ply": "3dgs", "spz": "blender", "glb": "gltf", "gltf": "gltf"}


def manifest_for(a):
    folder = a["path"]
    rel = lambda p: os.path.relpath(p, folder)
    files = {"splat": {}}
    for s in a["splats"]:
        files["splat"].setdefault(s["format"], rel(s["path"]))
    proxy = next((m for m in a["meshes"] if m["format"] in ("glb", "gltf")), None) or (a["meshes"][0] if a["meshes"] else None)
    if proxy:
        files["proxy"] = rel(proxy["path"])
    spz = next((s for s in a["splats"] if s["format"] == "spz"), None)
    cat = [c for c in a.get("categories", []) if c != "splat"]
    return {
        "format_version": 1, "type": "splat", "name": a["name"],
        "category": cat[0].title() if cat else None,
        "tags": [t for t in a.get("tags", []) if t not in ("splat", "gaussian splat")],
        "real_size_m": a.get("real_size_m"),
        "preview": rel(a["preview"]) if a.get("preview") else None,
        "files": files,
        "frames": {k: FRAMES[k] for k in files["splat"] if k in FRAMES} | ({"proxy": "gltf"} if proxy and proxy["format"] in ("glb", "gltf") else {}),
        "stats": {"points": a.get("points"), "sh_degree": a.get("sh_degree"),
                  "spz_version": spz.get("version") if spz else None, "extent_m": a.get("extent")},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--force", action="store_true", help="rewrite existing manifests")
    ap.add_argument("--generate", default="", help="comma list of splat formats to create when missing: spz,ply")
    ap.add_argument("--real-scale", default=None)
    args = ap.parse_args()

    lib = {"name": os.path.basename(args.folder.rstrip("\\/")), "kind": "generic", "path": args.folder}
    if args.real_scale:
        lib["real_scale"] = args.real_scale
    assets = [a for kind, a in scanners.scan_generic(lib, {}, lambda n: None) if kind == "asset" and a["type"] == "splat"]
    want = [f.strip() for f in args.generate.split(",") if f.strip()]
    plan = []
    for a in assets:
        mpath = os.path.join(a["path"], MANIFEST)
        have = {s["format"] for s in a["splats"]}
        gen = [f for f in want if f not in have and (have & {"ply", "spz"})]
        exists = os.path.exists(mpath)
        plan.append((a, mpath, gen, exists))

    n_new = sum(1 for _, _, _, e in plan if not e or args.force)
    n_gen = sum(len(g) for _, _, g, _ in plan)
    missing_real = [a["name"] for a, *_ in plan if not a.get("real_size_m")]
    missing_proxy = [a["name"] for a, *_ in plan if not a["meshes"]]
    print("Splat assets: %d   manifests to write: %d   formats to generate: %d" % (len(plan), n_new, n_gen))
    if missing_real:
        print("  no real size (%d): %s%s" % (len(missing_real), ", ".join(missing_real[:8]), " ..." if len(missing_real) > 8 else ""))
    if missing_proxy:
        print("  no proxy (%d): %s%s" % (len(missing_proxy), ", ".join(missing_proxy[:8]), " ..." if len(missing_proxy) > 8 else ""))
    for a, mpath, gen, exists in plan[:5]:
        print("  %-28s %s%s" % (a["name"], ("keep " if exists and not args.force else "write ") + MANIFEST,
                                (" + generate " + "/".join(gen)) if gen else ""))
    if len(plan) > 5:
        print("  ... %d more" % (len(plan) - 5))
    if not args.execute:
        print("\nDry run. Add --execute to apply.")
        return

    sys.path.insert(0, os.path.join(ROOT, "server"))
    import bridge2
    app = bridge2.App.__new__(bridge2.App)
    app.cfg = bridge2.load_config()
    os.makedirs(os.path.join(ROOT, "cache", "migrations"), exist_ok=True)
    log = open(os.path.join(ROOT, "cache", "migrations", "conform_%s.jsonl" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S")), "a", encoding="utf-8")
    for a, mpath, gen, exists in plan:
        for fmt in gen:  # generated next to the asset with canonical names
            src = next(s for s in a["splats"] if s["format"] in ("ply", "spz") and s["format"] != fmt)
            tmp = app.convert_splat(src, fmt)
            dst = os.path.join(a["path"], os.path.splitext(os.path.basename(src["path"]))[0] + "." + fmt)
            if not os.path.exists(dst):
                import shutil
                shutil.copy2(tmp, dst)
                a["splats"].append({"path": dst, "format": fmt, "version": 4 if fmt == "spz" else None,
                                    "points": src.get("points"), "sh_degree": src.get("sh_degree")})
                log.write(json.dumps({"action": "generated", "src": src["path"], "dst": dst}) + "\n")
        if exists and not args.force:
            continue
        with open(mpath + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(manifest_for(a), fh, indent=2)
        os.replace(mpath + ".tmp", mpath)
        log.write(json.dumps({"action": "manifest", "dst": mpath}) + "\n")
    log.close()
    print("Done.")


if __name__ == "__main__":
    main()
