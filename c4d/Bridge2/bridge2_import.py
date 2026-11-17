"""Import logic — runs on C4D's main thread (called from CoreMessage).

Reloaded on every job, so edits here apply without restarting Cinema 4D.
Payload format: see server/send.py build_payload().
"""
import math
import os
import re

import c4d

ID_OCTANE_MATERIAL = 1029501
ID_OCTANE_IMAGE = 1029508
ID_OCTANE_CC = 1029512
ID_OCTANE_TEX_EMISSION = 1029642
ID_OCTANE_MULTIPLY = 1029516
ID_OCTANE_DISPLACEMENT = 1031901
ID_OCTANE_ENV_TAG = 1029643
ID_OCTANE_SPLAT = 1065727        # Octane Gaussian Splat object
ID_OCTANE_OBJECT_TAG = 1029603   # Octane Object tag (Command Manager: "Add Octane Object Tag")
def _receiver_id():
    import json
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "receiver.json"), encoding="utf-8") as fh:
            return int(json.load(fh).get("receiver_id", 1000007))
    except (OSError, ValueError):
        return 1000007


STAMP_KEY = _receiver_id()      # our plugin ID, used as the key of the stamp container on import roots
SPLAT_FILE, SPLAT_SKIP_POINTS = 3024, 3001
HDRI_PREFIX = "HDRI · "
OCT_MAT_TYPE_UNIVERSAL = 2516

# Octane description IDs (OctaneRenderStudio+ 2.0.0 res/description/*.h). Octane registers
# these as c4d.* symbols at runtime; the numbers are only a fallback if it ever doesn't.
_FALLBACK = {
    "IMAGETEXTURE_FILE": 1100, "IMAGETEXTURE_GAMMA": 1102, "IMAGETEXTURE_INVERT": 1104,
    "IMAGETEXTURE_MODE": 1105, "IMAGETEX_COLOR_SPACE": 1118,
    "MULTIPLY_TEXTURE1": 1140, "MULTIPLY_TEXTURE2": 1141, "COLORCOR_TEXTURE_LNK": 1050,
    "DISPLACEMENT_TEXTURE": 2050, "DISPLACEMENT_AMOUNT": 2052, "DISPLACEMENT_MID": 2054,
    "ENVIRONMENTTAG_TEXTURE": 1150, "ENVIRONMENTTAG_POWER": 1151,
}
TEX_COLOR, TEX_FLOAT = 0, 1
CS_NONE, CS_SRGB, CS_LINEAR = 0, 1, 2
FLOAT_EXT = (".exr", ".hdr")  # float textures: linear, never gamma-corrected


def S(name):
    v = getattr(c4d, name, None)
    if v is None:
        v = _FALLBACK.get(name)
    if v is None:
        raise RuntimeError("Octane symbol %s not available — is Octane loaded?" % name)
    return v


def octane_available():
    return c4d.plugins.FindPlugin(ID_OCTANE_MATERIAL, c4d.PLUGINTYPE_MATERIAL) is not None


# --------------------------------------------------------------------- entry

def run(payload, state=None):
    """state: dict owned by the plugin (survives reloads) — remembers what we auto-selected."""
    state = state if state is not None else {}
    doc = c4d.documents.GetActiveDocument()
    asset = payload["asset"]
    opts = payload.get("options", {})
    maps = {k: v for k, v in (payload.get("maps") or {}).items() if v and os.path.exists(v)}
    renderer = opts.get("renderer", "octane")
    if renderer == "octane" and not octane_available():
        renderer = "standard"
    selection = doc.GetActiveObjects(c4d.GETACTIVEOBJECTFLAGS_NONE)
    last = state.get("auto_selected")
    if (last is not None and len(selection) == 1 and last.IsAlive() and selection[0] == last
            and doc.GetDirty(c4d.DIRTYFLAGS_ALL) == state.get("auto_dirty")):
        selection = []  # still selected only because we selected it after the previous import
    name = asset.get("name") or "Asset"
    notes = []

    if asset.get("type") == "hdri":
        return _run_hdri(doc, maps, name, renderer, opts, state)
    if asset.get("type") == "splat":
        return _run_splat(doc, payload, name, state)

    sig = payload.get("signature")
    if opts.get("instance", True) and sig and (payload.get("geometry") or payload.get("native")):
        src = find_stamped_root(doc, sig)
        if src is not None:
            return make_instance(doc, src, name, state, render_instance=True)

    doc.StartUndo()
    try:
        group = None
        mat = None
        merged_mats = []

        # 1) geometry
        if payload.get("native"):
            objs, merged_mats = _merge(doc, payload["native"], with_materials=True)
            group = _group(doc, name, objs)
        elif payload.get("geometry"):
            # models without a texture set keep the materials in their file; multi-part models need them
            # too, so each file material can be swapped for the matching part's Octane material
            own_mats = not maps or bool(payload.get("material_sets"))
            parts = []
            for g in payload["geometry"]:
                objs, mm = _merge(doc, g["path"], with_materials=own_mats)
                if g.get("object"):  # vendor file holding every variation side by side: keep one
                    objs = _keep_object(doc, objs, g["object"], notes)
                merged_mats += mm
                if maps and not payload.get("material_sets"):  # our material replaces whatever the file referenced
                    for o in objs:
                        _strip_texture_tags(o)
                label = g.get("variation") or os.path.splitext(os.path.basename(g["path"]))[0]
                parts.append((label, objs))
            if len(parts) == 1:
                group = _group(doc, name, parts[0][1])
            elif parts:
                group = c4d.BaseObject(c4d.Onull)
                group.SetName(name)
                x = 0.0
                for label, objs in parts:
                    sub = _group(doc, label, objs, insert=False)
                    w = _width(sub)
                    sub.SetRelPos(c4d.Vector(x + w / 2, 0, 0))
                    x += w * 1.15
                    sub.InsertUnderLast(group)
                doc.InsertObject(group)
                doc.AddUndo(c4d.UNDOTYPE_NEW, group)

        for m in merged_mats:
            doc.AddUndo(c4d.UNDOTYPE_NEW, m)

        # 1b) multi-material models: swap the file's materials for ones built from the matching texture set
        sets = {k: {t: p for t, p in v.items() if os.path.exists(p)} for k, v in (payload.get("material_sets") or {}).items()}
        opts["_values"] = payload.get("material_values") or {}
        opts["_aliases"] = payload.get("material_aliases") or {}
        for k in opts["_values"]:
            sets.setdefault(k, {})  # colour-only parts (no textures)
        if sets and merged_mats and group is not None and not payload.get("native"):
            if maps:
                sets[name] = maps  # the model's own set is just another part
            n, unmatched = _apply_material_sets(doc, group, merged_mats, sets, name, asset, opts, renderer)
            notes.append("%d part material(s) built" % n)
            if unmatched:
                notes.append("kept file material(s): " + ", ".join(unmatched[:4]))
            maps = {}  # handled per part

        # 1c) vendor scenes with C4D standard materials: Octane renders them badly, so convert
        if renderer == "octane" and merged_mats and group is not None and not payload.get("native"):
            live = [m for m in merged_mats if m.IsAlive() and m.GetDocument() is not None]
            n = _standard_to_octane(doc, group, live, asset, opts)
            if n:
                notes.append("%d standard material(s) converted to Octane" % n)

        # 1d) no Octane in this C4D: Octane materials from the file -> Standard, so nothing renders grey
        if renderer != "octane" and merged_mats and group is not None:
            live = [m for m in merged_mats if m.IsAlive() and m.GetDocument() is not None]
            src = payload.get("native") or (payload.get("geometry") or [{}])[0].get("path", "")
            n = _octane_to_standard(doc, group, live, src)
            if n:
                merged_mats = [m for m in merged_mats if m.IsAlive() and m.GetDocument() is not None]
                notes.append("%d Octane material(s) rebuilt as Standard (no Octane in this Cinema 4D)" % n)

        # 2) material from the texture set
        if maps and not payload.get("native"):
            mat_name = "%s [%s]" % (name, opts["resolution"]) if opts.get("resolution") else name
            mat = _find_material(doc, mat_name, renderer)
            if mat is None:
                has_geo = group is not None
                if renderer == "octane":
                    mat = build_octane(maps, mat_name, asset, opts, has_geo)
                else:
                    mat = build_standard(maps, mat_name)
                doc.InsertMaterial(mat)
                doc.AddUndo(c4d.UNDOTYPE_NEW, mat)
            else:
                notes.append("reused existing material")

            # 3) assignment
            if group is not None:
                _assign(doc, group, mat, new=True)
            elif opts.get("apply_to_selection", True) and selection:
                for o in selection:
                    _assign(doc, o, mat, new=False)
                notes.append("applied to %d selected object(s)" % len(selection))
            doc.SetActiveMaterial(mat, c4d.SELECTION_NEW)

        if group is not None:
            stamp(group, payload)
            doc.SetActiveObject(group, c4d.SELECTION_NEW)
            state["auto_selected"] = group
    finally:
        doc.EndUndo()
        c4d.EventAdd()
    state["auto_dirty"] = doc.GetDirty(c4d.DIRTYFLAGS_ALL)  # any later edit/click means the user owns the selection

    what = []
    if group is not None:
        what.append("geometry")
    if mat is not None:
        what.append("%s material" % ("Octane Universal" if renderer == "octane" else "Standard"))
    kept = [m for m in merged_mats if m.IsAlive() and m.GetDocument() is not None]
    if kept and not mat:
        what.append("%d material(s) from file" % len(kept))
    return {"ok": True, "message": "Imported %s: %s%s" % (name, ", ".join(what) or "nothing",
                                                          (" (" + "; ".join(notes) + ")") if notes else ""),
            "renderer": renderer, "material": mat.GetName() if mat else None,
            "object": group.GetName() if group else None}


# --------------------------------------------------------------------- splats

def _run_splat(doc, payload, name, state):
    splat, proxy = payload.get("splat"), payload.get("proxy")
    opts = payload.get("options", {})
    sig = payload.get("signature")
    if opts.get("instance", True) and sig:
        src = find_stamped_root(doc, sig)
        if src is not None:  # plain instance: Octane render-instancing of splat objects isn't proven
            return make_instance(doc, src, name, state, render_instance=False)
    if splat and c4d.plugins.FindPlugin(ID_OCTANE_SPLAT, c4d.PLUGINTYPE_OBJECT) is None:
        if not proxy:
            raise RuntimeError("Octane's Gaussian Splat object isn't available in this Cinema 4D")
        splat = None  # still bring the proxy in
    doc.StartUndo()
    try:
        root = c4d.BaseObject(c4d.Onull)
        root.SetName(name)
        sc = float(payload.get("proxy_scale") or payload.get("scale") or 1.0)  # splat file is pre-scaled
        parts = []
        if splat:
            # a layered asset (e.g. a volume layer + a star-field layer) brings one splat object per layer, side by side
            layers = payload.get("splats") or [splat]
            for sp in layers:
                so = c4d.BaseObject(ID_OCTANE_SPLAT)
                so.SetName("%s · %s" % (name, sp.get("layer") or "Splat"))
                so[SPLAT_FILE] = sp["path"]
                try:
                    so[SPLAT_SKIP_POINTS] = int(opts.get("skip_points", 0))  # viewport only; renders use every splat
                except Exception:
                    pass
                # every splat gets an Octane Object tag: the splat object doesn't come with one, and the user's Octane
                # workflow (render settings per object, visibility, layers) expects it on each splat
                if c4d.plugins.FindPlugin(ID_OCTANE_OBJECT_TAG, c4d.PLUGINTYPE_TAG) is not None:
                    tag = so.MakeTag(ID_OCTANE_OBJECT_TAG)
                    if tag is not None:
                        tag.SetName("Octane Object")
                so.InsertUnderLast(root)
            fmt = os.path.basename(splat["path"]).rsplit(".", 1)[-1].upper()
            if len(layers) > 1:
                parts.append("%d Octane splats (%s: %s) + Octane tags" % (
                    len(layers), fmt, " + ".join(sp.get("layer") or "?" for sp in layers)))
            else:
                parts.append("Octane splat (%s) + Octane tag" % fmt)
        if proxy:
            objs, mats = _merge(doc, proxy["path"], with_materials=True)
            grp = _group(doc, name + " · Proxy", objs, insert=False)
            grp.SetRelScale(c4d.Vector(sc, sc, sc))
            # No turn: C4D's glTF import of the asset-folder proxy already lines up 1:1 with the Octane splat
            # (verified in C4D 2026; an H=180 here faced it backwards).
            if splat:  # viewport stand-in only; the splat renders
                for o in _walk_one(grp):
                    o[c4d.ID_BASEOBJECT_VISIBILITY_RENDER] = c4d.OBJECT_OFF
            grp.InsertUnderLast(root)
            for m in mats:
                doc.AddUndo(c4d.UNDOTYPE_NEW, m)
            parts.append("proxy" + (" (viewport only)" if splat else ""))
        stamp(root, payload)
        doc.InsertObject(root)
        doc.AddUndo(c4d.UNDOTYPE_NEW, root)
        doc.SetActiveObject(root, c4d.SELECTION_NEW)
        state["auto_selected"] = root
    finally:
        doc.EndUndo()
        c4d.EventAdd()
    state["auto_dirty"] = doc.GetDirty(c4d.DIRTYFLAGS_ALL)
    scale_note = "" if abs(sc - 1) < 1e-6 else ", real scale x%.3g" % sc
    return {"ok": True, "message": "Imported %s: %s%s" % (name, " + ".join(parts), scale_note), "object": name}


# --------------------------------------------------------------------- stamps + instances

def stamp(obj, payload):
    """Record which library asset/options built this root (saved with the scene)."""
    bc = c4d.BaseContainer()
    bc.SetString(1, payload["asset"].get("id") or "")
    bc.SetString(2, payload.get("signature") or "")
    bc.SetString(3, payload["asset"].get("name") or "")
    bc.SetString(4, payload["asset"].get("library") or "")
    obj.GetDataInstance().SetContainer(STAMP_KEY, bc)


def stamp_info(obj):
    bc = obj.GetDataInstance().GetContainerInstance(STAMP_KEY)
    if bc is None:
        return None
    return {"id": bc.GetString(1), "signature": bc.GetString(2), "name": bc.GetString(3), "library": bc.GetString(4)}


def find_stamped_root(doc, sig):
    for o in _walk(doc.GetFirstObject()):
        info = stamp_info(o)
        if info and info["signature"] == sig and o.GetType() != c4d.Oinstance:
            return o
    return None


def make_instance(doc, src, name, state, render_instance):
    count = sum(1 for o in _walk(doc.GetFirstObject())
                if o.GetType() == c4d.Oinstance and o[c4d.INSTANCEOBJECT_LINK] is not None and o[c4d.INSTANCEOBJECT_LINK] == src)
    doc.StartUndo()
    try:
        inst = c4d.BaseObject(c4d.Oinstance)
        inst.SetName("%s · Instance %d" % (name, count + 1))
        inst[c4d.INSTANCEOBJECT_LINK] = src
        if render_instance:
            mode = getattr(c4d, "INSTANCEOBJECT_RENDERINSTANCE_MODE", None)
            single = getattr(c4d, "INSTANCEOBJECT_RENDERINSTANCE_MODE_SINGLEINSTANCE", 1)
            if mode is not None:
                inst[mode] = single
        w = max(_width(src), 1.0)
        mg = src.GetMg()
        mg.off = mg.off + c4d.Vector(w * 1.15 * (count + 1), 0, 0)  # beside the original so it's visible
        inst.SetMg(mg)
        info = stamp_info(src) or {}
        bc = c4d.BaseContainer()
        bc.SetString(1, info.get("id", "")); bc.SetString(2, "instance"); bc.SetString(3, info.get("name", ""))
        inst.GetDataInstance().SetContainer(STAMP_KEY, bc)
        doc.InsertObject(inst)
        doc.AddUndo(c4d.UNDOTYPE_NEW, inst)
        doc.SetActiveObject(inst, c4d.SELECTION_NEW)
        state["auto_selected"] = inst
    finally:
        doc.EndUndo()
        c4d.EventAdd()
    state["auto_dirty"] = doc.GetDirty(c4d.DIRTYFLAGS_ALL)
    return {"ok": True, "object": inst.GetName(),
            "message": "Added %s %s of %s (already in the scene)" % ("a render instance" if render_instance else "an instance",
                                                                       "#%d" % (count + 1), name)}


# --------------------------------------------------------------------- HDRI

def _run_hdri(doc, maps, name, renderer, opts, state=None):
    path = maps.get("hdri")
    if not path:
        raise RuntimeError("HDRI file missing")
    doc.StartUndo()
    try:
        sky = None
        if opts.get("replace_environment", True):
            o = doc.GetFirstObject()
            while o:
                if o.GetType() == c4d.Osky and o.GetName().startswith(HDRI_PREFIX):
                    sky = o
                    break
                o = o.GetNext()
        created = sky is None
        if created:
            sky = c4d.BaseObject(c4d.Osky)
            doc.InsertObject(sky)
            doc.AddUndo(c4d.UNDOTYPE_NEW, sky)
        else:
            doc.AddUndo(c4d.UNDOTYPE_CHANGE, sky)
        sky.SetName(HDRI_PREFIX + name)

        if renderer == "octane":
            tag = sky.GetTag(ID_OCTANE_ENV_TAG)
            if tag is None:
                tag = c4d.BaseTag(ID_OCTANE_ENV_TAG)
                sky.InsertTag(tag)
                if not created:
                    doc.AddUndo(c4d.UNDOTYPE_NEW, tag)
                tag[S("ENVIRONMENTTAG_POWER")] = 1.0
            elif not created:
                doc.AddUndo(c4d.UNDOTYPE_CHANGE, tag)
            old = tag[S("ENVIRONMENTTAG_TEXTURE")]
            tex = c4d.BaseShader(ID_OCTANE_IMAGE)
            tex.SetName(name)
            tex[S("IMAGETEXTURE_FILE")] = path
            tex[S("IMAGETEXTURE_MODE")] = TEX_COLOR
            tex[S("IMAGETEXTURE_GAMMA")] = 1.0
            try:
                tex[S("IMAGETEX_COLOR_SPACE")] = CS_LINEAR
            except Exception:
                pass
            tag.InsertShader(tex)
            tag[S("ENVIRONMENTTAG_TEXTURE")] = tex
            if old is not None:
                old.Remove()
            tag.Message(c4d.MSG_UPDATE)
        else:
            mat = c4d.BaseMaterial(c4d.Mmaterial)
            mat.SetName(HDRI_PREFIX + name)
            mat[c4d.MATERIAL_USE_COLOR] = False
            mat[c4d.MATERIAL_USE_REFLECTION] = False
            mat[c4d.MATERIAL_USE_LUMINANCE] = True
            sh = c4d.BaseShader(c4d.Xbitmap)
            sh[c4d.BITMAPSHADER_FILENAME] = path
            mat.InsertShader(sh)
            mat[c4d.MATERIAL_LUMINANCE_SHADER] = sh
            doc.InsertMaterial(mat)
            doc.AddUndo(c4d.UNDOTYPE_NEW, mat)
            for t in [t for t in sky.GetTags() if t.GetType() == c4d.Ttexture]:
                t.Remove()
            tt = c4d.TextureTag()
            tt.SetMaterial(mat)
            tt[c4d.TEXTURETAG_PROJECTION] = c4d.TEXTURETAG_PROJECTION_SPHERICAL
            sky.InsertTag(tt)
        doc.SetActiveObject(sky, c4d.SELECTION_NEW)
        if state is not None:  # we selected the sky, the user didn't: the next surface send must not land on it
            state["auto_selected"] = sky
    finally:
        doc.EndUndo()
        c4d.EventAdd()
    if state is not None:
        state["auto_dirty"] = doc.GetDirty(c4d.DIRTYFLAGS_ALL)
    return {"ok": True, "renderer": renderer, "object": sky.GetName(), "material": None,
            "message": "%s %s environment: %s" % ("Created" if created else "Swapped",
                                                   "Octane HDRI" if renderer == "octane" else "Sky", name)}


# --------------------------------------------------------------------- multi-material models

def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _drop_if_unused(doc, group, mat):
    """A file material whose texture tags only point at empty (or missing) polygon selections is unused:
    remove those tags and the material. Returns True when it was removed."""
    tags = [(o, t) for o in _walk_one(group) for t in o.GetTags()
            if t.GetType() == c4d.Ttexture and t.GetMaterial() is not None and t.GetMaterial() == mat]
    for o, t in tags:
        sel = t[c4d.TEXTURETAG_RESTRICTION]
        if not sel:
            return False  # applies to the whole object
        st = next((x for x in o.GetTags() if x.GetType() == c4d.Tpolygonselection and x.GetName() == sel), None)
        if st is not None and st.GetBaseSelect().GetCount() > 0:
            return False
    for o, t in tags:
        t.Remove()
    if mat.GetDocument() is not None:
        doc.AddUndo(c4d.UNDOTYPE_DELETEOBJ, mat)
        mat.Remove()
    return True


def _apply_material_sets(doc, group, merged_mats, sets, name, asset, opts, renderer):
    built, mapping, unmatched = {}, [], []
    aliases = opts.get("_aliases") or {}
    for m in merged_mats:
        if _drop_if_unused(doc, group, m):  # Multi/Sub slots no face uses still come through the FBX
            continue
        nm = _norm(m.GetName())
        best, best_len = None, 0
        alias = aliases.get(m.GetName()) or aliases.get(re.sub(r"\.\d+$", "", m.GetName()))
        if alias in sets:  # explicit mapping from the converter (vendor names like "trunk1", "leaf2_F")
            best = alias
        for part in ([] if best else sets):
            np_ = _norm(part)
            # "bark" in "fir_tree_01_bark", or the file's "pot_soil" inside "TexturesCom Ground PottingSoil"
            hit = len(np_) if np_ and np_ in nm else (len(nm) if len(nm) >= 3 and nm in np_ else 0)
            if hit > best_len:
                best, best_len = part, hit
        if best is None and len(sets) == 1 and len(merged_mats) == 1:
            best = next(iter(sets))
        if best is None or (not sets[best] and best not in opts.get("_values", {})):
            if _drop_if_unused(doc, group, m):
                continue
            unmatched.append(m.GetName())
            continue
        if best not in built:
            res = opts.get("resolution")
            label = name if _norm(best) == _norm(name) else "%s · %s" % (name, best)
            mname = "%s [%s]" % (label, res) if res else label
            newm = _find_material(doc, mname, renderer)
            if newm is None:
                if renderer == "octane":
                    newm = build_octane(sets[best], mname, asset, opts, True)
                else:
                    newm = build_standard(sets[best], mname)
                if opts.get("_values", {}).get(best):
                    apply_values(newm, opts["_values"][best], renderer, sets[best])
                doc.InsertMaterial(newm)
                doc.AddUndo(c4d.UNDOTYPE_NEW, newm)
            built[best] = newm
        mapping.append((m, built[best]))
    for o in _walk_one(group):
        for t in o.GetTags():
            if t.GetType() != c4d.Ttexture:
                continue
            cur = t.GetMaterial()
            for old, new in mapping:
                if cur is not None and cur == old:
                    t.SetMaterial(new)
    for old, _ in mapping:
        if old.IsAlive() and old.GetDocument() is not None:
            doc.AddUndo(c4d.UNDOTYPE_DELETEOBJ, old)
            old.Remove()
    return len(built), unmatched


# --------------------------------------------------------------------- geometry

def _top(doc):
    out, o = [], doc.GetFirstObject()
    while o:
        out.append(o)
        o = o.GetNext()
    return out


def _shaders(sh):
    while sh:
        yield sh
        for x in _shaders(sh.GetDown()):
            yield x
        sh = sh.GetNext()


def _texture_roots(src_path):
    """Where a vendor scene's bare texture names resolve: next to it, its tex/textures folders, sibling map folders."""
    base = os.path.dirname(src_path)
    roots = [base, os.path.join(base, "tex"), os.path.join(base, "textures"), os.path.join(os.path.dirname(base), "tex"),
             os.path.join(os.path.dirname(base), "Textures")]
    try:  # volumes that ship maps in a sibling folder (MT_PM_V139_Maps next to the scenes)
        roots += [os.path.join(base, d) for d in os.listdir(base)
                  if os.path.isdir(os.path.join(base, d)) and ("map" in d.lower() or "tex" in d.lower())]
    except OSError:
        pass
    return roots


def _resolve_texture(f, roots):
    if not f:
        return None
    f = str(f)
    if os.path.isabs(f) and os.path.exists(f):
        return f
    name = os.path.basename(f.replace("\\", "/"))
    for r in roots:
        cand = os.path.join(r, name)
        if os.path.exists(cand):
            return cand
    return None


def _absolutize_textures(mats, src_path):
    """Vendor scenes reference textures by bare name (resolved next to the scene or in its tex folder).
    Once merged into another document those lookups fail, so write absolute paths."""
    roots = _texture_roots(src_path)
    octane = octane_available()  # without Octane its shaders are placeholders: _octane_to_standard reads their names
    fixed = 0
    for m in mats:
        for sh in _shaders(m.GetFirstShader()):
            t = sh.GetType()
            pid = S("IMAGETEXTURE_FILE") if t == ID_OCTANE_IMAGE and octane else (c4d.BITMAPSHADER_FILENAME if t == c4d.Xbitmap else None)
            if pid is None:
                continue
            f = sh[pid]
            if not f or (os.path.isabs(f) and os.path.exists(f)):
                continue
            name = os.path.basename(str(f).replace("\\", "/"))
            for r in roots:
                cand = os.path.join(r, name)
                if os.path.exists(cand):
                    sh[pid] = cand
                    fixed += 1
                    break
    return fixed


# texture-name token -> map kind (last "_" token of the file name: Leaf_01_albedo.jpg -> albedo)
MAP_TOKENS = {"albedo": "albedo", "diffuse": "albedo", "basecolor": "albedo", "color": "albedo", "col": "albedo",
              "dif": "albedo", "diff": "albedo", "normal": "normal", "nor": "normal", "nrm": "normal", "norm": "normal",
              "bump": "bump", "height": "bump", "opacity": "opacity", "alpha": "opacity", "mask": "opacity",
              "roughness": "roughness", "rough": "roughness", "glossiness": "gloss", "gloss": "gloss",
              "translucency": "translucency", "transmission": "translucency", "emissive": "emission", "emission": "emission"}


def _image_name(sh):
    """Texture file name of an Octane image shader whose plugin isn't loaded: the File parameter, else any image
    file name in its data (an unknown plugin's container is kept as loaded; verified on C4D 2027 without Octane)."""
    bc = sh.GetDataInstance()
    v = bc.GetString(_FALLBACK["IMAGETEXTURE_FILE"])
    if v:
        return v
    for _, val in bc:
        if isinstance(val, str) and val.lower().endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".tga", ".hdr")):
            return val
    return None


def _octane_to_standard(doc, group, mats, src_path):
    """C4D without Octane (e.g. a new release before Octane supports it): vendor .c4d files built for Octane load
    as placeholder materials that render grey. Their image shaders still carry the texture file names, so each
    one becomes a Standard material of the same name (albedo / normal or bump / alpha) and the tags are swapped."""
    roots = _texture_roots(src_path)
    swap = []
    for m in mats:
        if m.GetType() != ID_OCTANE_MATERIAL:
            continue
        maps = {}
        for sh in _shaders(m.GetFirstShader()):
            if sh.GetType() != ID_OCTANE_IMAGE:
                continue
            f = _resolve_texture(_image_name(sh), roots)
            if not f:
                continue
            tok = os.path.splitext(os.path.basename(f))[0].lower().replace("-", "_").split("_")[-1]
            kind = MAP_TOKENS.get(tok)
            if kind:
                maps.setdefault(kind, f)
        new = build_standard(maps, m.GetName())
        doc.InsertMaterial(new)
        doc.AddUndo(c4d.UNDOTYPE_NEW, new)
        swap.append((m, new))
    if not swap:
        return 0
    for o in _walk_one(group):
        for t in o.GetTags():
            if t.GetType() == c4d.Ttexture:
                for old, new in swap:
                    if t.GetMaterial() == old:
                        doc.AddUndo(c4d.UNDOTYPE_CHANGE, t)
                        t.SetMaterial(new)
    for old, _ in swap:
        doc.AddUndo(c4d.UNDOTYPE_DELETE, old)
        old.Remove()
    return len(swap)


METAL_WORDS = ("chrome", "copper", "steel", "iron", "gold", "silver", "brass", "alumin", "bronze")  # "MetalGreen" = paint


def _standard_to_octane(doc, group, mats, asset, opts):
    """Vendor .c4d with C4D standard materials -> Octane Universal of the same name (tags swapped). Colour or
    texture -> albedo, bump/normal/alpha maps, roughness from the first reflection layer, transparency ->
    specular transmission, luminance -> emission, metal names -> metallic. Octane materials are left alone."""
    swap = []
    for m in mats:
        if m.GetType() != c4d.Mmaterial or not m.IsAlive() or m.GetDocument() is None:
            continue
        if _drop_if_unused(doc, group, m):  # kit files carry every part's materials; keep only what this import uses
            continue
        def bmp(sid):
            sh = m[sid]
            if sh is not None and sh.GetType() == c4d.Xbitmap:
                f = sh[c4d.BITMAPSHADER_FILENAME]
                return f if f and os.path.exists(f) else None
            return None
        maps, col = {}, None
        if m[c4d.MATERIAL_USE_COLOR]:
            p = bmp(c4d.MATERIAL_COLOR_SHADER)
            if p:
                maps["albedo"] = p
            else:
                col = m[c4d.MATERIAL_COLOR_COLOR] * m[c4d.MATERIAL_COLOR_BRIGHTNESS]  # C4D colours are linear already
        if m[c4d.MATERIAL_USE_NORMAL] and bmp(c4d.MATERIAL_NORMAL_SHADER):
            maps["normal"] = bmp(c4d.MATERIAL_NORMAL_SHADER)
        if m[c4d.MATERIAL_USE_BUMP] and bmp(c4d.MATERIAL_BUMP_SHADER):
            b = bmp(c4d.MATERIAL_BUMP_SHADER)
            maps.setdefault("normal" if "normal" in os.path.basename(b).lower() else "bump", b)
        if m[c4d.MATERIAL_USE_ALPHA] and bmp(c4d.MATERIAL_ALPHA_SHADER):
            maps["opacity"] = bmp(c4d.MATERIAL_ALPHA_SHADER)
        rough, layer_col = None, None
        try:
            if m.GetReflectionLayerCount():
                base = m.GetReflectionLayerIndex(0).GetDataID()
                rough = m[base + c4d.REFLECTION_LAYER_MAIN_VALUE_ROUGHNESS]
                layer_col = m[base + c4d.REFLECTION_LAYER_COLOR_COLOR]
        except Exception:
            pass
        om = build_octane(maps, m.GetName(), asset, opts, True)
        metal = any(w in m.GetName().lower() for w in METAL_WORDS)
        if metal:  # metals keep their tint in the reflection layer (copper 1, .67, .31); chrome = white
            om[S("OCT_MAT_SPECULAR_MAP_FLOAT")] = 1.0
            if "albedo" not in maps:
                om[S("OCT_MATERIAL_DIFFUSE_COLOR")] = layer_col if layer_col is not None else c4d.Vector(0.9)
        elif col is not None:
            om[S("OCT_MATERIAL_DIFFUSE_COLOR")] = col
        if rough is not None and "roughness" not in maps and "gloss" not in maps:
            om[S("OCT_MATERIAL_ROUGHNESS_FLOAT")] = float(rough)
        if m[c4d.MATERIAL_USE_TRANSPARENCY]:  # glass
            t = m[c4d.MATERIAL_TRANSPARENCY_COLOR] * m[c4d.MATERIAL_TRANSPARENCY_BRIGHTNESS]
            om[S("OCT_MATERIAL_TRANSMISSION_COLOR")] = t
            om[S("OCT_MATERIAL_TRANSMISSION_FLOAT")] = 1.0
            om[S("UNIVMAT_TRANSMISSION_TYPE")] = 0  # specular
            om[S("OCT_MATERIAL_DIFFUSE_COLOR")] = c4d.Vector(0)
        if m[c4d.MATERIAL_USE_LUMINANCE]:  # LEDs, screens
            lum = c4d.BaseShader(1029504)
            lum[c4d.RGBSPECTRUMSHADER_COLOR] = m[c4d.MATERIAL_LUMINANCE_COLOR] * m[c4d.MATERIAL_LUMINANCE_BRIGHTNESS]
            em = c4d.BaseShader(ID_OCTANE_TEX_EMISSION)
            em[S("TEXEMISSION_EFFIC_OR_TEX")] = lum
            em[S("TEXEMISSION_SURFACE_BRIGHTNESS")] = True
            om.InsertShader(em)
            om.InsertShader(lum)
            om[S("OCT_MATERIAL_EMISSION")] = em
        doc.InsertMaterial(om)
        doc.AddUndo(c4d.UNDOTYPE_NEW, om)
        swap.append((m, om))
    if not swap:
        return 0
    for o in _walk_one(group):
        for t in o.GetTags():
            if t.GetType() == c4d.Ttexture:
                for old, new in swap:
                    if t.GetMaterial() == old:
                        t.SetMaterial(new)
    for old, new in swap:
        name = old.GetName()
        if old.IsAlive() and old.GetDocument() is not None:
            doc.AddUndo(c4d.UNDOTYPE_DELETEOBJ, old)
            old.Remove()
        new.SetName(name)  # C4D suffixed ".1" while both existed
    return len(swap)


def _merge(doc, path, with_materials):
    before_o = _top(doc)
    before_m = doc.GetMaterials()
    flags = c4d.SCENEFILTER_OBJECTS | (c4d.SCENEFILTER_MATERIALS if with_materials else 0)
    if not c4d.documents.MergeDocument(doc, path, flags, None):
        raise RuntimeError("Cinema 4D could not load %s" % path)
    new_o = [o for o in _top(doc) if not any(o == b for b in before_o)]
    new_m = [m for m in doc.GetMaterials() if not any(m == b for b in before_m)]
    if new_m:
        _absolutize_textures(new_m, path)
    if not new_o and not new_m:
        raise RuntimeError("Nothing was imported from %s" % os.path.basename(path))
    return new_o, new_m


def _keep_object(doc, objs, want, notes):
    """Keep only the object named `want` from freshly merged objects, at the origin (X/Z), world-transform kept."""
    keep = next((o for top in objs for o in _walk_one(top) if o.GetName() == want), None)
    if keep is None:
        notes.append("object %s not found, kept the whole file" % want)
        return objs
    mg = keep.GetMg()
    keep.Remove()
    for top in objs:
        if top.IsAlive() and top.GetDocument() is not None:
            top.Remove()
    doc.InsertObject(keep)
    off = mg.off
    mg.off = c4d.Vector(0, off.y, 0)
    keep.SetMg(mg)
    return [keep]


def _group(doc, name, objs, insert=True):
    null = c4d.BaseObject(c4d.Onull)
    null.SetName(name)
    for o in reversed(objs):
        o.Remove()
        o.InsertUnder(null)
    if insert:
        doc.InsertObject(null)
        doc.AddUndo(c4d.UNDOTYPE_NEW, null)
    return null


def _walk(o):
    while o:
        yield o
        for c in _walk(o.GetDown()):
            yield c
        o = o.GetNext()


def _strip_texture_tags(root):
    for o in _walk_one(root):
        for t in [t for t in o.GetTags() if t.GetType() == c4d.Ttexture]:
            t.Remove()


def _walk_one(root):
    yield root
    for c in _walk(root.GetDown()):
        yield c


def _width(root):
    lo, hi = None, None
    for o in _walk_one(root):
        if not isinstance(o, c4d.PointObject) or o.GetPointCount() == 0:
            continue
        mg = o.GetMg()
        mp, rad = mg * o.GetMp(), o.GetRad()
        a, b = mp.x - rad.x, mp.x + rad.x
        lo = a if lo is None else min(lo, a)
        hi = b if hi is None else max(hi, b)
    return (hi - lo) if lo is not None else 100.0


def _assign(doc, obj, mat, new):
    tag = obj.GetTag(c4d.Ttexture) if not new else None
    if tag is None:
        tag = c4d.TextureTag()
        obj.InsertTag(tag)
        if not new:
            doc.AddUndo(c4d.UNDOTYPE_NEW, tag)
    elif not new:
        doc.AddUndo(c4d.UNDOTYPE_CHANGE, tag)
    tag.SetMaterial(mat)
    tag[c4d.TEXTURETAG_PROJECTION] = c4d.TEXTURETAG_PROJECTION_UVW


def _find_material(doc, name, renderer):
    want = ID_OCTANE_MATERIAL if renderer == "octane" else c4d.Mmaterial
    m = doc.GetFirstMaterial()
    while m:
        if m.GetName() == name and m.GetType() == want:
            return m
        m = m.GetNext()
    return None


# --------------------------------------------------------------------- Octane

def build_octane(maps, name, asset, opts, has_geometry):
    mat = c4d.BaseMaterial(ID_OCTANE_MATERIAL)
    mat[S("OCT_MATERIAL_TYPE")] = OCT_MAT_TYPE_UNIVERSAL
    mat.SetName(name)
    try:
        mat[S("OCT_MAT_BRDF_MODEL")] = 2  # GGX
    except Exception:
        pass

    def tex(kind, label, mode, color, invert=False):
        path = maps.get(kind)
        if not path:
            return None
        t = c4d.BaseShader(ID_OCTANE_IMAGE)
        t.SetName(label)
        t[S("IMAGETEXTURE_FILE")] = path
        t[S("IMAGETEXTURE_MODE")] = mode
        flt = path.lower().endswith(FLOAT_EXT)  # EXR/HDR colour is already linear
        t[S("IMAGETEXTURE_GAMMA")] = 2.2 if color and not flt else 1.0
        if invert:
            t[S("IMAGETEXTURE_INVERT")] = True
        try:
            t[S("IMAGETEX_COLOR_SPACE")] = (CS_LINEAR if flt else CS_SRGB) if color else CS_NONE
        except Exception:
            pass
        mat.InsertShader(t)
        return t

    def link(sym, shader):
        if shader is not None:
            mat[S(sym)] = shader

    albedo = tex("albedo", "Albedo", TEX_COLOR, True)
    if albedo is not None:
        cc = c4d.BaseShader(ID_OCTANE_CC)
        cc.SetName("Albedo CC")
        cc[S("COLORCOR_TEXTURE_LNK")] = albedo
        mat.InsertShader(cc)
        top = cc
        if opts.get("ao", True) and "ao" in maps and "metalness" not in maps:
            ao = tex("ao", "AO", TEX_FLOAT, False)
            mul = c4d.BaseShader(ID_OCTANE_MULTIPLY)
            mul.SetName("Albedo x AO")
            mul[S("MULTIPLY_TEXTURE1")] = cc
            mul[S("MULTIPLY_TEXTURE2")] = ao
            mat.InsertShader(mul)
            top = mul
        link("OCT_MATERIAL_DIFFUSE_LINK", top)

    if "roughness" in maps:
        link("OCT_MATERIAL_ROUGHNESS_LINK", tex("roughness", "Roughness", TEX_FLOAT, False))
    elif "gloss" in maps:
        link("OCT_MATERIAL_ROUGHNESS_LINK", tex("gloss", "Gloss (inverted)", TEX_FLOAT, False, invert=True))

    if "metalness" in maps:
        link("OCT_MAT_SPECULAR_MAP_LINK", tex("metalness", "Metalness", TEX_FLOAT, False))
    elif "specular" in maps:
        link("OCT_MATERIAL_SPECULAR_LINK", tex("specular", "Specular", TEX_COLOR, True))

    if "normal" in maps:
        link("OCT_MATERIAL_NORMAL_LINK", tex("normal", "Normal", TEX_COLOR, False))
    elif "bump" in maps:
        link("OCT_MATERIAL_BUMP_LINK", tex("bump", "Bump", TEX_FLOAT, False))

    if "opacity" in maps:
        link("OCT_MATERIAL_OPACITY_LINK", tex("opacity", "Opacity", TEX_FLOAT, False))

    if "translucency" in maps:
        link("OCT_MATERIAL_TRANSMISSION_LINK", tex("translucency", "Translucency", TEX_COLOR, True))
        try:
            mat[S("UNIVMAT_TRANSMISSION_TYPE")] = 1  # diffuse transmission
        except Exception:
            pass

    if "emission" in maps:  # glowing parts (forge coal, screens): texture emission, surface brightness
        em_tex = tex("emission", "Emission", TEX_COLOR, True)
        em = c4d.BaseShader(ID_OCTANE_TEX_EMISSION)
        em.SetName("Emission")
        em[S("TEXEMISSION_EFFIC_OR_TEX")] = em_tex
        em[S("TEXEMISSION_SURFACE_BRIGHTNESS")] = True
        em[S("TEXEMISSION_POWER")] = 1.0
        mat.InsertShader(em)
        try:
            mat[S("OCT_MAT_USE_EMISSION")] = True
        except Exception:
            pass
        link("OCT_MATERIAL_EMISSION", em)

    if opts.get("displacement") and "displacement" in maps and not has_geometry:
        d = c4d.BaseShader(ID_OCTANE_DISPLACEMENT)
        d.SetName("Displacement")
        d[S("DISPLACEMENT_AMOUNT")] = 10.0
        d[S("DISPLACEMENT_MID")] = 0.5
        d[S("DISPLACEMENT_TEXTURE")] = tex("displacement", "Height", TEX_FLOAT, False)
        mat.InsertShader(d)
        link("OCT_MATERIAL_DISPLACEMENT_LINK", d)
        try:
            mat[S("OCT_MAT_USE_DISPLACEMENT")] = True
        except Exception:
            pass

    mat.Message(c4d.MSG_UPDATE)
    mat.Update(True, True)
    return mat


def _srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def apply_values(mat, values, renderer, maps):
    """Constant colour / roughness for parts that have no texture for that channel."""
    rgb = values.get("albedo_color")
    rough = values.get("roughness")
    if renderer == "octane":
        if rgb and "albedo" not in maps:
            mat[S("OCT_MATERIAL_DIFFUSE_COLOR")] = c4d.Vector(*[_srgb_to_linear(v) for v in rgb])
        if rough is not None and "roughness" not in maps and "gloss" not in maps:
            mat[S("OCT_MATERIAL_ROUGHNESS_FLOAT")] = float(rough)
    else:
        if rgb and "albedo" not in maps:
            mat[c4d.MATERIAL_COLOR_COLOR] = c4d.Vector(*[v / 255.0 for v in rgb])
    mat.Message(c4d.MSG_UPDATE)


# --------------------------------------------------------------------- Standard (no Octane)

def build_standard(maps, name):
    mat = c4d.BaseMaterial(c4d.Mmaterial)
    mat.SetName(name)

    def bmp(path, linear):
        sh = c4d.BaseShader(c4d.Xbitmap)
        sh[c4d.BITMAPSHADER_FILENAME] = path
        if linear or path.lower().endswith(FLOAT_EXT):
            try:
                sh[c4d.BITMAPSHADER_COLORPROFILE] = c4d.BITMAPSHADER_COLORPROFILE_LINEAR
            except Exception:
                pass
        mat.InsertShader(sh)
        return sh

    if "albedo" in maps:
        mat[c4d.MATERIAL_COLOR_SHADER] = bmp(maps["albedo"], False)
    if "normal" in maps:
        mat[c4d.MATERIAL_USE_NORMAL] = True
        mat[c4d.MATERIAL_NORMAL_SHADER] = bmp(maps["normal"], True)
    elif "bump" in maps:
        mat[c4d.MATERIAL_USE_BUMP] = True
        mat[c4d.MATERIAL_BUMP_SHADER] = bmp(maps["bump"], True)
    if "opacity" in maps:
        mat[c4d.MATERIAL_USE_ALPHA] = True
        mat[c4d.MATERIAL_ALPHA_SHADER] = bmp(maps["opacity"], True)
    mat.Message(c4d.MSG_UPDATE)
    mat.Update(True, True)
    return mat
