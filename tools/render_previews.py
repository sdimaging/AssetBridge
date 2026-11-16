"""Static 3D previews for models whose gallery thumbnail is only a texture map (or missing).

    blender -b --factory-startup --python tools/render_previews.py -- [--library Quixel] [--ids a,b] [--force]
        [--all] [--size 640] [--samples 48] [--limit N]

Each model is imported exactly as a AssetBridge send would (bridge2_blender.api), framed from its bounding box at a
three-quarter view, lit by Blender's bundled studio light with a transparent background and a shadow catcher,
and saved to cache/renders/<sha1(id)[:20]>.png. The server prefers that render over a texture-map preview.
Without --all only models whose preview is one of their own texture maps (or none) are rendered.
"""
import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "blender"))
os.environ.setdefault("BRIDGE2_ROOT", ROOT)
from bridge2_blender import api  # noqa: E402

RENDERS = os.path.join(ROOT, "cache", "renders")
MODEL_TYPES = ("3d", "plant", "scene")
BLENDER_MESH = {"fbx", "glb", "gltf", "abc", "usd", "usda", "usdc", "usdz", "obj", "ply", "stl"}  # Blender 5 dropped Collada


def render_path(asset_id):
    return os.path.join(RENDERS, hashlib.sha1(asset_id.encode("utf-8")).hexdigest()[:20] + ".png")


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--library")
    ap.add_argument("--ids", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--all", action="store_true", help="also models that already have a real preview image")
    ap.add_argument("--size", type=int, default=640)
    ap.add_argument("--samples", type=int, default=48)
    ap.add_argument("--limit", type=int, default=0)
    return ap.parse_args(argv)


def needs_render(d, everything):
    if d.get("type") not in MODEL_TYPES:
        return False
    if not any(m.get("format") in BLENDER_MESH for m in d.get("meshes", [])):
        return False
    pv = d.get("preview")
    if everything or not pv:
        return True
    maps = {os.path.normcase(m.get("path", "")) for m in d.get("maps", [])}
    for ms in (d.get("material_sets") or {}).values():
        maps |= {os.path.normcase(m.get("path", "")) for m in (ms if isinstance(ms, list) else [])}
    return os.path.normcase(pv) in maps


def _stale(png, d):
    """The model (any mesh / variation file) was saved after its render: render again."""
    try:
        t = os.path.getmtime(png)
        return any(os.path.exists(m.get("path", "")) and os.path.getmtime(m["path"]) > t for m in d.get("meshes", []))
    except OSError:
        return False


def targets(a):
    db = sqlite3.connect("file:%s?mode=ro" % os.path.join(ROOT, "cache", "bridge2.db"), uri=True)
    rows = db.execute("SELECT id, library, data FROM assets").fetchall()
    want = {i for i in a.ids.split(",") if i}
    out = []
    for i, lib, data in rows:
        if want and i not in want:
            continue
        if a.library and lib != a.library:
            continue
        d = json.loads(data)
        if not want and not needs_render(d, a.all):
            continue
        if not a.force and os.path.exists(render_path(i)) and not _stale(render_path(i), d):
            continue
        out.append(d)
    return out[:a.limit] if a.limit else out


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for attr in ("meshes", "materials", "images", "cameras", "lights", "worlds"):
        for x in list(getattr(bpy.data, attr, ())):
            if x.users == 0:
                getattr(bpy.data, attr).remove(x)


def setup_scene(size, samples):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    try:
        sc.cycles.device = "GPU"
    except Exception:
        pass
    sc.render.film_transparent = True
    sc.render.resolution_x = sc.render.resolution_y = size
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    try:
        sc.view_settings.view_transform = "AgX"
    except TypeError:
        sc.view_settings.view_transform = "Filmic"
    w = bpy.data.worlds.new("assetbridge_studio")
    w.use_nodes = True
    nt = w.node_tree
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    studio = os.path.join(bpy.utils.system_resource("DATAFILES", path="studiolights/world"), "studio.exr")
    if os.path.exists(studio):
        env.image = bpy.data.images.load(studio, check_existing=True)
        nt.links.new(env.outputs["Color"], nt.nodes["Background"].inputs["Color"])
    nt.nodes["Background"].inputs["Strength"].default_value = 1.2
    sc.world = w


def lowest_lod(d):
    lods = d.get("lods") or []
    return lods[min(1, len(lods) - 1)] if lods else None  # LOD1 when it exists: same look, much faster


def render_one(d, size):
    sc = bpy.context.scene
    clear()
    kw = {}
    if d.get("variations"):
        kw["variations"] = [d["variations"][0]]
    lod = lowest_lod(d)
    if lod:
        kw["lod"] = lod
    root = api.import_asset(d["id"], resolution="2K", instance=False, **kw)
    bpy.context.view_layer.update()
    objs = [o for o in root.children_recursive if o.type == "MESH"]
    if not objs:
        return "no geometry"
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    c, ext = (lo + hi) / 2, hi - lo
    bpy.ops.mesh.primitive_plane_add(size=max(ext) * 8, location=(c.x, c.y, lo.z))
    ground = bpy.context.active_object
    ground.is_shadow_catcher = True
    cam = bpy.data.objects.new("assetbridge_cam", bpy.data.cameras.new("assetbridge_cam"))
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.data.lens = 60
    fov = 2 * math.atan(cam.data.sensor_width / (2 * cam.data.lens))
    r = ext.length * 0.5
    direction = Vector((0.62, -1.0, 0.42)).normalized()
    dist = r / math.sin(fov / 2) * 1.02
    cam.location = c + direction * dist
    cam.rotation_euler = (c - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.clip_start, cam.data.clip_end = dist / 200, dist * 20
    os.makedirs(RENDERS, exist_ok=True)
    out = render_path(d["id"])
    sc.render.filepath = out[:-4] + ".part.png"
    bpy.ops.render.render(write_still=True)
    os.replace(out[:-4] + ".part.png", out)
    return None


def main():
    a = args()
    todo = targets(a)
    print("PREVIEW %d model(s) to render" % len(todo), flush=True)
    setup_scene(a.size, a.samples)
    for n, d in enumerate(todo):
        if not a.force and os.path.exists(render_path(d["id"])) and not _stale(render_path(d["id"]), d):
            continue  # another run got there first
        try:
            err = render_one(d, a.size)
        except Exception as e:
            err = "%s: %s" % (type(e).__name__, e)
        print("PREVIEW %d/%d %s %s" % (n + 1, len(todo), d["name"], err or "ok"), flush=True)


main()
