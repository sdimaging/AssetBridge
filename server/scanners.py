"""Library scanners.

Each scanner turns a folder into normalized asset dicts:

    {id, library, type, name, path, preview, color, mtime, tags, categories, subtitle,
     size, workflow, importable, format,
     maps:    [{type, res, lod, ext, path, workflow, variant}],
     meshes:  [{path, format, lod, variation}],
     native:  path to a .c4d that already carries materials (or None),
     resolutions: [...], lods: [...], variations: [...]}

Types are shared across libraries: 3d, plant, surface, atlas, decal.
"""
import hashlib
import json
import os
import re

import splats as splatio
from brand import BRAND

IMAGE_EXT = {"jpg", "jpeg", "png", "tif", "tiff", "exr", "tga", "bmp", "webp"}
MODEL_EXT = {"fbx", "obj", "c4d", "abc", "glb", "gltf", "usd", "usda", "usdc", "usdz",
             "dae", "3ds", "stl", "ply"}
MODEL_PREF = ["c4d", "fbx", "abc", "usdz", "usd", "usdc", "usda", "glb", "gltf", "obj",
              "dae", "3ds", "ply", "stl"]


SCAN_VERSION = 9  # bump when scanner output changes -> forces a full re-parse


def _color(c):
    """Megascans writes #000000 when it has no real average; treat as unknown."""
    if not isinstance(c, str) or not c.startswith("#") or c.upper() in ("#000000", "#000"):
        return None
    return c.upper()


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _res_key(r):
    m = re.match(r"(\d+)K", r or "", re.I)
    return int(m.group(1)) if m else 0


def _finish(asset):
    res = sorted({m["res"] for m in asset["maps"] + asset["meshes"] if m.get("res")}, key=_res_key)
    lods = sorted({m["lod"] for m in asset["meshes"] if m.get("lod")}, key=lambda l: int(re.sub(r"\D", "", l) or 0))
    vars_ = sorted({m["variation"] for m in asset["meshes"] if m.get("variation")},
                   key=lambda v: (int(re.sub(r"\D", "", v) or 0), v.lower()))  # Var2 < Var10; VarA < VarB
    asset["resolutions"] = res
    asset["lods"] = lods
    asset["variations"] = vars_
    asset["map_types"] = sorted({m["type"] for m in asset["maps"]})
    return asset


# ---------------------------------------------------------------- Megascans

MS_TYPES = {"3d": "3d", "3dplant": "plant", "surface": "surface", "atlas": "atlas"}
MS_TEX_RE = re.compile(r"_(\d+K)_([A-Za-z]+)(?:_LOD(\d+))?\.(jpe?g|png|exr|tiff?)$", re.I)
MS_MESH_RE = re.compile(r"(?:_LOD(\d+)|_(High|Mid|Low))?\.(fbx|obj|abc)$", re.I)
MS_MAP_ALIASES = {"albedo": "albedo", "diffuse": "albedo", "normal": "normal", "roughness": "roughness",
                  "gloss": "gloss", "specular": "specular", "metalness": "metalness", "ao": "ao",
                  "cavity": "cavity", "displacement": "displacement", "bump": "bump",
                  "opacity": "opacity", "translucency": "translucency", "transmission": "translucency",
                  "fuzz": "fuzz", "brush": "brush", "mask": "mask", "basecolor": "albedo"}


def _megascans_ids(root):
    """{Megascans ID: folder} present in a Bridge-layout library: Downloaded|Custom/<type>/<name>_<id>."""
    ids = {}
    for group in ("Downloaded", "Custom"):
        gdir = os.path.join(root, group)
        if not os.path.isdir(gdir):
            continue
        for t in os.scandir(gdir):
            if t.is_dir():
                ids.update({a.name.rsplit("_", 1)[-1].lower(): a.path for a in os.scandir(t.path) if a.is_dir()})
    return ids


def _max_res(folder):
    """Highest texture resolution named in a Megascans folder's files (..._8K_Albedo.jpg -> 8)."""
    best = 0
    try:
        for f in os.listdir(folder):
            m = re.search(r"_(\d+)K_", f, re.I)
            if m:
                best = max(best, int(m.group(1)))
    except OSError:
        pass
    return best


def _flat_kind(adir):
    """Type of a flat (Fab / Quixel-download) asset folder from its <id>.json categories."""
    for f in os.listdir(adir):
        if f.lower().endswith(".json"):
            try:
                with open(os.path.join(adir, f), "r", encoding="utf-8", errors="replace") as fh:
                    cats = json.load(fh).get("categories") or []
            except (OSError, ValueError):
                return None
            return MS_TYPES.get(str(cats[0]).lower()) if cats else None
    return None


def _raw_kind(adir):
    files = os.listdir(adir)
    if any(MS_MESH_RE.search(f) and f.lower().endswith((".fbx", ".obj", ".abc")) for f in files):
        return "3d"
    if any(re.search(r"_opacity\.", f, re.I) for f in files):
        return "atlas"
    return "surface" if any(MS_TEX_RE.search(f) for f in files) else None


def scan_megascans(lib, known, progress):
    root = lib["path"]
    if not any(os.path.isdir(os.path.join(root, g)) for g in ("Downloaded", "Custom")):
        # flat folders (<name>_<id>_<res>/ with <id>.json), e.g. Fab downloads; skip IDs another library already has
        have = {}
        for other in lib.get("dedupe_against") or []:
            have.update(_megascans_ids(other))
        entries = []
        for e in os.scandir(root):
            if not e.is_dir():
                continue
            files = os.listdir(e.path)
            if any(f.lower().endswith(".json") for f in files) or any(MS_TEX_RE.search(f) for f in files):
                entries.append((e, None))
            else:  # a raw pack: one folder per Megascans ID, no json (e.g. unfinished_building_raw/<id>/)
                pack = re.sub(r"[_\s]+raw$", "", e.name, flags=re.I).replace("_", " ").title()
                entries += [(c, pack) for c in os.scandir(e.path) if c.is_dir()]
        for aentry, pack in entries:
            progress(aentry.name)
            msid = next((f[:-5].lower() for f in os.listdir(aentry.path) if f.lower().endswith(".json")), "") or \
                (aentry.name.lower() if pack else "")
            if msid and msid in have and _max_res(have[msid]) >= _max_res(aentry.path):
                continue  # the other library has it at the same or higher resolution
            mtime = aentry.stat().st_mtime
            prev = known.get(aentry.path)
            if prev and prev[1] == mtime:
                yield ("unchanged", prev[0])
                continue
            kind = _flat_kind(aentry.path) if not pack else _raw_kind(aentry.path)
            if not kind:
                continue
            try:
                a = _megascans_asset(lib, aentry.path, aentry.name, kind, mtime)
            except Exception as e:
                print("[megascans] skip %s: %s" % (aentry.path, e))
                continue
            if a and pack:
                a["name"] = "%s %s" % (pack, aentry.name)
                a["categories"] = [kind, pack]
                a["subtitle"] = pack
            if a:
                yield ("asset", a)
        return
    for group in ("Downloaded", "Custom"):
        gdir = os.path.join(root, group)
        if not os.path.isdir(gdir):
            continue
        for tentry in os.scandir(gdir):
            kind = MS_TYPES.get(tentry.name)
            if not kind or not tentry.is_dir():
                continue
            for aentry in os.scandir(tentry.path):
                if not aentry.is_dir():
                    continue
                progress(aentry.name)
                mtime = aentry.stat().st_mtime
                prev = known.get(aentry.path)
                if prev and prev[1] == mtime:
                    yield ("unchanged", prev[0])
                    continue
                try:
                    a = _megascans_asset(lib, aentry.path, aentry.name, kind, mtime)
                except Exception as e:  # one broken asset must not stop the scan
                    print("[megascans] skip %s: %s" % (aentry.path, e))
                    continue
                if a:
                    yield ("asset", a)


def _megascans_asset(lib, adir, dirname, kind, mtime):
    files = os.listdir(adir)
    jfile = next((f for f in files if f.lower().endswith(".json")), None)
    meta = {}
    if jfile:
        with open(os.path.join(adir, jfile), "r", encoding="utf-8", errors="replace") as fh:
            meta = json.load(fh)
    msid = meta.get("id") or dirname.split("_")[-1]
    cats = [c for c in meta.get("categories", []) if isinstance(c, str)]
    if kind == "atlas" and any(c.lower().startswith("decal") for c in cats):
        kind = "decal"

    tags = set(t.lower() for t in meta.get("tags", []) if isinstance(t, str))
    for v in (meta.get("semanticTags") or {}).values():
        for t in (v if isinstance(v, list) else [v]):
            if isinstance(t, str) and len(t) < 40:
                tags.add(t.lower())

    maps, meshes = [], []
    tex_dirs = [adir]
    if kind == "plant":
        tex_dirs = [os.path.join(adir, "Textures", "Atlas")]
    for tdir in tex_dirs:
        if not os.path.isdir(tdir):
            continue
        for f in os.listdir(tdir):
            m = MS_TEX_RE.search(f)
            if not m:
                continue
            mtype = MS_MAP_ALIASES.get(m.group(2).lower())
            if not mtype:
                continue
            maps.append({"type": mtype, "res": m.group(1).upper(), "lod": "LOD%s" % m.group(3) if m.group(3) else None,
                         "ext": m.group(4).lower(), "path": os.path.join(tdir, f)})

    for f in files:
        m = MS_MESH_RE.search(f)
        if m and os.path.isfile(os.path.join(adir, f)):
            lod = "LOD%s" % m.group(1) if m.group(1) else (m.group(2) or "LOD0")
            meshes.append({"path": os.path.join(adir, f), "format": m.group(3).lower(), "lod": lod, "variation": None})
    if kind == "plant":
        for v in files:
            vdir = os.path.join(adir, v)
            if v.lower().startswith("var") and os.path.isdir(vdir):
                for f in os.listdir(vdir):
                    m = MS_MESH_RE.search(f)
                    if m:
                        lod = "LOD%s" % m.group(1) if m.group(1) else (m.group(2) or "LOD0")
                        meshes.append({"path": os.path.join(vdir, f), "format": m.group(3).lower(),
                                       "lod": lod, "variation": v})

    preview = None
    for cand in ["%s_Preview.png" % msid] + [f for f in files if "preview" in f.lower() and f.lower().endswith((".png", ".jpg"))]:
        if cand in files:
            preview = os.path.join(adir, cand)
            break
    if not preview:
        pdir = os.path.join(adir, "previews")
        if os.path.isdir(pdir):
            imgs = sorted(f for f in os.listdir(pdir) if f.lower().endswith((".jpg", ".png")))
            if imgs:
                preview = os.path.join(pdir, imgs[0])
    if not preview:
        alb = sorted((m for m in maps if m["type"] == "albedo" and m["ext"] in ("jpg", "jpeg", "png")),
                     key=lambda m: _res_key(m["res"]))
        if alb:
            preview = alb[0]["path"]

    metad = {m.get("key"): m.get("value") for m in meta.get("meta", []) if isinstance(m, dict)}
    dims = [metad.get(k) for k in ("length", "width", "height")]
    if kind in ("3d", "plant") and all(dims):
        size = " × ".join(str(d) for d in dims)
    else:
        size = metad.get("scanArea") or meta.get("physicalSize") or metad.get("height")

    name = (meta.get("name") or dirname).strip()
    asset = {
        "id": "%s:%s" % (_slug(lib["name"]), dirname),
        "library": lib["name"], "type": kind, "name": name,
        "path": adir, "preview": preview, "color": _color(meta.get("averageColor")), "mtime": mtime,
        "tags": sorted(tags), "categories": cats,
        "subtitle": " / ".join(cats[1:3]) if len(cats) > 1 else kind,
        "size": size, "source_id": msid, "workflow": "metalness" if any(m["type"] == "metalness" for m in maps) else "roughness",
        "importable": bool(maps or meshes), "format": "fbx" if meshes else None,
        "maps": maps, "meshes": meshes, "native": None,
        "scale_fixed": str(metad.get("isScaleFixed", "")).lower() == "true",
    }
    return _finish(asset)


# ---------------------------------------------------------------- Generic / Poliigon

# order matters only for readability; lookup is by exact token
MAP_SYNONYMS = {
    "albedo": ["albedo", "col", "color", "colour", "basecolor", "basecolour", "diffuse", "diff", "dif", "base_color"],
    "normal": ["normal", "nrm", "nor", "norm", "normalgl", "normaldx", "normalmap", "nrm16", "normal16"],
    "roughness": ["roughness", "rough", "rgh"],
    "gloss": ["gloss", "glossiness"],
    "metalness": ["metalness", "metallic", "metal", "met"],
    "ao": ["ao", "occlusion", "ambientocclusion", "occ", "ambient_occlusion"],
    "displacement": ["displacement", "disp", "disp16", "height", "heightmap", "displace"],
    "bump": ["bump", "bump16", "bumpmap"],
    "opacity": ["opacity", "alpha", "alphamasked", "mask", "transparency", "cutout"],
    "translucency": ["translucency", "sss", "transmission", "subsurface", "scattering"],
    "specular": ["specular", "spec", "refl", "reflection"],
    "emission": ["emission", "emissive", "emit", "glow"],
}
SINGLE_LETTER = {"d": "albedo", "n": "normal", "s": "specular", "g": "gloss", "r": "roughness", "h": "displacement"}
TOKEN_TO_MAP = {t: k for k, v in MAP_SYNONYMS.items() for t in v}
TWO_TOKEN = {("base", "color"): "albedo", ("base", "colour"): "albedo", ("ambient", "occlusion"): "ao",
             ("normal", "gl"): "normal", ("normal", "dx"): "normal"}
WORKFLOW_TOK = {"metalness", "specular"}
DCC_TOK = {"cinema4d", "c4d", "octane", "redshift", "corona", "vray", "arnold", "fbx", "obj", "blender",
           "max", "maya", "unity", "unreal", "ue4", "ue5", "fstorm", "scanline", "cycles", "eevee"}
PREVIEW_RE = re.compile(r"^(preview|thumb|thumbnail|render|sphere|cube|flat|cover|promo|poster|hero)\d*$")
MOD_RE = re.compile(r"^(\d+k|lod\d+|var\d+|source|gl|dx|ogl|opengl|directx|16|8bit|16bit|32bit|high|mid|low|hi|lo|raw)$")
SIZE_RE = re.compile(r"^\d+(?:\.\d+)?x\d+(?:\.\d+)?$")  # Textures.com physical size, e.g. 2.5x2.5 (metres)
VENDOR_PREFIX = re.compile(r"^(texturescom|tcom|poliigon|ambientcg|polyhaven)\s+", re.I)
HDRI_EXT = {"hdr", "exr"}
TEX_SUBDIRS = {"textures", "texture", "tex", "maps", "images", "materials", "texturen", "sourceimages"}
SKIP_DIRS = {"thumbs", "previews", "__macosx", ".git", "$recycle.bin", "temp", "cache",
             # splat production leftovers: training datasets, quick previews, backups, review sets
             "quick_splat", "probe", "raw_backup", "_duplicates", "_pruned", "_excluded", "_promo", "_concepts",
             "_versions"}  # AssetBridge's own backups of saved-over splats
SKIP_DIR_RE = re.compile(r"^t\d+_seed\d+", re.I)  # splat training datasets (T1_seed123_...)

GENERIC_NAMES = {"model", "scene", "mesh", "untitled", "untitled1", "base", "material", "defaultmaterial",
                 "object", "export", "low", "high", "lowpoly", "highpoly", "poly", "main", "file", "asset"}
_SPLIT = re.compile(r"[_\-\s]+")  # not ".": keeps 2.5x2.5 intact (extension is already gone)


def _tokens(stem):
    return [t for t in _SPLIT.split(stem) if t]


def classify_image(stem):
    """-> (kind, key, info) where kind in map|preview|other."""
    toks = _tokens(stem)
    low = [t.lower() for t in toks]
    if len(toks) >= 2 and PREVIEW_RE.match(low[-1]):
        return "preview", " ".join(toks[:-1]), {}
    for treat_workflow_as_mod in (True, False):
        i = len(low) - 1
        info = {}
        while i > 0 and (MOD_RE.match(low[i]) or (treat_workflow_as_mod and low[i] in WORKFLOW_TOK)):
            t = low[i]
            if re.match(r"^\d+k$", t):
                info["res"] = t.upper()
            elif t.startswith("lod"):
                info["lod"] = t.upper()
            elif t.startswith("var"):
                info["variant"] = t.upper()
            elif t in ("dx", "directx"):
                info["variant"] = "DX"  # keep the OpenGL normal when both ship
            elif t in WORKFLOW_TOK:
                info["workflow"] = t
            i -= 1
        if i <= 0:
            continue
        mtype = None
        if (low[i - 1], low[i]) in TWO_TOKEN and i - 1 > 0:
            mtype = TWO_TOKEN[(low[i - 1], low[i])]
            i -= 1
        elif low[i] in TOKEN_TO_MAP:
            mtype = TOKEN_TO_MAP[low[i]]
        elif low[i] in SINGLE_LETTER and i == len(low) - 1:
            mtype = SINGLE_LETTER[low[i]]
        if mtype:
            # Textures.com puts the resolution *before* the map: Name_4K_albedo
            j = i
            while j > 1 and re.match(r"^\d+k$", low[j - 1]):
                info.setdefault("res", low[j - 1].upper())
                j -= 1
            return "map", " ".join(toks[:j]), dict(info, type=mtype)
    return "other", " ".join(toks), {}


def model_key(stem):
    toks = _tokens(stem)
    info = {}
    while len(toks) > 1:
        t = toks[-1].lower()
        if t.startswith("lod") and t[3:].isdigit():
            info["lod"] = t.upper()
        elif t in ("proxy", "collision", "collider", "lowpoly"):
            info["proxy"] = True
        elif re.match(r"^\d+k$", t):
            info["res"] = t.upper()
        elif t in DCC_TOK or t in WORKFLOW_TOK or MOD_RE.match(t):
            if t in ("high", "mid", "low"):
                info["lod"] = t.capitalize()
        else:
            break
        toks.pop()
    return " ".join(toks), info


def _norm(key):
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _pretty(key):
    s = VENDOR_PREFIX.sub("", key.replace("_", " "))
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    s = re.sub(r"(?<=[A-Za-z])(?=\d{2,}$)", " ", s)
    return re.sub(r"\s+", " ", s).strip() or key


def scan_generic(lib, known, progress):
    root = lib["path"]
    exclude = {e.lower() for e in lib.get("exclude", [])}
    max_depth = int(lib.get("max_depth", 8))
    lib = dict(lib, _real_scale=_load_real_scale(lib, root))
    for dirpath, files in _walk(root, exclude, max_depth):
        progress(os.path.basename(dirpath))
        for a in _group_dir(lib, root, dirpath, files):
            yield ("asset", a)


def _load_real_scale(lib, root):
    """{normalized asset key: largest real-world side in metres} from real_scale.json (library root or config)."""
    path = lib.get("real_scale") or os.path.join(root, "real_scale.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {_norm(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}


def _walk(root, exclude, max_depth):
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        files, subdirs = [], []
        for e in entries:
            try:
                if e.is_dir():
                    subdirs.append(e)
                elif e.is_file():
                    files.append(e.path)
            except OSError:
                pass
        for s in subdirs:
            n = s.name.lower()
            if n in TEX_SUBDIRS:  # textures folder belongs to the model next to it
                try:
                    files += [x.path for x in os.scandir(s.path) if x.is_file()]
                except OSError:
                    pass
            elif n.startswith(("individual", "seeds", "scatter_parts")):
                continue  # Textures.com ships each scatter piece as its own OBJ: parts of the parent asset
            elif n not in SKIP_DIRS and n not in exclude and not n.startswith(".") and not SKIP_DIR_RE.match(n) \
                    and depth < max_depth:
                stack.append((s.path, depth + 1))
        if files:
            yield d, files


def _sidecars(files):
    """Per-asset manifests: <Asset>.<manifest> (several per folder allowed)."""
    suf = "." + BRAND["manifest"].lower()
    return sorted(p for p in files if os.path.basename(p).lower().endswith(suf) and os.path.basename(p).lower() != suf[1:])


def _group_dir(lib, root, dirpath, files):
    sidecars = _sidecars(files)
    if sidecars:  # a folder with per-asset manifests is fully described by them
        for mp in sidecars:
            a = _model_manifest_asset(lib, root, dirpath, mp)
            if a:
                yield a
        return
    manifest = next((p for p in files if os.path.basename(p).lower() == BRAND["manifest"].lower()), None)
    if manifest:  # the asset-format manifest is authoritative for this folder
        a = _manifest_asset(lib, root, dirpath, manifest)
        if a:
            yield a
            return
    groups = {}
    hdris = {}
    in_hdri_dir = "hdri" in dirpath.lower()

    def g(key):
        nk = _norm(key) or "_"
        if nk not in groups:
            groups[nk] = {"key": key, "maps": [], "models": [], "previews": [], "others": [], "splats": []}
        return groups[nk]

    for p in files:
        base = os.path.basename(p)
        stem, ext = os.path.splitext(base)
        ext = ext[1:].lower()
        sn = splatio.sniff(p) if ext in splatio.SPLAT_ONLY_EXT or ext in splatio.MAYBE_SPLAT_EXT else None
        if sn and sn.get("splat"):
            key, info = model_key(stem)
            g(key)["splats"].append({"path": p, "format": ext, "version": sn.get("version"), "points": sn.get("points"),
                                     "sh_degree": sn.get("sh_degree"), "size": os.path.getsize(p)})
        elif ext in MODEL_EXT:
            key, info = model_key(stem)
            m = {"path": p, "format": ext, "lod": info.get("lod"), "res": info.get("res"), "variation": None}
            if info.get("proxy"):
                m["proxy"] = True
                if sn and sn.get("bounds"):
                    m["bounds"] = sn["bounds"]
            g(key)["models"].append(m)
        elif ext in IMAGE_EXT or ext == "hdr":
            kind, key, info = classify_image(stem)
            if ext == "hdr" or (ext == "exr" and kind != "map" and (in_hdri_dir or "hdri" in stem.lower())):
                hkey, hinfo = model_key(stem)  # strips the _16k resolution suffix
                hkey = re.sub(r"(?i)[ _]?hdri(?: sphere)?$", "", hkey) or hkey
                hdris.setdefault(_norm(hkey), {"key": hkey, "files": []})["files"].append(
                    {"type": "hdri", "res": hinfo.get("res"), "lod": None, "ext": ext, "path": p})
            elif kind == "map":
                g(key)["maps"].append({"type": info["type"], "res": info.get("res"), "lod": info.get("lod"),
                                       "ext": ext, "path": p, "workflow": info.get("workflow"),
                                       "variant": info.get("variant")})
            elif kind == "preview":
                g(key)["previews"].append(p)
            else:
                g(key)["others"].append(p)

    rel = os.path.relpath(dirpath, root)
    for nk, h in hdris.items():
        files_ = h["files"]
        yield _finish({
            "id": "%s:%s" % (_slug(lib["name"]), hashlib.sha1((rel + "|hdri|" + nk).encode()).hexdigest()[:12]),
            "library": lib["name"], "type": "hdri", "name": _pretty(h["key"]), "path": dirpath,
            "preview": sorted(files_, key=lambda m: _res_key(m["res"]) or 99)[0]["path"],
            "color": None, "mtime": max(os.path.getmtime(m["path"]) for m in files_),
            "tags": ["hdri", "environment"] + [t.lower() for t in _tokens(h["key"])],
            "categories": ["hdri", lib["name"].lower()], "subtitle": "HDRI",
            "size": None, "workflow": None, "importable": True, "format": files_[0]["ext"],
            "maps": files_, "meshes": [], "native": None,
        })

    # Asset-per-folder splats: a stray splat file named differently from the rest (e.g. Ship_Figurehead_1.ply
    # next to Figurehead_1.spz/_proxy/_promo) belongs to the folder's main asset, not a second asset.
    splat_groups = [k for k, v in groups.items() if v["splats"]]
    if len(splat_groups) > 1:
        folder = _norm(os.path.basename(dirpath))
        complete = [k for k in splat_groups if groups[k]["models"] or groups[k]["previews"]]
        primary = folder if folder in splat_groups else (complete[0] if len(complete) == 1 else None)
        if primary:
            for k in splat_groups:
                v = groups[k]
                if re.search(r"(^|[ _])v\d+$", v["key"], re.I):  # <name>_v2: a saved version is its own asset
                    continue
                if k != primary and not v["models"] and not v["previews"] and not v["maps"]:
                    groups[primary]["splats"] += v["splats"]
                    del groups[k]

    model_groups = [k for k, v in groups.items() if v["models"]]
    tex_groups = [k for k, v in groups.items() if v["maps"] and not v["models"]]
    # one model + one stray texture set in the same folder -> they belong together
    if len(model_groups) == 1 and len(tex_groups) == 1:
        mg, tg = groups[model_groups[0]], groups.pop(tex_groups[0])
        mg["maps"] += tg["maps"]
        mg["previews"] += tg["previews"]
    # multi-material models (Poly Haven trees: <model>_bark, <model>_trunk_a ...): the sets become
    # per-part material sets of the model instead of separate surfaces
    for mk in model_groups:
        mg = groups[mk]
        mg.setdefault("sets", {})
        # with a single model in the folder every other texture set is one of its parts (plant + pot + soil)
        sole = len(model_groups) == 1
        for tk in [k for k, v in groups.items() if v["maps"] and not v["models"] and k != mk and (sole or k.startswith(mk))]:
            tg = groups.pop(tk)
            part = (tg["key"][len(mg["key"]):].strip(" _-") if tk.startswith(mk) else "") or _pretty(tg["key"])
            mg["sets"][part] = _pick_workflow(tg["maps"])
            mg["previews"] += tg["previews"]
    # orphan previews / loose images with a key matching nothing: attach if folder has a single asset
    real = [k for k, v in groups.items() if v["models"] or v["splats"] or _is_texture_set(v["maps"])]
    if len(real) == 1:
        tgt = groups[real[0]]
        for k, v in list(groups.items()):
            if k != real[0] and not v["models"] and not v["splats"] and not _is_texture_set(v["maps"]):
                tgt["previews"] += v["previews"]
                if not tgt["previews"]:
                    tgt["others"] += v["others"]

    for nk in real:
        v = groups[nk]
        if v["splats"]:
            yield _splat_asset(lib, root, dirpath, rel, nk, v)
            continue
        maps = _pick_workflow(v["maps"])
        sets = v.get("sets") or {}
        models = v["models"]
        native = next((m["path"] for m in models if m["format"] == "c4d"), None)
        meshes = [m for m in models if m["format"] != "c4d"]
        kind = "3d" if models else "surface"
        preview = None
        if v["previews"]:
            preview = sorted(v["previews"], key=lambda p: (len(os.path.basename(p)), p))[0]
        elif kind == "3d" and v["others"] and len(v["others"]) <= 3:
            preview = sorted(v["others"])[0]
        if not preview:
            pool = maps or [m for ms in sets.values() for m in ms]
            alb = sorted((m for m in pool if m["type"] == "albedo" and m["ext"] in ("jpg", "jpeg", "png", "tif", "tiff", "tga", "bmp", "webp")),
                         key=lambda m: _res_key(m["res"]) or 99)
            if alb:
                preview = alb[0]["path"]
            else:  # overlays, height-only sets: any viewable map beats a placeholder
                order = ["opacity", "displacement", "ao", "roughness", "bump", "normal"]  # normal maps read as blue noise
                other = sorted((m for m in pool if m["type"] in order and m["ext"] in ("jpg", "jpeg", "png", "tif", "tiff")),
                               key=lambda m: (order.index(m["type"]), _res_key(m["res"]) or 99))
                if other:
                    preview = other[0]["path"]
        name = _pretty(v["key"])
        if rel != "." and (_norm(v["key"]) in GENERIC_NAMES or _norm(v["key"]).isdigit() or len(_norm(v["key"])) < 3):
            folder = _pretty(os.path.basename(dirpath))
            name = folder if len(real) == 1 else "%s · %s" % (folder, name)
        fmt = None
        if models:
            fmt = sorted({m["format"] for m in models}, key=lambda f: MODEL_PREF.index(f))[0]
        size = next((t.replace("x", " × ") + " m" for t in _tokens(v["key"]) if SIZE_RE.match(t)), None)
        mtime = max((os.path.getmtime(p) for p in ([preview] if preview else []) + [m["path"] for m in models + maps][:5]), default=None)
        all_maps = maps + [m for ms in sets.values() for m in ms]
        asset = {
            "id": "%s:%s" % (_slug(lib["name"]), hashlib.sha1((rel + "|" + nk).encode()).hexdigest()[:12]),
            "library": lib["name"], "type": kind, "name": name,
            "path": dirpath, "preview": preview, "color": None, "mtime": mtime,
            "tags": [t.lower() for t in _tokens(rel) if t != "."] if rel != "." else [],
            "categories": [kind, lib["name"].lower()],
            "subtitle": (fmt.upper() if fmt else "material") + ("" if rel == "." else " / " + rel.replace("\\", "/").split("/")[-1]),
            "size": size, "workflow": "specular" if all_maps and all(m.get("workflow") == "specular" for m in all_maps) else "metalness",
            "importable": True, "format": fmt,
            "maps": maps, "meshes": meshes, "native": native,
            "material_sets": sets,
        }
        a = _finish(asset)
        if sets:  # resolutions / map list across all parts
            a["resolutions"] = sorted({m["res"] for m in all_maps if m.get("res")} | set(a["resolutions"]), key=_res_key)
            a["map_types"] = sorted({m["type"] for m in all_maps})
            a["parts"] = sorted(sets)
        yield a


SPLAT_LABEL = {"ply": "PLY", "splat": "SPLAT", "ksplat": "KSPLAT", "sog": "SOG", "lcc": "LCC", "glb": "GLB",
               "gltf": "glTF", "usd": "USD", "usda": "USD", "usdc": "USD", "usdz": "USDZ"}


def splat_label(sp):
    if sp["format"] == "spz":
        return "SPZ%s" % (sp.get("version") or "")
    return SPLAT_LABEL.get(sp["format"], sp["format"].upper())


def _splat_display_key(dirpath, sps, key):
    """A splat named exactly like its asset folder keeps its full name: the grouping key drops trailing texture
    modifiers, which turns Ship_Debris_16 (16 = a bit depth to texture names) into "Ship Debris"."""
    folder = os.path.basename(dirpath)
    for s in sps:
        stem = os.path.splitext(os.path.basename(s["path"]))[0]
        if _norm(stem) == _norm(folder):
            return stem
    return key


def _splat_asset(lib, root, dirpath, rel, nk, v):
    sps = sorted(v["splats"], key=lambda s: ["ply", "spz", "glb", "gltf", "usdz", "usd", "usdc", "usda",
                                              "sog", "ksplat", "splat", "lcc"].index(s["format"]))
    proxies = [m for m in v["models"] if m.get("proxy")] or [m for m in v["models"]]
    for m in proxies:
        m["proxy"] = True
    preview = sorted(v["previews"], key=lambda p: (len(os.path.basename(p)), p))[0] if v["previews"] else None
    if not preview and v["others"]:
        preview = sorted(v["others"])[0]
    extent = None
    for m in proxies:
        if m.get("bounds"):
            lo, hi = m["bounds"]
            extent = max(h - l for l, h in zip(lo, hi))
            break
    real = lib.get("_real_scale", {}).get(nk)
    pack = rel.replace("\\", "/").split("/")[0] if rel != "." else ""
    pack_name = re.sub(r"^\d+[_ ]*", "", pack).replace("_", " ").strip()
    pts = max((s.get("points") or 0) for s in sps) or None
    labels = []
    for s in sps:
        lb = splat_label(s)
        if lb not in labels:
            labels.append(lb)
    a = {
        "id": "%s:%s" % (_slug(lib["name"]), hashlib.sha1((rel + "|splat|" + nk).encode()).hexdigest()[:12]),
        "library": lib["name"], "type": "splat", "name": _pretty(_splat_display_key(dirpath, sps, v["key"])),
        "path": dirpath, "preview": preview, "color": None,
        "mtime": max(os.path.getmtime(s["path"]) for s in sps),
        "tags": ["splat", "gaussian splat"] + [t.lower() for t in _tokens(pack_name)] + [t.lower() for t in _tokens(v["key"])],
        "categories": ["splat"] + ([pack_name.lower()] if pack_name else []),
        "subtitle": " · ".join(labels) + (" / " + pack_name if pack_name else ""),
        "size": ("%g m" % real) if real else None,
        "real_size_m": real, "extent": extent,
        "splats": sps, "splat_formats": labels, "points": pts,
        "sh_degree": max((s.get("sh_degree") or 0) for s in sps),
        "meshes": [{k: m[k] for k in ("path", "format", "lod", "variation", "proxy") if k in m} for m in proxies],
        "maps": [], "native": None, "importable": True, "format": labels[0] if labels else None,
        "workflow": None,
    }
    return _finish(a)


def _manifest_asset(lib, root, dirpath, mpath):
    try:
        with open(mpath, "r", encoding="utf-8") as fh:
            m = json.load(fh)
    except (OSError, ValueError) as e:
        print("[%s] %s: %s" % (BRAND["manifest"], mpath, e))
        return None
    if m.get("type") != "splat":
        return None  # other types: heuristics until their conform step lands
    absp = lambda p: os.path.join(dirpath, p)
    sps = []
    for fmt, p in (m.get("files", {}).get("splat") or {}).items():
        if os.path.exists(absp(p)):
            sn = splatio.sniff(absp(p)) or {}
            sps.append({"path": absp(p), "format": fmt, "version": sn.get("version"), "points": sn.get("points"),
                        "sh_degree": sn.get("sh_degree"), "size": os.path.getsize(absp(p))})
    if not sps:
        return None
    v = {"key": m.get("name") or os.path.basename(dirpath), "splats": sps, "models": [], "previews": [], "others": []}
    proxy = m.get("files", {}).get("proxy")
    if proxy and os.path.exists(absp(proxy)):
        sn = splatio.sniff(absp(proxy)) or {}
        v["models"].append({"path": absp(proxy), "format": os.path.splitext(proxy)[1][1:].lower(), "lod": None,
                            "variation": None, "proxy": True, "bounds": sn.get("bounds")})
    if m.get("preview") and os.path.exists(absp(m["preview"])):
        v["previews"].append(absp(m["preview"]))
    rel = os.path.relpath(dirpath, root)
    nk = _norm(v["key"])
    rs = dict(lib.get("_real_scale", {}))
    if m.get("real_size_m"):
        rs[nk] = float(m["real_size_m"])
    a = _splat_asset(dict(lib, _real_scale=rs), root, dirpath, rel, nk, v)
    a["name"] = m.get("name") or a["name"]
    if m.get("category"):
        a["categories"] = ["splat", m["category"].lower()]
        a["subtitle"] = " · ".join(a["splat_formats"]) + " / " + m["category"]
    if m.get("tags"):
        a["tags"] = sorted(set(a["tags"]) | {t.lower() for t in m["tags"]})
    if not a.get("extent") and (m.get("stats") or {}).get("extent_m"):
        a["extent"] = m["stats"]["extent_m"]
    a["conformed"] = True
    return a


def _model_manifest_asset(lib, root, dirpath, mpath):
    """3d / plant manifest (tools/conform_converted.py): variations of meshes + named material parts."""
    try:
        with open(mpath, "r", encoding="utf-8") as fh:
            m = json.load(fh)
    except (OSError, ValueError) as e:
        print("[%s] %s: %s" % (BRAND["manifest"], mpath, e))
        return None
    if m.get("type") == "splat":
        return _manifest_asset(lib, root, dirpath, mpath)
    absp = lambda p: os.path.normpath(os.path.join(dirpath, p)) if p else None
    files = m.get("files", {})
    meshes = []
    for var, v in (files.get("variations") or {}).items():
        for fmt in ("fbx", "glb", "gltf", "obj", "abc", "usd", "usdc", "usdz"):
            if v.get(fmt) and os.path.exists(absp(v[fmt])):
                meshes.append({"path": absp(v[fmt]), "format": fmt, "lod": "LOD0" if v.get("lods") else None,
                               "variation": var, "object": v.get("object")})
        for lod, p in (v.get("lods") or {}).items():  # vendor low-poly twins
            if os.path.exists(absp(p)):
                meshes.append({"path": absp(p), "format": os.path.splitext(p)[1][1:].lower(), "lod": lod,
                               "variation": var, "object": v.get("object")})
        if v.get("c4d") and os.path.exists(absp(v["c4d"])):  # vendor C4D scene (own materials): C4D only
            meshes.append({"path": absp(v["c4d"]), "format": "c4d", "lod": None, "variation": var, "object": v.get("object")})
    for fmt, p in (files.get("mesh") or {}).items():
        if os.path.exists(absp(p)):
            meshes.append({"path": absp(p), "format": fmt, "lod": None, "variation": None})
    sets = {}
    for part, maps in (files.get("materials") or {}).items():
        lst = []
        for kind, p in maps.items():
            ap_ = absp(p)
            if ap_ and os.path.exists(ap_):
                lst.append({"type": kind, "res": None, "lod": None, "ext": os.path.splitext(ap_)[1][1:].lower(),
                            "path": ap_, "workflow": None, "variant": None})
        if lst or part in (m.get("material_values") or {}):
            sets[part] = lst
    preview = absp(m.get("preview")) if m.get("preview") and os.path.exists(absp(m["preview"])) else None
    if not meshes and files.get("maps"):  # surface / material manifest: one texture set, no geometry
        maps = []
        for kind, p in files["maps"].items():
            ap_ = absp(p)
            if ap_ and os.path.exists(ap_):
                maps.append({"type": kind, "res": m.get("resolution"), "lod": None,
                             "ext": os.path.splitext(ap_)[1][1:].lower(), "path": ap_})
        if not maps:
            return None
        rel = os.path.relpath(mpath, root)
        a = {
            "id": "%s:%s" % (_slug(lib["name"]), hashlib.sha1(rel.encode()).hexdigest()[:12]),
            "library": lib["name"], "type": m.get("type", "surface"), "name": m.get("name") or os.path.basename(mpath),
            "path": dirpath, "preview": preview or next((x["path"] for x in maps if x["type"] == "albedo"), None),
            "color": None, "mtime": os.path.getmtime(mpath), "tags": sorted({t.lower() for t in m.get("tags", [])}),
            "categories": [m.get("type", "surface")] + ([m["category"].lower()] if m.get("category") else []),
            "subtitle": m.get("category") or "", "size": m.get("physical_size"),
            "workflow": "metalness" if any(x["type"] == "metalness" for x in maps) else "specular",
            "importable": True, "format": None, "maps": maps, "meshes": [], "native": None, "conformed": True,
        }
        return _finish(a)
    if not meshes:
        return None
    real = m.get("real_size_m")
    vars_ = sorted({x["variation"] for x in meshes if x.get("variation")}, key=lambda v: int(re.sub(r"\D", "", v) or 0))
    rel = os.path.relpath(mpath, root)
    all_maps = [x for ms in sets.values() for x in ms]
    a = {
        "id": "%s:%s" % (_slug(lib["name"]), hashlib.sha1(rel.encode()).hexdigest()[:12]),
        "library": lib["name"], "type": m.get("type", "3d"), "name": m.get("name") or os.path.basename(mpath),
        "path": dirpath, "preview": preview, "color": None, "mtime": os.path.getmtime(mpath),
        "tags": sorted({t.lower() for t in m.get("tags", [])}),
        "categories": [m.get("type", "3d")] + ([m["category"].lower()] if m.get("category") else []),
        "subtitle": "%s%s" % ("%d variations · " % len(vars_) if len(vars_) > 1 else "", m.get("category") or ""),
        "size": ("%g m" % real) if real else None, "real_size_m": real,
        "workflow": "specular" if any(x["type"] == "specular" for x in all_maps) else "metalness",
        "importable": True, "format": next((f for f in ("fbx", "glb", "c4d") if any(x["format"] == f for x in meshes)), meshes[0]["format"]),
        "maps": [], "meshes": meshes, "native": None,
        "material_sets": sets, "uv_channels": m.get("uv_channels") or {}, "conformed": True,
        "material_values": m.get("material_values") or {},
        "material_aliases": m.get("material_aliases") or {},
    }
    a = _finish(a)
    a["resolutions"] = []
    a["map_types"] = sorted({x["type"] for x in all_maps})
    a["parts"] = sorted(sets)
    return a


def scan_manifests(lib, known, progress):
    """Library kind "manifest": only folders with asset manifests are indexed (vendor dumps full of loose
    textures, previews and source scenes stay out of the grid)."""
    root = lib["path"]
    exclude = {e.lower() for e in lib.get("exclude", [])}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d.lower() not in exclude and not d.startswith(".")]
        progress(os.path.basename(dirpath))
        full = [os.path.join(dirpath, f) for f in files]
        if not _sidecars(full) and not any(f.lower() == BRAND["manifest"].lower() for f in files):
            continue
        for a in _group_dir(lib, root, dirpath, full):
            yield ("asset", a)


# ---------------------------------------------------------------- finished splat packs

PACK_SPLAT_EXT = ("ply", "spz", "splat", "ksplat", "sog")
PACK_SKIP_DIR = re.compile(r"^(quick_splat|probe|images?_?\d*|sparse|colmap|input|_.*|x_.*|t\d+_seed.*)$", re.I)


def _num_key(stem):
    """'Crystal_7' -> ('crystal', 7), '7' -> ('', 7), 'Tree_Stump_0007' -> ('treestump', 7)."""
    m = re.match(r"^(.*?)[_\- ]*0*(\d+)$", stem)
    return (_norm(m.group(1)), int(m.group(2))) if m else None


def _pack_previews(stem, images, prefer):
    """Images that belong to a splat stem, best first: <stem>.<ext>, then <stem>_<suffix> (ordered by `prefer`),
    then a numbered match (Crystal_7 <- 7.png, Tree_Stump_07 <- Tree_Stump_0007.png)."""
    exact, suffixed, numbered = [], [], []
    nk = _num_key(stem)
    for img in images:
        ist = os.path.splitext(os.path.basename(img))[0]
        if ist.lower() == stem.lower():
            exact.append(img)
        elif ist.lower().startswith(stem.lower() + "_"):
            suffixed.append((ist[len(stem) + 1:].lower(), img))
        elif nk:
            ik = _num_key(ist)
            if ik and ik[1] == nk[1] and ik[0] in ("", nk[0]):
                numbered.append(img)
    rank = lambda suf: (prefer.index(suf) if suf in prefer else len(prefer), suf)
    return exact + [img for _, img in sorted(suffixed, key=lambda t: rank(t[0]))] + sorted(numbered)


def scan_splat_pack(lib, known, progress):
    """Library kind "splat_pack": a finished splat pack, flat or one folder per asset. Only splat files at most
    `depth` folders below the root are assets, so training datasets (quick_splat/, probe/, T1_seed.../) stay out.
    Options:
      layers          suffixes that make one asset of several splats: ["Volume", "Points"] groups
                      <stem>_Volume.ply + <stem>_Points.ply into <stem>, imported as two splats together
      preview_prefer  image suffixes in order, e.g. ["thumb_both", "thumb_both_alt"] for <stem>_thumb_both.png
      depth           folder levels to look in (default 1: the root and one folder per asset)
      tags            extra tags for every asset in the pack
      strip           regex removed from display names, e.g. "_FINAL$"
    """
    root = lib["path"]
    depth = int(lib.get("depth", 1))
    layers = list(lib.get("layers") or [])
    prefer = [p.lower() for p in lib.get("preview_prefer") or []]
    pack = lib.get("pack") or lib["name"]
    for dirpath, dirs, files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        level = 0 if rel == "." else rel.count(os.sep) + 1
        dirs[:] = sorted(d for d in dirs if level < depth and not PACK_SKIP_DIR.match(d))
        progress(os.path.basename(dirpath))
        images = [os.path.join(dirpath, f) for f in files if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
        groups = {}
        for f in sorted(files):
            stem, ext = os.path.splitext(f)
            ext = ext[1:].lower()
            if ext not in PACK_SPLAT_EXT:
                continue
            path = os.path.join(dirpath, f)
            sn = splatio.sniff(path) or {}
            if not sn.get("splat"):
                continue  # a mesh PLY (proxy, scan) is not a splat
            layer = next((L for L in layers if stem.lower().endswith("_" + L.lower())), None)
            key = stem[:-len(layer) - 1] if layer else stem
            groups.setdefault(key, []).append({"path": path, "format": ext, "version": sn.get("version"),
                                               "points": sn.get("points"), "sh_degree": sn.get("sh_degree"),
                                               "size": os.path.getsize(path), "layer": layer})
        for key, sps in groups.items():
            previews = _pack_previews(key, images, prefer)
            v = {"key": key, "splats": sps, "models": [], "previews": previews[:1], "others": []}
            a = _splat_asset(lib, root, dirpath, os.path.join(rel, key), _norm(key), v)
            name = re.sub(lib.get("strip") or r"$^", "", key)
            a["name"] = " ".join(w[:1].upper() + w[1:] if w.islower() else w for w in name.replace("_", " ").split()) or key
            a["preview"] = previews[0] if previews else None
            a["previews"] = previews
            a["tags"] = sorted(set(a["tags"]) | {t.lower() for t in lib.get("tags") or []} | {pack.lower()})
            a["categories"] = ["splat", pack.lower()]
            have = [L for L in layers if any(s["layer"] == L for s in sps)]
            if have:
                a["layers"] = have
                a["points"] = sum(max((s.get("points") or 0) for s in sps if s["layer"] == L) for L in have) or None
            a["subtitle"] = " · ".join(a["splat_formats"]) + (" · " + " + ".join(have) if have else "") + " / " + pack
            yield ("asset", a)


def _is_texture_set(maps):
    types = {m["type"] for m in maps}
    return "albedo" in types or len(types) >= 2


def _pick_workflow(maps):
    wfs = {m.get("workflow") for m in maps if m.get("workflow")}
    if len(wfs) > 1:  # Poliigon ships both — keep metalness (+ untagged maps)
        maps = [m for m in maps if m.get("workflow") in (None, "metalness")]
    return maps


SCANNERS = {"megascans": scan_megascans, "generic": scan_generic, "poliigon": scan_generic, "manifest": scan_manifests,
            "splat_pack": scan_splat_pack}
