"""Scripting API — works in background Blender (`blender -b --python script.py`), no server needed.

    from bridge2_blender import api
    hits = api.find_assets("barnacle rock", type="3d")
    root = api.import_asset(hits[0]["id"], lod="LOD2", resolution="2K")       # layout
    root = api.import_asset(asset_id, lod="LOD0", resolution="8K", displacement=True)  # finals
    api.import_asset(hdri_id)                                                 # sets the world

Assets come from the browser's catalog (cache/bridge2.db, read-only). Every imported object carries
custom properties: bridge_asset_id, bridge_library, bridge_name, bridge_type, bridge_lod,
bridge_resolution, bridge_variation, bridge_source — so a layout can be rebuilt elsewhere
(e.g. C4D/Octane via the Bridge server's /api/send) from the same asset.
"""
import json
import os
import sqlite3
import sys

from . import core

_DEFAULT_ROOT = ""  # set by config.json (installer) or the BRIDGE2_ROOT environment variable


def bridge_root():
    root = os.environ.get("BRIDGE2_ROOT")
    if not root:
        cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        try:
            with open(cfg, "r", encoding="utf-8") as fh:
                root = json.load(fh).get("root")
        except (OSError, ValueError):
            root = None
    return root or _DEFAULT_ROOT


def _send_module():
    server = os.path.join(bridge_root(), "server")
    if server not in sys.path:
        sys.path.insert(0, server)
    import send  # Bridge's own payload resolver, so Blender and C4D pick the same files
    return send


def _db():
    path = os.path.join(bridge_root(), "cache", "bridge2.db")
    if not os.path.exists(path):
        raise RuntimeError("Asset catalog not found at %s — open the browser once to index libraries" % path)
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)


def get_asset(asset_id):
    """Full catalog record (maps, meshes, lods, variations, resolutions...)."""
    with _db() as db:
        row = db.execute("SELECT data FROM assets WHERE id=?", (asset_id,)).fetchone()
    if not row:
        raise KeyError("Unknown Bridge asset id: %s" % asset_id)
    return json.loads(row[0])


def find_assets(query="", type=None, library=None, limit=50):
    """Search the catalog. Returns light dicts: id, name, type, library, lods, resolutions, variations, size."""
    where, args, join = [], [], ""
    if query:
        words = [w.replace('"', "") for w in query.split() if w.strip('"')]
        join = " JOIN assets_fts f ON f.id=a.id"
        where.append("assets_fts MATCH ?")
        args.append(" AND ".join('"%s"*' % w for w in words))
    if type:
        where.append("a.type=?")
        args.append(type)
    if library:
        where.append("a.library=?")
        args.append(library)
    sql = "SELECT a.data FROM assets a" + join + (" WHERE " + " AND ".join(where) if where else "") + \
          (" ORDER BY bm25(assets_fts)" if query else " ORDER BY a.name") + " LIMIT %d" % int(limit)
    with _db() as db:
        rows = db.execute(sql, args).fetchall()
    out = []
    for (data,) in rows:
        d = json.loads(data)
        out.append({k: d.get(k) for k in ("id", "name", "type", "library", "lods", "resolutions",
                                          "variations", "size", "path", "map_types")})
    return out


def build_payload(asset_id, lod=None, resolution="4K", displacement=False, ao=True, variations=None,
                  replace_environment=True, drop_to_ground=False, splat_mode="both", real_scale=False,
                  instance=True):
    opts = {"lod": lod or "LOD0", "resolution": resolution, "displacement": displacement, "ao": ao,
            "replace_environment": replace_environment, "apply_to_selection": False,
            "splat_mode": splat_mode, "real_scale": real_scale, "instance": instance}
    if variations:
        opts["variations"] = ["all"] if variations == "all" else list(variations)
    payload = _send_module().build_payload(get_asset(asset_id), opts, target="blender", converter=_convert)
    payload["options"]["drop_to_ground"] = bool(drop_to_ground)
    return payload


def _convert(src, out_fmt, scale=1.0):
    """Same conversion (and cache) the asset server uses, run locally for headless scripts."""
    server = os.path.join(bridge_root(), "server")
    if server not in sys.path:
        sys.path.insert(0, server)
    import bridge2
    if bridge2.APP is None:
        import jobs
        bridge2.APP = bridge2.App.__new__(bridge2.App)  # just what convert_splat needs: config + the job registry
        bridge2.APP.cfg = bridge2.load_config()
        bridge2.APP.jobs = jobs.Jobs()
    return bridge2.APP.convert_splat(src, out_fmt, scale=scale)


def import_asset(asset_id, lod=None, resolution="4K", displacement=False, ao=True, variations=None,
                 collection=None, location=None, replace_environment=True, drop_to_ground=False,
                 splat_mode="both", real_scale=False, instance=True):
    """Import a Bridge asset into the current Blender scene.

    lod:          "LOD0".."LOD8" / "High" (Megascans). Falls back to the nearest available.
    resolution:   "1K".."16K"; picks the nearest available at or below.
    displacement: wire the height map into Material Output > Displacement (method BOTH, for Cycles).
    variations:   plants: ["Var1","Var3"] or "all" (default: first variation).
    collection:   bpy.types.Collection to link into (default: active collection).
    location:     (x, y, z) for the root Empty.
    drop_to_ground: move the asset so its lowest point sits at location z (off = keep the file's own origin).
    splat_mode:   splats only: "both" (splat + proxy as viewport wire), "splat", or "proxy".
    real_scale:   splats only: bake real-world size into a cached copy (positions AND splat sizes) when the
                  library has real_scale.json. Off = the file's own size, identical to a manual load.
    instance:     when this asset with the same settings is already in the scene, add a linked duplicate
                  (shares mesh/splat data) instead of importing again. location= places it.
    Returns the root Empty (geometry), Material (surfaces/atlases) or World (HDRIs).
    """
    payload = build_payload(asset_id, lod, resolution, displacement, ao, variations, replace_environment,
                            drop_to_ground, splat_mode, real_scale, instance)
    if payload["asset"]["type"] in ("3d", "plant") and not payload["geometry"]:
        raise RuntimeError("%s has no mesh file Blender can import" % payload["asset"]["name"])
    _result, created = core.run(payload, collection=collection, location=location)
    return created
