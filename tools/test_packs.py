"""Tests for the "splat_pack" library kind and layered splat payloads.   py -3.13 tools/test_packs.py
Builds tiny 3DGS PLYs in a temp folder; exit code 0 = all pass."""
import os
import shutil
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
import scanners  # noqa: E402
import send  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(detail)))
    if not cond:
        failures.append(name)


PROPS = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "scale_1", "scale_2",
         "rot_0", "rot_1", "rot_2", "rot_3"]


def splat_ply(path, n=3):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    head = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n" % n + \
        "".join("property float %s\n" % p for p in PROPS) + "end_header\n"
    with open(path, "wb") as fh:
        fh.write(head.encode())
        fh.write(struct.pack("<%df" % (n * len(PROPS)), *([0.0] * n * len(PROPS))))


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()


tmp = tempfile.mkdtemp(prefix="assetbridge_packs_test_")
try:
    # flat pack with numbered previews + a dataset folder that must stay out
    crystals = os.path.join(tmp, "Crystals")
    for i in (1, 7, 12):
        splat_ply(os.path.join(crystals, "Crystal_%d.ply" % i))
        touch(os.path.join(crystals, "%d.png" % i))
    touch(os.path.join(crystals, "Crystal_Lineup.png"))
    splat_ply(os.path.join(crystals, "Crystal_7", "quick_splat", "quick_splat_262144.ply"))
    with open(os.path.join(crystals, "mesh.ply"), "w") as fh:  # a mesh PLY is not a splat
        fh.write("ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nproperty float y\nproperty float z\n"
                 "element face 0\nproperty list uchar int vertex_indices\nend_header\n0 0 0\n")
    lib = {"name": "Crystals", "kind": "splat_pack", "path": crystals, "depth": 0, "tags": ["crystal"]}
    got = {a["name"]: a for _, a in scanners.scan_splat_pack(lib, {}, lambda n: None)}
    check("flat pack: one asset per splat, no dataset or mesh PLYs", sorted(got) == ["Crystal 1", "Crystal 12", "Crystal 7"], sorted(got))
    check("numbered preview pairs (Crystal_7 <- 7.png)", os.path.basename(got["Crystal 7"]["preview"]) == "7.png", got["Crystal 7"]["preview"])
    check("pack tags", "crystal" in got["Crystal 1"]["tags"])

    # zero-padded numbers: Tree_Stump_07.ply <- Tree_Stump_0007.png, not Group_7.png
    st = os.path.join(tmp, "Stumps")
    splat_ply(os.path.join(st, "Tree_Stump_07.ply"))
    touch(os.path.join(st, "Tree_Stump_0007.png"))
    touch(os.path.join(st, "Group_7.png"))
    a = next(a for _, a in scanners.scan_splat_pack(dict(lib, path=st, name="Stumps"), {}, lambda n: None))
    check("zero-padded preview pairs, other prefixes ignored", [os.path.basename(p) for p in a["previews"]] == ["Tree_Stump_0007.png"], a["previews"])

    # layered pack, one folder per asset
    neb = os.path.join(tmp, "Clouds")
    d = os.path.join(neb, "09_cirrus")
    splat_ply(os.path.join(d, "09_cirrus_Volume.ply"))
    splat_ply(os.path.join(d, "09_cirrus_Points.ply"), n=5)
    for suf in ("thumb_points", "thumb_both_alt", "thumb_volume", "thumb_both"):
        touch(os.path.join(d, "09_cirrus_%s.png" % suf))
    nlib = {"name": "Clouds", "kind": "splat_pack", "path": neb, "depth": 1, "layers": ["Volume", "Points"],
            "preview_prefer": ["thumb_both", "thumb_both_alt", "thumb_volume", "thumb_points"], "strip": "_FINAL$"}
    out = [a for _, a in scanners.scan_splat_pack(nlib, {}, lambda n: None)]
    check("layers group into one asset", len(out) == 1 and out[0]["layers"] == ["Volume", "Points"], [a.get("layers") for a in out])
    n = out[0]
    check("combined preview first, preferred order", [os.path.basename(p)[len("09_cirrus_"):-4] for p in n["previews"]] ==
          ["thumb_both", "thumb_both_alt", "thumb_volume", "thumb_points"], n["previews"])
    check("name + points of both layers", n["name"] == "09 Cirrus" and n["points"] == 8, (n["name"], n["points"]))

    # payloads: both layers by default, one when asked; signature tells them apart
    p = send.build_payload(n, {}, "c4d")
    check("payload has every layer", [s["layer"] for s in p["splats"]] == ["Volume", "Points"] and p["splat"]["layer"] == "Volume", p["splats"])
    p2 = send.build_payload(n, {"splat_layers": ["Points"]}, "c4d")
    check("layer pick + distinct signature", [s["layer"] for s in p2["splats"]] == ["Points"] and p2["signature"] != p["signature"])
    single = send.build_payload(got["Crystal 1"], {}, "c4d")
    check("single splats unchanged (no splats list)", single["splats"] == [] and single["splat"]["format"] == "ply")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failed" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
