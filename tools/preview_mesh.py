"""Convert a USD or Alembic file to a GLB for the browser's 3D preview.

    blender -b --factory-startup --python-exit-code 1 --python tools/preview_mesh.py -- <in.usd|.abc> <out.glb>

The server runs this (App.preview_mesh) the first time a USD/Alembic asset is opened in 3D and caches the result
in cache/preview_mesh/. Object, armature and shape-key animation is kept so the preview can play it; textures
are capped at 2K because this is only a preview. The output appears atomically (written to a temp name, then
renamed), so a half-written GLB is never served.
"""
import os
import sys

import bpy

MAX_TEX = 2048


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) < 2:
        raise SystemExit("usage: blender -b --python preview_mesh.py -- <in> <out.glb>")
    src, out = argv[0], argv[1]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ext = src.rsplit(".", 1)[-1].lower()
    if ext in ("usd", "usda", "usdc", "usdz"):
        bpy.ops.wm.usd_import(filepath=src)
    elif ext == "abc":
        bpy.ops.wm.alembic_import(filepath=src, as_background_job=False)
    else:
        raise SystemExit("not a USD or Alembic file: " + src)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit("no meshes in " + src)
    baked = bake_transform_caches()
    for img in bpy.data.images:
        try:
            w, h = img.size
            if max(w, h) > MAX_TEX:
                k = MAX_TEX / max(w, h)
                img.scale(max(1, int(w * k)), max(1, int(h * k)))
        except (RuntimeError, ValueError):
            pass  # unreadable or procedural image: the exporter skips it
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = out[:-4] + ".part.glb"
    bpy.ops.export_scene.gltf(filepath=tmp, export_format="GLB", use_selection=False, export_animations=True,
                              export_cameras=False, export_lights=False, export_apply=False)
    os.replace(tmp, out)
    tris = sum(len(p.vertices) - 2 for o in meshes for p in o.data.polygons)
    print("PREVIEW_MESH %s -> %s (%d meshes, %d triangles, %d animated, %.1f MB)" % (
        os.path.basename(src), os.path.basename(out), len(meshes), tris, baked, os.path.getsize(out) / 1e6))


def bake_transform_caches():
    """USD and Alembic transforms arrive as Transform Cache constraints, which glTF can't carry: bake them to
    keyframes over the file's frame range (the importers set it). Point-cache deformation (a mesh whose vertices
    move) stays on its first frame. Returns how many objects were baked."""
    sc = bpy.context.scene
    obs = [o for o in sc.objects if any(c.type == "TRANSFORM_CACHE" for c in o.constraints)]
    frames = sc.frame_end - sc.frame_start + 1
    if not obs or frames < 2:
        return 0
    bpy.ops.object.select_all(action="DESELECT")
    for o in obs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = obs[0]
    step = max(1, -(-frames * len(obs) // 100000))  # keep very long or very busy caches to ~100k keys
    bpy.ops.nla.bake(frame_start=sc.frame_start, frame_end=sc.frame_end, step=step, only_selected=True,
                     visual_keying=True, clear_constraints=True, bake_types={"OBJECT"})
    return len(obs)


main()
