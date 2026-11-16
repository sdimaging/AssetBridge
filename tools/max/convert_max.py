"""Headless 3ds Max converter: .max -> .fbx next to the source + a JSON report per file.

Runs inside 3dsmaxbatch.exe (see tools/max/run_batch.py, which writes the job file and starts Max):
    MAX_JOB=<job.json>  {"files": [...], "report": "<results.jsonl>", "overwrite": false}

For every geometry object it finds which bitmaps each material slot uses by walking references, so it
works even when the original renderer (Corona, V-Ray) isn't installed and the materials load as
Missing_Mtl. Each slot is renamed to its texture-set ("part") name and replaced with a Physical
Material wired to those bitmaps, so the FBX carries clean material names (and textures) for any app.
"""
import json
import os
import re
import time
import traceback

from pymxs import runtime as rt

MAP_TYPES = [  # (type, filename tokens)
    ("albedo", ("albedo", "diffuse", "diff", "dif", "color", "col", "basecolor")),
    ("normal", ("normal", "nrm", "norm", "nor")),
    ("gloss", ("gloss", "glossiness")),
    ("roughness", ("roughness", "rough")),
    ("specular", ("specular", "spec", "reflection", "refl")),
    ("opacity", ("opacity", "alpha", "mask", "cutout")),
    ("translucency", ("translucency", "tarnslucency", "tran", "trans", "sss", "transmission")),
    ("bump", ("bump",)),
    ("displacement", ("displacement", "displace", "dislace", "disp", "height")),
    ("ao", ("ao", "occlusion")),
    ("metalness", ("metalness", "metallic", "metal")),
]
TOKEN_TYPE = {t: k for k, ts in MAP_TYPES for t in ts}


def classify(stem):
    """-> (part, type, side) from names like MT_Grass_04_Leaf_02_albedo_02 or ..._Leaf_02_B_albedo."""
    toks = re.split(r"[_\-\s.]+", stem)
    for i in range(len(toks) - 1, 0, -1):
        t = toks[i].lower()
        if t.startswith("gloss"):  # vendor typos: "glossniess"
            t = "gloss"
        if t in TOKEN_TYPE:
            part_toks = toks[:i]
            side = None
            if part_toks and part_toks[-1].upper() in ("F", "B"):  # two-sided leaves: front / back albedo
                side = part_toks[-1].upper()
                part_toks = part_toks[:-1]
            return "_".join(part_toks), TOKEN_TYPE[t], side
    return None, None, None


def bitmaps_in(node, seen, depth=0):
    """[(filename, mapChannel)] for every Bitmaptexture under node (through missing-plugin wrappers)."""
    return [(f, ch) for f, ch, _, side in bitmap_chains(node, seen, depth) if side == "front"]


def _cls(node):
    try:
        return str(rt.classOf(node))
    except Exception:
        return ""


def _is_texmap(node):
    try:
        return str(rt.superClassOf(node)) == "textureMap"
    except Exception:
        return False


# native 2D maps Max can bake exactly with renderMap (renderer-only and 3D procedural maps are not on the list)
BAKE_KINDS = ("albedo", "albedo_back", "translucency")
BAKEABLE = {"Bitmaptexture", "Color_Correction", "Mix", "Gradient_Ramp", "Gradient", "RGB_Multiply", "RGB_Tint",
            "Output", "CompositeTexturemap"}


def _is_mtl(node):
    try:
        return str(rt.superClassOf(node)) == "material"
    except Exception:
        return False


def bitmap_chains(node, seen, depth=0, top=None, side="front"):
    """[(filename, mapChannel, top)]: top = the highest native map above the bitmap in its texture chain (a Color
    Correction / Mix / Gradient the vendor used to tint or blend it), None when the bitmap feeds the material directly."""
    out = []
    if node is None or depth > 8:
        return out
    try:
        h = rt.getHandleByAnim(node)
    except Exception:
        return out
    key = (h, rt.getHandleByAnim(top) if top is not None else 0)
    if key in seen:
        return out
    seen.add(key)
    cls = _cls(node)
    if _is_texmap(node):
        if cls == "Bitmaptexture":
            ch = 1
            try:
                ch = int(node.coords.mapChannel)
            except Exception:
                pass
            out.append((str(node.filename), ch, top, side))
        elif cls in BAKEABLE:
            top = top or node
        else:
            top = None  # renderer-only / procedural wrapper: start over below it
    elif cls == "Missing_Mtl" or "material" in str(rt.superClassOf(node)).lower():
        top = None
    try:
        n = rt.refs.getNumRefs(node)
    except Exception:
        n = 0
    two_sided = _is_mtl(node) and cls != "Multimaterial"
    mtl_kids = 0
    for i in range(n):
        try:
            r = rt.refs.getReference(node, i + 1)
        except Exception:
            r = None
        if r is not None:
            kid_side = side
            if two_sided and _is_mtl(r):  # two-sided wrappers (VRay2Sided / Corona): front material first, then back
                mtl_kids += 1
                if mtl_kids > 1:
                    kid_side = "back"
            out += bitmap_chains(r, seen, depth + 1, top, kid_side)
    return out


def _chain_desc(node, depth=0, seen=None):
    """Stable text description of a texture chain (classes, parameters, bitmap files, tiling) for the bake hash."""
    seen = seen if seen is not None else set()
    if node is None or depth > 6:
        return ""
    try:
        h = rt.getHandleByAnim(node)
    except Exception:
        return ""
    if h in seen:
        return ""
    seen.add(h)
    parts = [_cls(node)]
    if parts[0] == "Bitmaptexture":
        parts.append(os.path.basename(str(node.filename)).lower())
    try:
        for pn in rt.getPropNames(node):
            try:
                v = rt.getProperty(node, pn)
            except Exception:
                continue
            if v is None or _is_texmap(v) or str(pn).lower() in ("filename", "bitmap"):
                continue
            parts.append("%s=%s" % (pn, v))
    except Exception:
        pass
    try:
        n = rt.refs.getNumRefs(node)
    except Exception:
        n = 0
    for i in range(n):
        try:
            r = rt.refs.getReference(node, i + 1)
        except Exception:
            r = None
        if r is not None and (_is_texmap(r) or _cls(r) in ("UVGenClass", "StandardUVGen", "StandardTextureOutput")):
            parts.append("(" + _chain_desc(r, depth + 1, seen) + ")")
    return "|".join(parts)


def _subtree_maps(node, depth=0, seen=None):
    seen = seen if seen is not None else set()
    out = []
    if node is None or depth > 8:
        return out
    try:
        h = rt.getHandleByAnim(node)
    except Exception:
        return out
    if h in seen:
        return out
    seen.add(h)
    if _is_texmap(node):
        out.append(node)
    try:
        n = rt.refs.getNumRefs(node)
    except Exception:
        n = 0
    for i in range(n):
        try:
            r = rt.refs.getReference(node, i + 1)
        except Exception:
            r = None
        if r is not None:
            out += _subtree_maps(r, depth + 1, seen)
    return out


_DIMS = {}


def _mean_level(bm, close=False):
    """Average brightness (0-1) of a 9x9 grid of pixels."""
    try:
        w, h = int(bm.width), int(bm.height)
        tot, n = 0.0, 0
        for j in range(9):
            for i in range(9):
                px = rt.getPixels(bm, rt.Point2(int((i + 0.5) * w / 9), int((j + 0.5) * h / 9)), 1)
                if px:
                    c = px[0]
                    tot += (float(c.r) + float(c.g) + float(c.b)) / (3 * 255.0)
                    n += 1
        return tot / n if n else 0.0
    finally:
        if close:
            rt.close(bm)


def bake_chain(top, base_path, src_dir):
    """Bake a native texture chain (tint / blend over a bitmap) to <base>_baked_<hash>.jpg next to the bitmap, with
    Max's own evaluation, so every renderer sees the vendor's colours. None when the chain can't be baked exactly."""
    import hashlib
    maps = _subtree_maps(top)
    if any(_cls(m) not in BAKEABLE for m in maps):
        return None
    for m in maps:  # point bitmaps at the files on this machine
        if _cls(m) == "Bitmaptexture":
            p = resolve(str(m.filename), src_dir)
            if p is None:
                return None
            m.filename = p
    key = hashlib.sha1(_chain_desc(top).encode("utf-8")).hexdigest()[:8]
    out = os.path.splitext(base_path)[0] + "_baked_" + key + ".jpg"
    if os.path.exists(out):
        return out
    if base_path not in _DIMS:
        bm = rt.openBitMap(base_path)
        _DIMS[base_path] = (int(bm.width), int(bm.height))
        rt.close(bm)
    w, h = _DIMS[base_path]
    tmp = out[:-4] + ".part.jpg"
    try:
        r = rt.renderMap(top, size=rt.Point2(w, h), filter=False)
        if _mean_level(r) < 0.02 and _mean_level(rt.openBitMap(base_path), close=True) > 0.05:
            rt.close(r)  # an unconnected chain renders black: never let it replace a real texture
            print("bake rejected (black) %s" % out)
            return None
        r.filename = tmp
        rt.save(r)
        rt.close(r)
        os.replace(tmp, out)
    except Exception as e:
        print("bake failed %s: %s" % (out, e))
        return None
    return out


_MAP_DIRS = {}


def map_dirs(src_dir):
    """Folders that may hold a scene's textures: its own, child/sibling/ancestor *map* folders, ancestors themselves."""
    if src_dir in _MAP_DIRS:
        return _MAP_DIRS[src_dir]
    cands = [src_dir]
    try:  # maps in a child folder (free samples: <dir>/<Vol>_Maps)
        cands += [os.path.join(src_dir, d) for d in os.listdir(src_dir)
                  if "map" in d.lower() and os.path.isdir(os.path.join(src_dir, d))]
    except OSError:
        pass
    up = src_dir
    for _ in range(4):  # Maxtree keeps maps in <Volume>/<Vol>_Maps/<Vol>_Maps, a few levels up
        up = os.path.dirname(up)
        cands.append(up)  # some volumes unpack their maps straight into the volume folder
        try:
            for d in os.listdir(up):
                p = os.path.join(up, d)
                if os.path.isdir(p) and "map" in d.lower():
                    cands.append(p)
                    for d2 in os.listdir(p):
                        if os.path.isdir(os.path.join(p, d2)):
                            cands.append(os.path.join(p, d2))
        except OSError:
            pass
    _MAP_DIRS[src_dir] = cands
    return cands


def resolve(path, src_dir):
    """Bitmap paths in old scenes point at the author's machine; find the file next to the scene or in *Maps* folders."""
    if path and os.path.exists(path):
        return path
    name = os.path.basename(path or "")
    if not name:
        return None
    for c in map_dirs(src_dir):
        p = os.path.join(c, name)
        if os.path.exists(p):
            return p
    return None


def maps_by_part(part, src_dir):
    """Material with no bitmaps but named after its texture set (vendor FBX): find <part>_<type>.jpg in the map folders."""
    for c in map_dirs(src_dir):
        maps = complete_part(part, {"_probe": os.path.join(c, "_")})
        maps.pop("_probe", None)
        if maps:
            return maps
    return {}


_LIBS = {}
_DIRCACHE = {}


def complete_part(part, maps):
    """Add maps of the same part that the material didn't link (translucency, gloss, back albedo...)."""
    if not part or not maps:
        return maps
    folder = os.path.dirname(next(iter(maps.values())))
    if folder not in _DIRCACHE:
        try:
            _DIRCACHE[folder] = os.listdir(folder)
        except OSError:
            _DIRCACHE[folder] = []
    for f in _DIRCACHE[folder]:
        if f.startswith("._") or "_baked_" in f or not f.lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff", ".exr")):
            continue
        p, kind, side = classify(os.path.splitext(f)[0])
        if p != part or not kind:
            continue
        kind = "albedo_back" if side == "B" else kind
        maps.setdefault(kind, os.path.join(folder, f))
    return maps


def find_std_library(src_dir):
    """Vendor 'standard' material library (*standard*.mat) a few folders up, e.g. <Vol>_Material_Librarys."""
    up = src_dir
    for _ in range(5):
        for dp, dn, fn in os.walk(up):
            for f in fn:
                if f.lower().endswith(".mat") and "standard" in f.lower() and not f.startswith("._"):
                    return os.path.join(dp, f)
            if dp.count(os.sep) - up.count(os.sep) >= 2:
                dn[:] = []
        up = os.path.dirname(up)
    return None


def load_library(path):
    """-> [[slot,...] per Multi/Sub]; slot = {name, rgb, gloss, maps:{type: basename}} (plain python, survives resets)."""
    if path in _LIBS:
        return _LIBS[path]
    out = []
    try:
        lib = rt.loadTempMaterialLibrary(path)
        for i in range(int(lib.count)):
            m = lib[i]
            subs = [rt.getSubMtl(m, k + 1) for k in range(rt.getNumSubMtls(m))] if rt.classOf(m) == rt.Multimaterial else [m]
            slots = []
            for sub in subs:
                if sub is None or rt.classOf(sub) != rt.StandardMaterial:
                    slots.append(None)
                    continue
                c = sub.diffuse
                maps = {}
                for fname, _ in bitmaps_in(sub, set()):
                    _, kind, side = classify(os.path.splitext(os.path.basename(fname))[0])
                    if kind:
                        maps.setdefault("albedo_back" if side == "B" else kind, os.path.basename(fname))
                slots.append({"name": str(sub.name), "rgb": [int(c.r), int(c.g), int(c.b)],
                              "gloss": float(sub.glossiness), "maps": maps})
            out.append(slots)
    except Exception as e:
        print("library %s: %s" % (path, e))
    _LIBS[path] = out
    return out


def match_library(lib, scene_slots):
    """Library Multi/Sub whose textured slots share the most bitmaps with the scene's (same slot index)."""
    best, best_score = None, 0
    for slots in lib:
        if len(slots) != len(scene_slots):
            continue
        score = 0
        for ls, maps in zip(slots, scene_slots):
            if ls and maps and set(ls["maps"].values()) & {os.path.basename(p) for p in maps.values()}:
                score += 1
        if score > best_score:
            best, best_score = slots, score
    return best


def part_material(name, maps, values=None):
    m = rt.PhysicalMaterial()
    m.name = name
    values = values or {}
    if values.get("albedo_color"):
        r, g, b = values["albedo_color"]
        m.base_color = rt.Color(r, g, b)
    if "roughness" in values:
        m.roughness = values["roughness"]
    def bm(path):
        b = rt.Bitmaptexture()
        b.filename = path
        return b
    if maps.get("albedo"):
        m.base_color_map = bm(maps["albedo"])
    if maps.get("roughness"):
        m.roughness_map = bm(maps["roughness"])
    elif maps.get("gloss"):
        m.roughness_map = bm(maps["gloss"])
        m.roughness_inv = True
    if maps.get("normal"):
        n = rt.Normal_Bump()
        n.normal_map = bm(maps["normal"])
        m.bump_map = n
    if maps.get("opacity"):
        m.cutout_map = bm(maps["opacity"])
    return m


def convert(src, overwrite):
    res = {"file": src, "ok": False}
    out = os.path.splitext(src)[0] + ".fbx"
    res["fbx"] = out
    if os.path.exists(out) and not overwrite:
        res["skipped"] = "fbx exists"
    rt.resetMaxFile(rt.name("noPrompt"))
    is_fbx = src.lower().endswith(".fbx")
    if is_fbx:  # vendor FBX: read its materials, keep the file itself (no second copy of the geometry)
        res["fbx"] = src
        rt.FBXImporterSetParam("ResetImport")
        rt.FBXImporterSetParam("ConvertUnit", "cm")
        if not rt.importFile(src, rt.name("noPrompt"), using=rt.FBXIMP):
            res["error"] = "could not import FBX"
            return res
    elif not rt.loadMaxFile(src, quiet=True, useFileUnits=True):
        res["error"] = "could not load"
        return res
    res["units"] = str(rt.units.SystemType)
    src_dir = os.path.dirname(src)
    lib_path = find_std_library(src_dir)
    lib = load_library(lib_path) if lib_path else []
    res["library"] = lib_path
    species = re.sub(r"_\d{2}$", "", os.path.splitext(os.path.basename(src))[0])
    objects, parts, missing, aliases, built, bakes = [], {}, set(), {}, {}, {}
    for o in list(rt.objects):
        if rt.superClassOf(o) != rt.GeometryClass:
            continue
        cls = str(rt.classOf(o))
        bb = o.max - o.min
        info = {"name": str(o.name), "class": cls, "size_cm": [round(float(bb.x), 2), round(float(bb.y), 2), round(float(bb.z), 2)]}
        try:
            info["map_channels"] = int(rt.meshop.getNumMaps(o.mesh)) - 1
        except Exception:
            pass
        objects.append(info)
        m = o.material
        if m is None:
            continue
        is_multi = rt.classOf(m) == rt.Multimaterial
        slots = [(i + 1, rt.getSubMtl(m, i + 1)) for i in range(rt.getNumSubMtls(m))] if is_multi else [(0, m)]
        found = []
        for idx, sub in slots:
            maps, chans, part = {}, set(), None
            for fname, ch, top, face in (bitmap_chains(sub, set()) if sub is not None else []):
                stem = os.path.splitext(os.path.basename(fname))[0]
                p, kind, side = classify(stem)
                if not kind:
                    continue
                if face == "back":  # back face of a two-sided leaf: only its albedo matters
                    if kind != "albedo":
                        continue
                    side = "B"
                path = resolve(fname, src_dir)
                if path is None:
                    missing.add(os.path.basename(fname))
                    continue
                if side == "B":
                    kind = "albedo_back"
                part = part or p
                baked = bake_chain(top, path, src_dir) if top is not None and kind in BAKE_KINDS else None
                if baked:
                    maps[kind] = baked  # the tinted / blended result wins over a raw reference to the same bitmap
                    bakes[baked] = path
                else:
                    maps.setdefault(kind, path)
                chans.add(ch)
            if not maps and sub is not None:
                nm = re.sub(r"_[FB]$", "", str(sub.name))
                maps = maps_by_part(nm, src_dir)
                if maps:
                    part, chans = nm, {1}
            found.append((idx, sub, maps, chans, part))
        lib_slots = match_library(lib, [f[2] for f in found]) if lib else None
        for n, (idx, sub, maps, chans, part) in enumerate(found):
            values = {}
            ls = lib_slots[n] if lib_slots and n < len(lib_slots) else None
            if not maps and ls:  # untextured in the scene (renderer-only texmap or plain colour): use the library
                for kind, base in ls["maps"].items():
                    path = resolve(base, src_dir)
                    if path:
                        maps[kind] = path
                if maps:
                    part = classify(os.path.splitext(os.path.basename(next(iter(maps.values()))))[0])[0]
                else:
                    values = {"albedo_color": ls["rgb"], "roughness": round(max(0.0, min(1.0, 1 - ls["gloss"] / 100.0)), 3)}
                    part = "%s_%s" % (species, re.sub(r"_[FB]$", "", ls["name"]) or "slot%d" % idx)
                chans = chans or {1}
            if not maps and not values:
                continue
            if maps and part:
                maps = complete_part(part, maps)
            name = part or str(sub.name)
            alb = os.path.splitext(os.path.basename(maps.get("albedo", "")))[0]
            if alb and (re.search(r"_(albedo|diffuse|color|basecolor)_\d+$", alb, re.I)
                        or (name in parts and parts[name]["maps"].get("albedo") != maps["albedo"])):
                name = alb  # one part, several albedo variants (..._albedo_05 / _albedo_08): name by the albedo file
            if sub is not None and str(sub.name) and str(sub.name) != name:
                aliases[str(sub.name)] = name
            if name not in parts:
                parts[name] = {"maps": maps, "values": values, "uv_channels": sorted(chans)}
            # one material per part name: two slots with the same name in an FBX hang C4D's importer
            new = built.get(name) or built.setdefault(name, part_material(name, parts[name]["maps"], parts[name]["values"]))
            if is_multi:
                rt.setSubMtl(m, idx, new)
            else:
                o.material = new
    res.update(objects=objects, parts=parts, missing_maps=sorted(missing), aliases=aliases, baked=bakes)
    if res.get("skipped") or is_fbx:
        res["ok"] = True
        return res
    rt.FBXExporterSetParam("ResetExport")
    for k, v in (("Animation", False), ("Cameras", False), ("Lights", False), ("EmbedTextures", False),
                 ("SmoothingGroups", True), ("UpAxis", "Y"), ("ConvertUnit", "cm"), ("FileVersion", "FBX202000")):
        rt.FBXExporterSetParam(k, v)
    tmp = out + ".part.fbx"
    rt.exportFile(tmp, rt.name("noPrompt"), using=rt.FBXEXP)
    if not os.path.exists(tmp):
        res["error"] = "FBX export failed"
        return res
    os.replace(tmp, out)
    res["ok"] = True
    return res


def main():
    try:  # the vendors author with Max's gamma 2.2 workflow: bakes match it (identity bake == source to <0.5/255)
        rt.IDisplayGamma.colorCorrectionMode = rt.name("gamma")
    except Exception:
        pass
    with open(os.environ["MAX_JOB"], "r", encoding="utf-8") as fh:
        job = json.load(fh)
    rep = open(job["report"], "a", encoding="utf-8")
    for src in job["files"]:
        t0 = time.time()
        try:
            r = convert(src, job.get("overwrite", False))
        except Exception as e:
            r = {"file": src, "ok": False, "error": "%s: %s" % (type(e).__name__, e), "trace": traceback.format_exc()[-800:]}
        r["seconds"] = round(time.time() - t0, 1)
        rep.write(json.dumps(r) + "\n")
        rep.flush()
    rep.close()


main()
