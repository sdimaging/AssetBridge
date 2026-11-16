"""QA renders: import assets through the Blender add-on and render a labelled contact sheet (Cycles).

    blender -b --factory-startup --python tools/qa_contact_sheet.py -- --library Maxtree --query "" --limit 40 \
        --out C:/path/sheet.png [--variation Var1] [--cell 320]

Each asset is imported alone (same code path as a AssetBridge send), framed from its bounding box, lit by an
HDRI from the catalog, and rendered; tiles are joined into one image with names underneath.
"""
import argparse
import math
import os
import sys

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "blender"))
os.environ.setdefault("BRIDGE2_ROOT", ROOT)
from bridge2_blender import api  # noqa: E402


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--library")
    ap.add_argument("--type")
    ap.add_argument("--query", default="")
    ap.add_argument("--ids", default="")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--variation", default="")
    ap.add_argument("--cell", type=int, default=320)
    ap.add_argument("--samples", type=int, default=24)
    ap.add_argument("--hdri", default="forest grove")
    ap.add_argument("--out", required=True)
    return ap.parse_args(argv)


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for attr in ("meshes", "materials", "images", "cameras", "pointclouds", "point_clouds"):
        for x in list(getattr(bpy.data, attr, ())):
            coll = getattr(bpy.data, attr)
            if x.users == 0:
                coll.remove(x)


def render_one(asset, a, out_png, cell, samples, hdri_id):
    clear()
    sc = bpy.context.scene
    if hdri_id:
        api.import_asset(hdri_id)
    kw = {}
    if a.variation and asset.get("variations"):
        kw["variations"] = [a.variation if a.variation in asset["variations"] else asset["variations"][0]]
    root = api.import_asset(asset["id"], resolution="2K", instance=False, **kw)
    bpy.context.view_layer.update()
    objs = [o for o in (root.children_recursive if hasattr(root, "children_recursive") else []) if o.type in ("MESH", "POINTCLOUD")]
    if not objs:
        return "no geometry"
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    c, ext = (lo + hi) / 2, hi - lo
    bpy.ops.mesh.primitive_plane_add(size=max(ext) * 10, location=(c.x, c.y, lo.z))
    g = bpy.context.active_object
    gm = bpy.data.materials.new("qa_ground")
    gm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.045, 0.04, 0.035, 1)
    g.data.materials.append(gm)
    cam = bpy.data.objects.new("qa_cam", bpy.data.cameras.new("qa_cam"))
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.data.lens = 50
    r = ext.length * 0.5
    dist = r / math.sin(math.radians(39.6 / 2)) * 1.05
    direction = Vector((0.45, -1.0, 0.32)).normalized()
    cam.location = c + direction * dist
    cam.rotation_euler = (c - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.clip_start, cam.data.clip_end = dist / 100, dist * 50
    sc.render.engine = "CYCLES"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    try:
        sc.cycles.device = "GPU"
    except Exception:
        pass
    sc.render.resolution_x = sc.render.resolution_y = cell
    sc.render.filepath = out_png
    bpy.ops.render.render(write_still=True)
    return None


def main():
    a = args()
    if a.ids:
        assets = [api.get_asset(i) for i in a.ids.split(",") if i]
    else:
        assets = api.find_assets(a.query, type=a.type, library=a.library, limit=a.limit)
        assets = [api.get_asset(x["id"]) for x in assets]
    h = api.find_assets(a.hdri, type="hdri", limit=1) if a.hdri else []
    hdri_id = h[0]["id"] if h else None
    tmp = os.path.join(os.path.dirname(a.out), "qa_tiles")
    os.makedirs(tmp, exist_ok=True)
    tiles = []
    for i, asset in enumerate(assets):
        png = os.path.join(tmp, "%03d.png" % i)
        try:
            err = render_one(asset, a, png, a.cell, a.samples, hdri_id)
        except Exception as e:
            err = "%s: %s" % (type(e).__name__, e)
        tiles.append((asset["name"], png if not err else None, err, asset.get("preview") or ""))
        print("QA %d/%d %s %s" % (i + 1, len(assets), asset["name"], err or "ok"), flush=True)
    with open(os.path.splitext(a.out)[0] + ".txt", "w", encoding="utf-8") as fh:
        for i, (name, png, err, prev) in enumerate(tiles):
            fh.write("%s\t%s\t%s\t%s\n" % (name, png or "", err or "ok", prev))
    print("QA tiles index", os.path.splitext(a.out)[0] + ".txt")


main()
