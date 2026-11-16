"""Tests for server/fab.py (the remote Fab library).   py -3.13 tools/test_fab.py [--live]
Offline by default (a synthetic index + zip in a temp folder). --live also pulls one real asset at Low quality
(~7 MB) from Fab into a temp library and evicts it again. Exit code 0 = all pass."""
import json
import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
import fab  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(detail)))
    if not cond:
        failures.append(name)


tmp = tempfile.mkdtemp(prefix="assetbridge_fab_test_")
try:
    # 1. listing type -> AssetBridge type
    T = lambda lt, cat: fab.assetbridge_type({"listing_type": lt, "category_path": cat})
    check("3d model", T("3d-model", "tools-objects-decor") == "3d")
    check("plant by category", T("3d-model", "nature-plants/plants") == "plant" and T("3d-model", "nature-plants/trees") == "plant")
    check("rocks are 3D, not plants", T("3d-model", "nature-plants/rocks-stones") == "3d")
    check("vegetation decals are atlases", T("decal", "nature-terrain/vegetation") == "atlas")
    check("brushes and Unreal-only listings are left out", not fab.listed({"listing_type": "brush", "formats": {"image": 1}})
          and not fab.listed({"listing_type": "3d-model", "formats": {"unreal-engine": 1}}))
    check("material -> surface", T("material", "ground/sand") == "surface")
    check("decal", T("decal", "damage-grunge/graffiti") == "decal")

    # 2. remote asset: browsable, no files, virtual path, publish date as "added"
    item = {"uid": "u-123", "title": "Old Stand 05", "listing_type": "3d-model", "category": "Furniture",
            "category_path": "furniture-fixtures", "tags": ["Wood", "Table"], "published": "2026-11-06T16:28:57Z",
            "thumb": {"s": "https://x/s.jpg", "m": "https://x/m.jpg", "l": "https://x/l.jpg"}, "formats": {"fbx": [1, 2]}}
    lib = {"name": "Fab Megascans", "kind": "fab", "path": os.path.join(tmp, "lib")}
    r = fab.remote_asset(lib, item, 100.0)
    check("remote asset shape", r["remote"] and r["path"] == "fab://u-123" and r["id"] == "fab-megascans:u-123"
          and not r["maps"] and r["added"] and r["tags"] == ["table", "wood"], r)

    # 3. zip-slip refused, normal zip extracted
    bad = os.path.join(tmp, "bad.zip")
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../escape.txt", "x")
    try:
        fab._safe_extract(bad, os.path.join(tmp, "x"))
        check("zip with ../ refused", False)
    except fab.FabError:
        check("zip with ../ refused", not os.path.exists(os.path.join(tmp, "escape.txt")))

    # 4. a pulled folder (Fab layout: flat, tier names, new-schema <id>.json) -> a full local asset under the same id
    folder = os.path.join(lib["path"], "3d", "Old_Stand_05_xeokah0")
    os.makedirs(os.path.join(folder, "Var1"))
    for f in ("Old_Stand_05_xeokah0_Low.fbx", "Old_Stand_05_xeokah0_Low_1K_BaseColor.jpg",
              "Old_Stand_05_xeokah0_Low_1K_Normal.jpg", "Old_Stand_05_xeokah0_Low_1K_Roughness.jpg",
              os.path.join("Var1", "Old_Stand_05_Var1_LOD0.fbx"), "Old_Stand_05_xeokah0_Low_xeokah0_VarA_LOD1.fbx"):
        open(os.path.join(folder, f), "wb").close()
    with open(os.path.join(folder, "xeokah0.json"), "w") as fh:
        json.dump({"id": "xeokah0", "categories": ["3d", "interior"], "tags": ["brown"], "averageColor": "#43382D"}, fh)
    with open(os.path.join(folder, fab.SIDE), "w") as fh:
        json.dump({"uid": "u-123", "title": item["title"], "type": "3d", "tier": "low", "format": "fbx",
                   "bytes": 7246865, "pulled_at": 1.0, "listing": item}, fh)
    a = fab.local_asset(lib, folder)
    check("local asset keeps the remote id", a["id"] == r["id"] and a["name"] == "Old Stand 05" and not a["remote"], a["id"])
    check("maps parsed (BaseColor -> albedo)", set(a["map_types"]) >= {"albedo", "normal", "roughness"}, a["map_types"])
    check("meshes incl. nested + file-name variations", len(a["meshes"]) == 3 and a["format"] == "fbx"
          and {"Var1", "VarA"} <= set(a["variations"]), a["meshes"])
    check("source id + tier recorded", a["source_id"] == "xeokah0" and a["fab"]["tier"] == "low", a.get("fab"))

    # 5. scan: pulled -> local, others -> remote
    fab._index_cache.update(mtime=-1, data={"crawled_at": 5.0, "items": [item, dict(item, uid="u-456", title="Other")]},
                            by_uid={"u-123": item, "u-456": dict(item, uid="u-456", title="Other")})
    orig = fab.load_index
    fab.load_index = lambda: fab._index_cache["data"]
    got = {x["id"]: x for k, x in fab.scan_fab(lib, {}, lambda n: None) if k == "asset"}
    fab.load_index = orig
    check("scan yields local + remote", not got["fab-megascans:u-123"]["remote"] and got["fab-megascans:u-456"]["remote"], list(got))

    # 6. evict deletes only that folder and returns the remote asset
    fab.index_item = lambda uid: item
    stub, size = fab.evict(lib, "u-123")
    check("evict removes the files", not os.path.exists(folder) and stub["remote"] and os.path.isdir(lib["path"]))

    if "--live" in sys.argv:
        import importlib
        importlib.reload(fab)
        idx = fab.load_index()
        live = next(i for i in idx["items"] if i["listing_type"] == "3d-model")
        lib2 = {"name": "Fab Test", "kind": "fab", "path": os.path.join(tmp, "live")}
        info = fab.files_for(live["uid"])
        check("live: file list has tiers", "low" in info["tiers"] and info["msid"], info)
        a, rep = fab.pull(lib2, live["uid"], "low")
        check("live: pulled + parsed", a["meshes"] and a["maps"] and a["fab"]["tier"] == "low" and os.path.exists(a["preview"]), rep)
        check("live: zip removed after extract", not os.listdir(os.path.join(lib2["path"], "_downloads")))
        stub, size = fab.evict(lib2, live["uid"])
        check("live: evicted", stub["remote"] and size > 1e6, size)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failed" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
