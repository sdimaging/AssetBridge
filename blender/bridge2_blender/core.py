"""Asset → Blender import: geometry, Principled BSDF materials (Cycles/EEVEE), HDRI worlds.

Takes the same payload the C4D receiver gets (server/send.py build_payload, target="blender").
Must run on Blender's main thread.
"""
import os
import re

import bpy

PROP_ID = "bridge_asset_id"
HDRI_PREFIX = "HDRI · "
IMPORTERS = {
    "fbx": lambda p: bpy.ops.import_scene.fbx(filepath=p),
    "glb": lambda p: bpy.ops.import_scene.gltf(filepath=p),
    "gltf": lambda p: bpy.ops.import_scene.gltf(filepath=p),
    "obj": lambda p: bpy.ops.wm.obj_import(filepath=p),
    "abc": lambda p: bpy.ops.wm.alembic_import(filepath=p, as_background_job=False),
    "usd": lambda p: bpy.ops.wm.usd_import(filepath=p),
    "usda": lambda p: bpy.ops.wm.usd_import(filepath=p),
    "usdc": lambda p: bpy.ops.wm.usd_import(filepath=p),
    "usdz": lambda p: bpy.ops.wm.usd_import(filepath=p),
    "stl": lambda p: bpy.ops.wm.stl_import(filepath=p),
    "ply": lambda p: bpy.ops.wm.ply_import(filepath=p),
    "dae": lambda p: bpy.ops.wm.collada_import(filepath=p),
}
# displacement height (Blender units = metres) when the asset doesn't say
DISP_SCALE = {"surface": 0.1, "atlas": 0.02, "decal": 0.01, "3d": 0.02, "plant": 0.01}


def run(payload, collection=None, location=None):
    """Import one payload. Returns (result_dict, created) — created is the root Empty for geometry,
    the Material for a texture-only asset, or the World for an HDRI."""
    asset = payload["asset"]
    opts = payload.get("options", {})
    maps = {k: v for k, v in (payload.get("maps") or {}).items() if v and os.path.exists(v)}
    sets = {k: {t: p for t, p in v.items() if os.path.exists(p)}
            for k, v in (payload.get("material_sets") or {}).items()}
    name = asset.get("name") or "Asset"

    if asset.get("type") == "hdri":
        world = _hdri(maps, name, opts, asset)
        return {"ok": True, "message": "World environment: " + name, "object": world.name}, world
    sig = payload.get("signature")
    if opts.get("instance", True) and sig and (asset.get("type") == "splat" or payload.get("geometry")):
        src = find_stamped_root(sig)
        if src is not None:
            return linked_instance(src, name, collection, location)
    if asset.get("type") == "splat":
        return _splat(payload, name, collection, location)

    coll = collection or _active_collection()
    selection = _selected_meshes()
    mats_before = set(bpy.data.materials)
    notes = []
    root = None
    mat = None

    geometry = payload.get("geometry") or []
    if geometry:
        parts = []
        for g in geometry:
            objs = _import_file(g["path"], g["format"], coll)
            if g.get("object"):  # vendor file holding every variation side by side: keep one
                objs = _keep_object(objs, g["object"], notes)
            label = g.get("variation") or os.path.splitext(os.path.basename(g["path"]))[0]
            parts.append((label, objs, g))
        root = _empty(name, coll)
        if len(parts) == 1:
            _parent_tops(parts[0][1], root)
        else:  # plant variations side by side, like the C4D import
            bpy.context.view_layer.update()
            x = 0.0
            for label, objs, _ in parts:
                sub = _empty(label, coll)
                sub.parent = root
                _parent_tops(objs, sub)
                bpy.context.view_layer.update()
                w = _width(objs)
                sub.location.x = x + w / 2
                x += w * 1.15
        all_objs = [o for _, objs, _ in parts for o in objs]
        meshes = [o for o in all_objs if o.type == "MESH"]

        if sets:
            if maps:
                sets[name] = maps  # the model's own set is just another part
            values = payload.get("material_values") or {}
            for k in values:
                sets.setdefault(k, {})
            opts = dict(opts, _values=values, _aliases=payload.get("material_aliases") or {})
            n, unmatched = _apply_sets(meshes, sets, name, asset, opts)
            notes.append("%d part material(s)" % n)
            if unmatched:
                notes.append("kept file material(s): " + ", ".join(unmatched[:4]))
        elif maps:
            mat = build_material(name_for(name, opts), maps, asset, opts)
            for o in meshes:
                _assign_only(o, mat)

        for label, objs, g in parts:
            for o in objs + [root]:
                _tag(o, asset, opts, g)
        root["bridge_sig"] = payload.get("signature") or ""
        if location is not None:
            root.location = location
        if opts.get("drop_to_ground"):  # some files (e.g. Poly Haven fir_tree_01) sit metres above their origin
            bpy.context.view_layer.update()
            zs = [(o.matrix_world @ _vec(c)).z for o in meshes for c in o.bound_box]
            if zs:
                root.location.z -= min(zs) - (location[2] if location is not None else 0.0)
        _select_only(root)
    elif maps:
        mat = build_material(name_for(name, opts), maps, asset, opts)
        if opts.get("apply_to_selection", True) and selection:
            for o in selection:
                _assign_only(o, mat)
            notes.append("applied to %d selected object(s)" % len(selection))

    _purge_orphans(mats_before)
    what = []
    if root is not None:
        what.append("geometry")
    if mat is not None:
        what.append("Principled material")
    msg = "Imported %s into Blender: %s%s" % (name, ", ".join(what) or "file materials",
                                              (" (" + "; ".join(notes) + ")") if notes else "")
    created = root if root is not None else mat
    return {"ok": True, "message": msg, "object": root.name if root else None,
            "material": mat.name if mat else None}, created


def name_for(name, opts):
    return "%s [%s]" % (name, opts["resolution"]) if opts.get("resolution") else name


# ------------------------------------------------------------------ geometry

def _active_collection():
    ctx = bpy.context
    try:
        return ctx.view_layer.active_layer_collection.collection
    except AttributeError:
        return ctx.scene.collection


def _selected_meshes():
    try:
        return [o for o in bpy.context.selected_objects if o.type == "MESH"]
    except AttributeError:  # background mode with no window context
        return []


def _import_file(path, fmt, coll):
    imp = IMPORTERS.get(fmt)
    if imp is None:
        raise RuntimeError("Blender has no importer for .%s" % fmt)
    before = set(bpy.data.objects)
    result = imp(path)
    if "FINISHED" not in result:
        raise RuntimeError("Blender could not import %s" % os.path.basename(path))
    new = [o for o in bpy.data.objects if o not in before]
    if not new:
        raise RuntimeError("Nothing was imported from %s" % os.path.basename(path))
    for o in new:  # move into the target collection
        for c in list(o.users_collection):
            if c != coll:
                c.objects.unlink(o)
        if coll not in o.users_collection:
            coll.objects.link(o)
    _drop_missing_images(new)
    return new


def _drop_missing_images(objs):
    """File materials pointing at textures that don't exist render error-pink: unplug those image nodes."""
    seen = set()
    for o in objs:
        for slot in getattr(o, "material_slots", []):
            m = slot.material
            if not m or m.name in seen or not m.use_nodes:
                continue
            seen.add(m.name)
            for n in list(m.node_tree.nodes):
                if n.type != "TEX_IMAGE":
                    continue
                img = n.image
                ok = img is not None and (img.packed_file is not None or os.path.exists(bpy.path.abspath(img.filepath)))
                if not ok:
                    for out in n.outputs:
                        for link in list(out.links):
                            m.node_tree.links.remove(link)


def _keep_object(objs, want, notes):
    """Keep only the object named `want` (FBX importers may suffix .001), at the origin in X/Y, world transform kept."""
    keep = next((o for o in objs if o.name == want), None) or next((o for o in objs if o.name.split(".")[0] == want), None)
    if keep is None:
        notes.append("object %s not found, kept the whole file" % want)
        return objs
    mw = keep.matrix_world.copy()
    keep.parent = None
    mine = [keep] + list(keep.children_recursive)
    names = {o.name for o in mine}
    for o in [o for o in objs if o.name not in names]:
        bpy.data.objects.remove(o, do_unlink=True)
    mw.translation.x = mw.translation.y = 0.0
    keep.matrix_world = mw
    return mine


def _empty(name, coll):
    e = bpy.data.objects.new(name, None)
    e.empty_display_type = "PLAIN_AXES"
    e.empty_display_size = 0.25
    coll.objects.link(e)
    return e


def _parent_tops(objs, parent):
    s = set(objs)
    for o in objs:
        if o.parent is None or o.parent not in s:
            mw = o.matrix_world.copy()
            o.parent = parent
            o.matrix_world = mw


def _width(objs):
    xs = []
    for o in objs:
        if o.type in ("MESH", "POINTCLOUD"):
            for c in o.bound_box:
                xs.append((o.matrix_world @ _vec(c)).x)
    return (max(xs) - min(xs)) if xs else 1.0


def _vec(c):
    from mathutils import Vector
    return Vector(c)


def _select_only(obj):
    try:
        for o in bpy.context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
    except (AttributeError, RuntimeError):
        pass


def _tag(obj, asset, opts, g):
    obj[PROP_ID] = asset.get("id") or ""
    obj["bridge_library"] = asset.get("library") or ""
    obj["bridge_name"] = asset.get("name") or ""
    obj["bridge_type"] = asset.get("type") or ""
    obj["bridge_lod"] = (g or {}).get("lod") or opts.get("lod") or ""
    obj["bridge_resolution"] = opts.get("resolution") or ""
    if (g or {}).get("variation"):
        obj["bridge_variation"] = g["variation"]
    obj["bridge_source"] = (g or {}).get("path") or asset.get("path") or ""


def _assign_only(obj, mat):
    if obj.type != "MESH":
        return
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def _purge_orphans(before):
    """Drop only the file materials this import created and then replaced — never the user's own."""
    for m in [m for m in bpy.data.materials if m not in before and m.users == 0 and not m.use_fake_user
              and not m.get("bridge_built")]:  # keep ours: a surface's material has no users yet
        bpy.data.materials.remove(m)


# ------------------------------------------------------------------ multi-part

def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _apply_sets(meshes, sets, name, asset, opts):
    built, unmatched = {}, []
    for o in meshes:
        used = {p.material_index for p in o.data.polygons}
        for si, slot in enumerate(o.material_slots):
            if si not in used:  # file material on no faces (e.g. empty Multi/Sub slots)
                slot.material = None
                continue
            m = slot.material
            if m is None:
                continue
            nm = _norm(m.name.split(".")[0])
            best, best_len = None, 0
            alias = (opts.get("_aliases") or {}).get(m.name.split(".")[0])
            if alias in sets:  # explicit mapping from the converter (vendor names like "trunk1", "leaf2_F")
                best = alias
            for part in ([] if best else sets):
                np_ = _norm(part)
                hit = len(np_) if np_ and np_ in nm else (len(nm) if len(nm) >= 3 and nm in np_ else 0)
                if hit > best_len:
                    best, best_len = part, hit
            if best is None and len(sets) == 1:
                best = next(iter(sets))
            if best is None or (not sets[best] and best not in opts.get("_values", {})):
                if m.name not in unmatched:
                    unmatched.append(m.name)
                continue
            if best not in built:
                label = name if _norm(best) == _norm(name) else "%s · %s" % (name, best)
                built[best] = build_material(name_for(label, opts), sets[best], asset, opts,
                                             values=opts.get("_values", {}).get(best))
            slot.material = built[best]
    return len(built), unmatched


# ------------------------------------------------------------------ materials

# Texture colour spaces by role, resolved against the OCIO config Blender runs with (its own, or an ACES / studio
# config through $OCIO): the first name the config knows wins. Hard-coding "sRGB" / "Non-Color" breaks imports
# under configs that don't have those names.
CS_NAMES = {
    "color": ("sRGB", "sRGB - Texture", "Utility - sRGB - Texture", "srgb_texture", "Input - Generic - sRGB - Texture",
              "sRGB Encoded Rec.709 (sRGB)"),
    "linear": ("Linear Rec.709", "Linear Rec.709 (sRGB)", "Utility - Linear - sRGB", "lin_srgb", "Linear"),
    "data": ("Non-Color", "Raw", "Utility - Raw", "raw", "Data"),
}
FLOAT_EXT = (".exr", ".hdr")
_cs_found = {}


def set_colorspace(img, role):
    """Give img the colour space for role "color" (an 8/16-bit sRGB texture), "linear" (a float EXR/HDR colour
    map) or "data" (normal, roughness, displacement ...). Returns the name used. When the config has none of the
    names, data maps are flagged is_data (works under any config) and colour maps keep Blender's default for the
    file type, which is already right for sRGB 8-bit and linear float files."""
    settings = img.colorspace_settings
    known = _cs_found.get(role)
    for name in ((known,) if known else ()) + tuple(n for n in CS_NAMES[role] if n != known):
        try:
            settings.name = name
        except TypeError:  # not in this OCIO config
            continue
        _cs_found[role] = name
        return name
    if role == "data":
        settings.is_data = True
    return None


def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def build_material(mat_name, maps, asset, opts, values=None):
    existing = bpy.data.materials.get(mat_name)
    if existing is not None and existing.get("bridge_built") and \
            bool(existing.get("bridge_disp")) == bool(opts.get("displacement")):
        return existing
    mat = bpy.data.materials.new(mat_name)
    try:
        mat.use_nodes = True  # always on in 5.x; harmless there
    except AttributeError:
        pass
    nt = mat.node_tree
    nt.nodes.clear()
    N, L = nt.nodes, nt.links

    out = N.new("ShaderNodeOutputMaterial")
    out.location = (700, 0)
    bsdf = N.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (250, 0)
    values = values or {}
    if values.get("albedo_color") and "albedo" not in maps:
        bsdf.inputs["Base Color"].default_value = [_lin(v) for v in values["albedo_color"]] + [1.0]
    if values.get("roughness") is not None and "roughness" not in maps and "gloss" not in maps:
        bsdf.inputs["Roughness"].default_value = float(values["roughness"])
    surface = bsdf.outputs["BSDF"]
    row = [300]

    def tex(kind, color):
        path = maps.get(kind)
        if not path:
            return None
        n = N.new("ShaderNodeTexImage")
        n.label = kind.capitalize()
        n.image = bpy.data.images.load(path, check_existing=True)
        set_colorspace(n.image, "data" if not color else "linear" if path.lower().endswith(FLOAT_EXT) else "color")
        n.location = (-700, row[0])
        row[0] -= 290
        return n

    albedo = tex("albedo", True)
    if albedo is not None:
        col = albedo.outputs["Color"]
        if opts.get("ao", True) and "ao" in maps and "metalness" not in maps:
            ao = tex("ao", False)
            mix = N.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.blend_type = "MULTIPLY"
            mix.label = "Albedo × AO"
            mix.location = (-250, 300)
            mix.inputs["Factor"].default_value = 1.0
            L.new(col, mix.inputs[6])
            L.new(ao.outputs["Color"], mix.inputs[7])
            col = mix.outputs[2]
        L.new(col, bsdf.inputs["Base Color"])

    if "roughness" in maps:
        L.new(tex("roughness", False).outputs["Color"], bsdf.inputs["Roughness"])
    elif "gloss" in maps:
        inv = N.new("ShaderNodeInvert")
        inv.location = (-250, 0)
        L.new(tex("gloss", False).outputs["Color"], inv.inputs["Color"])
        L.new(inv.outputs["Color"], bsdf.inputs["Roughness"])

    if "metalness" in maps:
        L.new(tex("metalness", False).outputs["Color"], bsdf.inputs["Metallic"])

    if "normal" in maps:
        nm = N.new("ShaderNodeNormalMap")
        nm.location = (-250, -300)
        L.new(tex("normal", False).outputs["Color"], nm.inputs["Color"])
        L.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    elif "bump" in maps:
        bump = N.new("ShaderNodeBump")
        bump.location = (-250, -300)
        bump.inputs["Strength"].default_value = 0.5
        L.new(tex("bump", False).outputs["Color"], bump.inputs["Height"])
        L.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    alpha = None
    if "opacity" in maps:
        alpha = tex("opacity", False).outputs["Color"]
        _set(mat, "blend_method", "HASHED")
        _set(mat, "surface_render_method", "DITHERED")

    if "translucency" in maps:  # foliage: light through the leaves
        tr = N.new("ShaderNodeBsdfTranslucent")
        tr.location = (250, -650)
        L.new(tex("translucency", True).outputs["Color"], tr.inputs["Color"])
        mix = N.new("ShaderNodeMixShader")
        mix.location = (480, -200)
        mix.inputs["Fac"].default_value = 0.3
        L.new(surface, mix.inputs[1])
        L.new(tr.outputs["BSDF"], mix.inputs[2])
        surface = mix.outputs["Shader"]

    if alpha is not None:
        if "translucency" in maps:  # cut out after the mix so translucent areas still respect alpha
            tp = N.new("ShaderNodeBsdfTransparent")
            tp.location = (480, -500)
            cut = N.new("ShaderNodeMixShader")
            cut.location = (620, -150)
            L.new(alpha, cut.inputs["Fac"])
            L.new(tp.outputs["BSDF"], cut.inputs[1])
            L.new(surface, cut.inputs[2])
            surface = cut.outputs["Shader"]
        else:
            L.new(alpha, bsdf.inputs["Alpha"])

    if "emission" in maps:
        L.new(tex("emission", True).outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = 1.0

    L.new(surface, out.inputs["Surface"])

    if opts.get("displacement") and "displacement" in maps:
        d = N.new("ShaderNodeDisplacement")
        d.location = (450, -450)
        d.inputs["Midlevel"].default_value = 0.5
        d.inputs["Scale"].default_value = _disp_scale(asset)
        L.new(tex("displacement", False).outputs["Color"], d.inputs["Height"])
        L.new(d.outputs["Displacement"], out.inputs["Displacement"])
        _set(mat, "displacement_method", "BOTH")  # true displacement in Cycles (needs subdivided geometry)

    mat["bridge_built"] = True
    mat["bridge_disp"] = bool(opts.get("displacement"))
    mat[PROP_ID] = asset.get("id") or ""
    return mat


def _disp_scale(asset):
    size = str(asset.get("size") or "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*m\b", size)
    if asset.get("type") == "surface" and m:
        return max(0.02, min(0.25, float(m.group(1)) * 0.05))  # ~5% of the scanned patch size
    return DISP_SCALE.get(asset.get("type"), 0.05)


def _set(obj, attr, value):
    try:
        setattr(obj, attr, value)
    except (AttributeError, TypeError):
        pass


# ------------------------------------------------------------------ splats

def _splat(payload, name, collection=None, location=None):
    splat, proxy = payload.get("splat"), payload.get("proxy")
    asset, opts = payload["asset"], payload.get("options", {})
    coll = collection or _active_collection()
    if splat and not hasattr(bpy.ops.wm, "spz_import"):
        if not proxy:
            raise RuntimeError("This Blender has no Gaussian-splat import (needs Blender 5.3+)")
        splat = None
    root = _empty(name, coll)
    parts = []
    if splat:
        # a layered asset (e.g. a volume layer + a star-field layer) brings one splat object per layer, side by side
        layers = payload.get("splats") or [splat]
        for sp in layers:
            objs = _import_with(lambda p=sp["path"]: bpy.ops.wm.spz_import(filepath=p), sp["path"], coll)
            for o in objs:
                o.name = "%s · %s" % (name, sp.get("layer") or "Splat")
            _parent_tops(objs, root)
        parts.append("splat" if len(layers) == 1 else "%d splats (%s)" % (
            len(layers), " + ".join(sp.get("layer") or "?" for sp in layers)))
    if proxy:
        objs = _import_file(proxy["path"], proxy["format"], coll)
        holder = _empty(name + " · Proxy", coll)
        holder.parent = root
        sc = float(payload.get("proxy_scale") or payload.get("scale") or 1.0)  # splat file is pre-scaled
        holder.scale = (sc, sc, sc)
        _parent_tops(objs, holder)
        for o in objs:
            if o.type == "MESH":
                o.name = name + " · Proxy mesh"
                if splat:  # stand-in for layout / physics; the splat renders
                    o.hide_render = True
                    o.display_type = "WIRE"
        parts.append("proxy" + (" (viewport wire)" if splat else ""))
    sc = float(payload.get("proxy_scale") or payload.get("scale") or 1.0)
    if location is not None:
        root.location = location
    for o in [root] + list(root.children_recursive):
        _tag(o, asset, {"lod": "proxy" if not splat else "splat", "resolution": ""}, None)
        o["bridge_splat_mode"] = opts.get("splat_mode", "both")
    root["bridge_real_scale"] = sc
    root["bridge_sig"] = payload.get("signature") or ""
    _select_only(root)
    note = "" if abs(sc - 1) < 1e-6 else ", real scale x%.3g" % sc
    return {"ok": True, "message": "Imported %s into Blender: %s%s" % (name, " + ".join(parts), note),
            "object": root.name}, root


def _import_with(op, path, coll):
    before = set(bpy.data.objects)
    result = op()
    if "FINISHED" not in result:
        raise RuntimeError("Blender could not import %s" % os.path.basename(path))
    new = [o for o in bpy.data.objects if o not in before]
    if not new:
        raise RuntimeError("Nothing was imported from %s" % os.path.basename(path))
    for o in new:
        for c in list(o.users_collection):
            if c != coll:
                c.objects.unlink(o)
        if coll not in o.users_collection:
            coll.objects.link(o)
    return new


# ------------------------------------------------------------------ instances

def find_stamped_root(sig):
    for o in bpy.data.objects:
        if o.get("bridge_sig") == sig and not o.get("bridge_instance_of") and o.users_scene:
            return o
    return None


def linked_instance(src, name, collection=None, location=None):
    """Linked duplicate of the whole import hierarchy: new objects, shared mesh/splat/material data."""
    coll = collection or _active_collection()
    n = sum(1 for o in bpy.data.objects if o.get("bridge_instance_of") == src.name) + 1

    def dup(o, parent):
        c = o.copy()  # Object copy shares o.data: a linked duplicate
        coll.objects.link(c)
        if parent is not None:
            c.parent = parent
            c.matrix_parent_inverse = o.matrix_parent_inverse.copy()
        for ch in o.children:
            dup(ch, c)
        return c

    new = dup(src, None)
    new.name = "%s · Instance %d" % (name, n)
    new["bridge_instance_of"] = src.name
    bpy.context.view_layer.update()
    meshes = [o for o in src.children_recursive if o.type in ("MESH", "POINTCLOUD")]
    w = max(_width(meshes), 0.1) if meshes else 1.0
    new.matrix_world = src.matrix_world.copy()
    if location is not None:
        new.location = location
    else:
        new.location.x += w * 1.15 * n
    _select_only(new)
    return {"ok": True, "message": "Added linked duplicate #%d of %s (already in the scene)" % (n, name),
            "object": new.name}, new


# ------------------------------------------------------------------ HDRI

def _hdri(maps, name, opts, asset):
    path = maps.get("hdri")
    if not path:
        raise RuntimeError("HDRI file missing")
    scene = bpy.context.scene
    world = scene.world
    if not (opts.get("replace_environment", True) and world is not None and world.name.startswith(HDRI_PREFIX)):
        world = bpy.data.worlds.new(HDRI_PREFIX + name)
    world.name = HDRI_PREFIX + name
    try:
        world.use_nodes = True
    except AttributeError:
        pass
    nt = world.node_tree
    nt.nodes.clear()
    coord = nt.nodes.new("ShaderNodeTexCoord")
    coord.location = (-900, 0)
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.location = (-700, 0)
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    env.location = (-450, 0)
    env.image = bpy.data.images.load(path, check_existing=True)
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.location = (-150, 0)
    bg.inputs["Strength"].default_value = 1.0
    wout = nt.nodes.new("ShaderNodeOutputWorld")
    wout.location = (100, 0)
    nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], wout.inputs["Surface"])
    world[PROP_ID] = asset.get("id") or ""
    scene.world = world
    return world
