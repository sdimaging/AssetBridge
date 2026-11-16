"""Resolve an asset + options into a concrete payload and ship it to a receiver (C4D or Blender).

Wire protocol (bridge2/1): one JSON object per line over TCP, one JSON reply line.
"""
import json
import os
import re
import socket

PROTO = "bridge2/1"
IMPORTABLE_MESH = {
    "c4d": ["c4d", "fbx", "abc", "usdz", "usd", "usdc", "usda", "glb", "gltf", "obj", "dae", "3ds", "ply", "stl"],
    # Blender: glTF/FBX keep per-face materials; no .c4d/.3ds importer
    "blender": ["fbx", "glb", "gltf", "abc", "usdz", "usd", "usdc", "usda", "obj", "ply", "stl"],  # no .dae in Blender 5
}
RECEIVER_NAMES = {"c4d": "Cinema 4D", "blender": "Blender"}
# lower is better
EXT_PREF = {"color": ["jpg", "jpeg", "png", "tif", "tiff", "tga", "bmp", "webp", "exr"],
            "data": ["jpg", "jpeg", "png", "tif", "tiff", "tga", "bmp", "webp", "exr", "hdr"],
            "disp_exr": ["exr", "tif", "tiff", "png", "jpg", "jpeg"]}


def _res_num(r):
    m = re.match(r"(\d+)K", r or "", re.I)
    return int(m.group(1)) if m else 0


def pick_resolution(available, wanted):
    if not available:
        return None
    if wanted in available:
        return wanted
    w = _res_num(wanted) or 4
    nums = sorted(available, key=_res_num)
    below = [r for r in nums if _res_num(r) <= w]
    return below[-1] if below else nums[0]


def resolve_maps(asset, opts, maps=None):
    """-> ({type: path}, chosen_res)"""
    maps = asset.get("maps", []) if maps is None else maps
    res = pick_resolution(asset.get("resolutions", []), opts.get("resolution", "4K"))
    lod = opts.get("lod") or "LOD0"
    out = {}
    for mtype in sorted({m["type"] for m in maps}):
        cands = [m for m in maps if m["type"] == mtype]
        # res: exact, else nearest available for this map, else untagged
        rs = [m for m in cands if m.get("res") == res] or \
             sorted(cands, key=lambda m: abs(_res_num(m.get("res")) - _res_num(res)))
        best_res = rs[0].get("res") if rs else None
        cands = [m for m in cands if m.get("res") == best_res]
        # lod-specific normals for 3D (Normal_LOD0 etc): prefer matching, then plain
        cands = [m for m in cands if m.get("lod") == lod] or [m for m in cands if not m.get("lod")] or cands
        cands = [m for m in cands if not m.get("variant") or m.get("variant") == "VAR1"] or cands
        pref = EXT_PREF["disp_exr"] if (mtype == "displacement" and opts.get("disp_exr")) else EXT_PREF["data"]
        cands.sort(key=lambda m: pref.index(m["ext"]) if m["ext"] in pref else 99)
        if cands:
            out[mtype] = cands[0]["path"]
    return out, res


def resolve_geometry(asset, opts, target="c4d"):
    order = IMPORTABLE_MESH[target]
    meshes = [m for m in asset.get("meshes", []) if m["format"] in order]
    if not meshes:
        return []
    lod = opts.get("lod") or "LOD0"
    if any(m.get("res") for m in meshes):  # same model shipped per texture resolution (Poly Haven)
        want = pick_resolution(sorted({m["res"] for m in meshes if m.get("res")}, key=_res_num),
                               opts.get("resolution", "4K"))
        meshes = [m for m in meshes if m.get("res") in (want, None)] or meshes
    variations = asset.get("variations") or []
    if variations:
        want = opts.get("variations") or [variations[0]]
        if want == ["all"]:
            want = variations
        chosen = []
        for v in want:
            vm = [m for m in meshes if m.get("variation") == v]
            pick = _pick_mesh(vm, lod, order)
            if pick:
                chosen.append(pick)
        return chosen
    pick = _pick_mesh(meshes, lod, order)
    return [pick] if pick else []


def _pick_mesh(meshes, lod, order):
    if not meshes:
        return None
    exact = [m for m in meshes if m.get("lod") == lod]
    pool = exact or [m for m in meshes if not m.get("lod")] or \
        sorted(meshes, key=lambda m: int(re.sub(r"\D", "", m.get("lod") or "0") or 0))
    pool = sorted(pool, key=lambda m: order.index(m["format"]) if m["format"] in order else 99)
    return {"path": pool[0]["path"], "format": pool[0]["format"], "lod": pool[0].get("lod"),
            "variation": pool[0].get("variation"), "object": pool[0].get("object")}


# ------------------------------------------------------------------ splats
# What each app reads natively, best first. C4D = Octane Gaussian Splat object; the installed
# Octane plugin predates SPZ v4, so v4 is converted. Blender 5.3 = wm.spz_import (SPZ only).
SPLAT_NATIVE = {
    "c4d": [("ply", None), ("spz", 3), ("spz", 2)],
    "blender": [("spz", 4), ("spz", 3), ("spz", 2)],
}
SPLAT_CONVERT_FROM = ["ply", "spz", "sog", "ksplat", "splat", "lcc"]  # splat-transform inputs
PROXY_PREF = ["glb", "gltf", "fbx", "obj", "usd", "usdz", "abc", "ply", "stl"]


def _native_splat(asset, target):
    for fmt, ver in SPLAT_NATIVE[target]:
        for s in asset.get("splats", []):
            if s["format"] == fmt and (ver is None or s.get("version") == ver):
                return s
    return None


def resolve_splat(asset, opts, target, converter=None, scale=1.0):
    """-> (splat dict or None, note). Converts when the app can't read any of the asset's formats.
    A real-world scale is BAKED into a converted copy: scaling the splat object instead doesn't scale each
    gaussian's covariance correctly in Octane (positions shrink, splats don't)."""
    if opts.get("splat_mode", "both") == "proxy":
        return None, None
    s = _native_splat(asset, target)
    if s and abs(scale - 1.0) > 1e-6:
        if converter is None:
            raise RuntimeError("Real-world scale needs the asset server to bake the splat")
        path = converter(s, s["format"], scale=scale)
        return {"path": path, "format": s["format"], "version": s.get("version")}, "real scale x%.3g baked" % scale
    if s:
        return {"path": s["path"], "format": s["format"], "version": s.get("version")}, None
    src = next((x for f in SPLAT_CONVERT_FROM for x in asset.get("splats", []) if x["format"] == f), None)
    if src is None or converter is None:
        fmts = ", ".join(sorted({x["format"].upper() for x in asset.get("splats", [])}))
        raise RuntimeError("No splat format %s can read (has %s)" % (RECEIVER_NAMES[target], fmts or "none"))
    out_fmt = "ply" if target == "c4d" else "spz"
    path = converter(src, out_fmt, scale=scale)
    return {"path": path, "format": out_fmt, "version": 4 if out_fmt == "spz" else None}, \
        "converted %s → %s" % (src["format"].upper() + (str(src.get("version") or "") if src["format"] == "spz" else ""),
                               out_fmt.upper())


def resolve_proxy(asset, opts, target):
    if opts.get("splat_mode", "both") == "splat":
        return None
    order = PROXY_PREF if target == "blender" else [f for f in PROXY_PREF if f != "ply"]
    proxies = sorted([m for m in asset.get("meshes", []) if m["format"] in order], key=lambda m: order.index(m["format"]))
    return {"path": proxies[0]["path"], "format": proxies[0]["format"]} if proxies else None


def build_splat_payload(asset, opts, target, converter=None):
    scale = 1.0  # default: exactly the file's own size, same as loading it by hand
    if opts.get("real_scale", False) and asset.get("real_size_m") and asset.get("extent"):
        scale = asset["real_size_m"] / asset["extent"]
    splats, notes = [], []
    if asset.get("layers"):
        # a layered asset (e.g. a volume layer + a star-field layer) arrives as one splat object per layer, together
        want = [l for l in (opts.get("splat_layers") or asset["layers"]) if l in asset["layers"]] or asset["layers"]
        for layer in want:
            part = dict(asset, splats=[x for x in asset.get("splats", []) if x.get("layer") == layer])
            sp, nt = resolve_splat(part, opts, target, converter, scale)
            if sp:
                splats.append(dict(sp, layer=layer))
            if nt and nt not in notes:
                notes.append(nt)
        splat, note = (splats[0] if splats else None), ("; ".join(notes) or None)
    else:
        splat, note = resolve_splat(asset, opts, target, converter, scale)
    proxy = resolve_proxy(asset, opts, target)
    if splat is None and proxy is None:
        raise RuntimeError("Nothing to send: %s" % ("this asset has no proxy geometry" if opts.get("splat_mode") == "proxy"
                                                   else "no splat or proxy"))
    return {
        "proto": PROTO, "action": "import", "target": target,
        "asset": {k: asset.get(k) for k in ("id", "name", "type", "library", "path", "real_size_m", "extent", "points",
                                              "sh_degree")},
        "options": {"splat_mode": opts.get("splat_mode", "both"), "real_scale": bool(opts.get("real_scale", False)),
                    "skip_points": 8 if opts.get("fast_viewport") else 0},
        # the splat file is already at final size; receivers scale only the proxy mesh by proxy_scale
        "splat": splat, "proxy": proxy, "proxy_scale": round(scale, 6), "scale": 1.0, "note": note,
        "splats": splats,  # layered assets: every layer ({layer, path, format, version}); "splat" = the first
        "maps": {}, "material_sets": {}, "geometry": [], "native": None,
    }


def signature(payload):
    """Identity of an import: same asset + same resolved files/options -> the app may instance it."""
    import hashlib
    key = {k: payload.get(k) for k in ("maps", "material_sets", "geometry", "native", "splat", "proxy", "proxy_scale")}
    if payload.get("splats"):
        key["splats"] = payload["splats"]
    key["id"] = payload["asset"].get("id")
    key["opts"] = {k: v for k, v in payload.get("options", {}).items() if k not in ("instance", "apply_to_selection")}
    return hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


def build_payload(asset, opts, target="c4d", converter=None):
    p = _build_payload(asset, opts, target, converter)
    p["options"]["instance"] = bool(opts.get("instance", True))
    p["signature"] = signature(p)
    return p


def _build_payload(asset, opts, target="c4d", converter=None):
    if asset.get("type") == "splat":
        return build_splat_payload(asset, opts, target, converter)
    maps, res = resolve_maps(asset, opts)
    sets = {part: resolve_maps(asset, opts, ms)[0] for part, ms in (asset.get("material_sets") or {}).items()}
    # a bundled .c4d only helps Cinema 4D; Blender always gets real mesh files
    use_native = target == "c4d" and bool(asset.get("native")) and opts.get("use_native", True)
    geometry = [] if use_native else resolve_geometry(asset, opts, target)
    return {
        "proto": PROTO,
        "action": "import",
        "target": target,
        "asset": {k: asset.get(k) for k in ("id", "name", "type", "library", "path", "workflow", "size",
                                              "source_id", "scale_fixed")},
        "options": {
            "renderer": opts.get("renderer", "octane"),
            "resolution": res,
            "displacement": bool(opts.get("displacement", asset.get("type") in ("surface",))),
            "ao": bool(opts.get("ao", True)),
            "apply_to_selection": bool(opts.get("apply_to_selection", True)),
            "lod": opts.get("lod") or "LOD0",
            "replace_environment": bool(opts.get("replace_environment", True)),
        },
        "maps": maps,
        "material_sets": sets,
        "material_values": asset.get("material_values") or {},
        "material_aliases": asset.get("material_aliases") or {},
        "geometry": geometry,
        "native": asset.get("native") if use_native else None,
    }


def _brand_name():
    try:
        from brand import BRAND
        return BRAND["name"]
    except ImportError:  # imported from the Blender add-on without the server folder on sys.path
        return "asset browser"


def _exchange(host, port, msg, timeout):
    with socket.create_connection((host, port), timeout=3) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps(msg) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.decode("utf-8").strip() or "{}")


def ping(host, port):
    try:
        r = _exchange(host, port, {"proto": PROTO, "action": "ping"}, 3)
        return dict(r, online=True)
    except (OSError, ValueError) as e:
        return {"online": False, "error": str(e)}


def send(host, port, payload, timeout=300):
    app = RECEIVER_NAMES.get(payload.get("target"), "Cinema 4D")
    if payload["asset"].get("type") in ("3d", "plant") and not payload["geometry"] and not payload["native"]:
        return {"ok": False, "error": "No mesh file %s can import for this asset" % app}
    set_paths = [p for ms in payload.get("material_sets", {}).values() for p in ms.values()]
    set_paths += [x["path"] for x in [payload.get("splat"), payload.get("proxy")] + list(payload.get("splats") or []) if x]
    missing = [p for p in list(payload["maps"].values()) + set_paths + [g["path"] for g in payload["geometry"]]
               + ([payload["native"]] if payload["native"] else []) if not os.path.exists(p)]
    if missing:
        return {"ok": False, "error": "Missing files on disk", "missing": missing}
    try:
        return _exchange(host, port, payload, timeout)
    except ConnectionRefusedError:
        return {"ok": False, "error": "%s isn't listening — open %s with the %s %s installed" % (
            app, app, _brand_name(), "add-on" if app == "Blender" else "plugin")}
    except (OSError, ValueError) as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
