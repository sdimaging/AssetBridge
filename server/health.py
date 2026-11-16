"""Library health: one pass over the catalog that groups problems into actionable issues.

    report = health.run(catalog, thumb_failures, cache_dir)
    -> {"generated_at", "seconds", "issues": [{code, severity, title, detail, count, ids, action}]}

severity: "error" (something is broken), "warn" (worth fixing), "info" (good to know).
"""
import json
import os
import re
import time

from brand import BRAND
from catalog import blender_ok

JUNK_NAME = re.compile(r"^(\d+|untitled.*|new.*|default.*|object.*|mesh.*|model.*|\d+ ?preview|preview.*|material.*)$", re.I)


def _main_file(d):
    for key in ("splats", "meshes"):
        if d.get(key):
            return d[key][0]["path"]
    if d.get("native"):
        return d["native"]
    alb = [m for m in d.get("maps", []) if m["type"] == "albedo"]
    if alb:
        return alb[0]["path"]
    return d["maps"][0]["path"] if d.get("maps") else None


def _all_files(d):
    out = [m["path"] for m in d.get("maps", [])] + [m["path"] for m in d.get("meshes", [])]
    out += [s["path"] for s in d.get("splats", [])]
    for ms in (d.get("material_sets") or {}).values():
        out += [m["path"] for m in ms]
    if d.get("native"):
        out.append(d["native"])
    if d.get("preview"):
        out.append(d["preview"])
    return out


def _dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def run(catalog, thumb_failures=(), cache_dir=None, progress=lambda n, total: None):
    t0 = time.time()
    with catalog._lock:
        rows = [(r["id"], json.loads(r["data"])) for r in catalog._db.execute("SELECT id, data FROM assets")]
    buckets = {k: [] for k in ("missing", "no_preview", "broken_preview", "dupe", "no_albedo", "splat_no_proxy",
                               "splat_no_scale", "c4d_only", "no_mesh", "junk_name", "not_conformed", "dx_normal_only")}
    by_source, by_sig = {}, {}
    failures = set(thumb_failures)
    for n, (aid, d) in enumerate(rows):
        if n % 200 == 0:
            progress(n, len(rows))
        if d.get("remote"):  # only on Fab until pulled: no files to check by design
            continue
        files = _all_files(d)
        missing = [p for p in files if not os.path.exists(p)]
        if missing:
            buckets["missing"].append(aid)
        if not d.get("preview"):
            buckets["no_preview"].append(aid)
        elif d["preview"] in failures:
            buckets["broken_preview"].append(aid)
        t = d.get("type")
        if t in ("surface", "atlas", "decal") and "albedo" not in (d.get("map_types") or []) and "opacity" not in (d.get("map_types") or []):
            buckets["no_albedo"].append(aid)
        if t == "splat":
            if not d.get("meshes"):
                buckets["splat_no_proxy"].append(aid)
            if not d.get("real_size_m"):
                buckets["splat_no_scale"].append(aid)
            if not d.get("conformed"):
                buckets["not_conformed"].append(aid)
        if t in ("3d", "plant") and not d.get("meshes") and not d.get("native"):
            buckets["no_mesh"].append(aid)
        elif t in ("3d", "plant") and not blender_ok(d):
            buckets["c4d_only"].append(aid)
        if JUNK_NAME.match((d.get("name") or "").strip()) or len((d.get("name") or "").strip()) < 3:
            buckets["junk_name"].append(aid)
        normals = [m for m in d.get("maps", []) if m["type"] == "normal"]
        if normals and all(m.get("variant") == "DX" for m in normals):
            buckets["dx_normal_only"].append(aid)
        # duplicates: Megascans id across folders/libraries, else type + name + main-file size
        if d.get("source_id"):
            by_source.setdefault(d["source_id"], []).append(aid)
        main = _main_file(d)
        if main and main not in missing:
            try:
                sig = (t, re.sub(r"[^a-z0-9]", "", (d.get("name") or "").lower()), os.path.getsize(main))
                by_sig.setdefault(sig, []).append(aid)
            except OSError:
                pass
    dupe_groups = [ids for ids in list(by_source.values()) + list(by_sig.values()) if len(ids) > 1]
    seen = set()
    for g in dupe_groups:
        for i in g:
            if i not in seen:
                seen.add(i)
                buckets["dupe"].append(i)

    defs = [
        ("missing", "error", "Files missing on disk",
         "These assets point at files that were moved, renamed or deleted. Rescan to refresh them; anything still missing has really gone.",
         {"label": "Rescan libraries", "do": "rescan"}),
        ("broken_preview", "error", "Previews that won't load",
         "The preview image exists but couldn't be read (corrupt or an unsupported format).", None),
        ("dupe", "warn", "Duplicates",
         "The same asset appears more than once (same Megascans ID, or same name and file size), across folders or libraries.", None),
        ("no_mesh", "error", "3D assets without a mesh file",
         "No FBX/OBJ/GLB/ABC or .c4d was found next to the textures, so there's nothing to import. Re-download or re-export them.", None),
        ("no_albedo", "info", "Height or mask-only texture sets",
         "No colour map: usually Megascans imperfections and displacement sets, which are greyscale by design. They import as grey.", None),
        ("splat_no_proxy", "warn", "Splats without a proxy",
         "No proxy mesh, so 'Proxy only' and the C4D viewport stand-in aren't available.", None),
        ("splat_no_scale", "warn", "Splats without a real-world size",
         "Add them to the library's real_scale.json to enable 'Real-world scale'.", None),
        ("no_preview", "warn", "No preview image",
         "These show a placeholder in the grid. Auto-thumbnails will render them.", None),
        ("junk_name", "info", "Unhelpful names",
         "Names like '1', 'Model' or '00 Preview' come from loose downloads. Label them, or rename the files.", None),
        ("c4d_only", "info", "Cinema 4D only",
         "These ship only a .c4d scene, so they can't go to Blender.", None),
        ("dx_normal_only", "info", "DirectX normal maps only",
         "Only a DirectX (Y-down) normal map is present; Blender and Octane expect OpenGL, so bumps may look inverted.", None),
        ("not_conformed", "info", "Splats not in %s" % BRAND["format_name"],
         "Run tools/conform.py on the library to write %s manifests." % BRAND["manifest"], None),
    ]
    issues = []
    for code, sev, title, detail, action in defs:
        ids = buckets[code]
        if ids:
            issues.append({"code": code, "severity": sev, "title": title, "detail": detail, "count": len(ids),
                           "ids": ids, "action": action})
    if cache_dir:
        conv = os.path.join(cache_dir, "splat_conv")
        size = _dir_size(conv) if os.path.isdir(conv) else 0
        if size > 1 << 30:
            issues.append({"code": "splat_cache", "severity": "info", "title": "Converted-splat cache is %.1f GB" % (size / 1e9),
                           "detail": "Format conversions and real-scale bakes are cached so repeat sends are instant. Safe to clear; they rebuild on demand.",
                           "count": 0, "ids": [], "action": {"label": "Clear cache", "do": "clear_splat_cache"}})
    progress(len(rows), len(rows))
    return {"generated_at": time.time(), "seconds": round(time.time() - t0, 1), "assets": len(rows), "issues": issues}
