"""Texture colour spaces under any OCIO config.  py -3 tools/test_colorspace.py  (exit code 0 = pass, or Blender not found)

Runs Blender in the background twice: once with its own colour config, once with a small ACES-style studio
config (written to a temp folder and passed through $OCIO) whose names are not Blender's ("Utility - sRGB - Texture",
"Utility - Raw" ...). In each it builds a material from an sRGB PNG albedo, a float EXR albedo and a PNG normal map
through the add-on (blender/bridge2_blender/core.py) and checks the colour space each texture got.
Set BLENDER=<path to blender.exe> to choose the Blender build.
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ACES_LIKE = """ocio_profile_version: 2
search_path: ""
strictparsing: true
luma: [0.2126, 0.7152, 0.0722]
roles:
  reference: ACEScg
  scene_linear: ACEScg
  rendering: ACEScg
  default: Utility - sRGB - Texture
  default_byte: Utility - sRGB - Texture
  default_float: ACEScg
  default_sequencer: Utility - sRGB - Texture
  data: Utility - Raw
  color_picking: Utility - sRGB - Texture
  texture_paint: Utility - sRGB - Texture
  compositing_log: Utility - sRGB - Texture
  color_timing: Utility - sRGB - Texture
  aces_interchange: ACEScg
  cie_xyz_d65_interchange: ACEScg
file_rules:
  - !<Rule> {name: Default, colorspace: default}
displays:
  sRGB:
    - !<View> {name: Standard, colorspace: Utility - sRGB - Texture}
active_displays: [sRGB]
active_views: [Standard]
colorspaces:
  - !<ColorSpace>
    name: ACEScg
    isdata: false
  - !<ColorSpace>
    name: Utility - Linear - sRGB
    isdata: false
    to_scene_reference: !<MatrixTransform> {matrix: [0.613, 0.339, 0.047, 0, 0.070, 0.916, 0.013, 0, 0.020, 0.109, 0.869, 0, 0, 0, 0, 1]}
  - !<ColorSpace>
    name: Utility - sRGB - Texture
    isdata: false
    to_scene_reference: !<ExponentWithLinearTransform> {gamma: 2.4, offset: 0.055}
  - !<ColorSpace>
    name: Utility - Raw
    isdata: true
"""

EXPECT = {
    "blender": {"albedo_png": "sRGB", "albedo_exr": "Linear Rec.709", "normal": "Non-Color"},
    "aces": {"albedo_png": "Utility - sRGB - Texture", "albedo_exr": "Utility - Linear - sRGB", "normal": "Utility - Raw"},
}


def find_blender():
    exe = os.environ.get("BLENDER")
    if exe and os.path.exists(exe):
        return exe
    found = sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender *\blender.exe"),
                   key=lambda p: [int(x) for x in os.path.basename(os.path.dirname(p)).split()[-1].split(".") if x.isdigit()])
    return found[-1] if found else shutil.which("blender")


def outside():
    exe = find_blender()
    if not exe:
        print("SKIP colour spaces: Blender not found (set BLENDER=...)")
        return 0
    tmp = tempfile.mkdtemp(prefix="cs_test_")
    cfg = os.path.join(tmp, "aces_like.ocio")
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write(ACES_LIKE)
    failures = 0
    flags = 0x08000000 if os.name == "nt" else 0  # no console window
    for mode in ("blender", "aces"):
        env = dict(os.environ)
        env.pop("OCIO", None)
        if mode == "aces":
            env["OCIO"] = cfg
        r = subprocess.run([exe, "-b", "--factory-startup", "--python-exit-code", "1", "--python", os.path.abspath(__file__),
                            "--", mode, tmp], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300, creationflags=flags)
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.startswith(("PASS ", "FAIL "))]
        print("\n".join(lines) if lines else (r.stdout or "")[-3000:] + (r.stderr or "")[-2000:])
        failures += sum(ln.startswith("FAIL") for ln in lines) + (0 if r.returncode == 0 and lines else 1)
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failures else 0


def inside(mode, tmp):
    import bpy
    sys.path.insert(0, os.path.join(ROOT, "blender"))
    from bridge2_blender import core

    def image(name, ext, float_buffer):
        img = bpy.data.images.new(name, 8, 8, float_buffer=float_buffer)
        path = os.path.join(tmp, "%s_%s.%s" % (mode, name, ext))
        img.filepath_raw = path
        img.file_format = "OPEN_EXR" if ext == "exr" else "PNG"
        img.save()
        bpy.data.images.remove(img)
        return path

    png, exr, nrm = image("albedo", "png", False), image("albedo", "exr", True), image("normal", "png", False)
    names = {}
    for key, maps in (("albedo_png", {"albedo": png, "normal": nrm}), ("albedo_exr", {"albedo": exr})):
        mat = core.build_material("cs_" + key, maps, {"type": "surface", "name": key}, {})
        for n in mat.node_tree.nodes:
            if n.type == "TEX_IMAGE" and n.image:
                kind = "normal" if n.image.filepath == nrm else key
                names[kind] = n.image.colorspace_settings.name
    failed = False
    for kind, want in EXPECT[mode].items():
        ok = names.get(kind) == want
        failed |= not ok
        print(("PASS " if ok else "FAIL ") + "%s config: %s -> %s" % (mode, kind, names.get(kind)) + ("" if ok else " (want %s)" % want))
    if mode == "aces":  # the old hard-coded names really are missing from such a config
        img = bpy.data.images.load(png, check_existing=True)
        try:
            img.colorspace_settings.name = "sRGB"
            print("FAIL aces config: 'sRGB' should not exist here, so the config didn't load")
            failed = True
        except TypeError:
            print("PASS aces config: a hard-coded 'sRGB' would have failed the import")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    if "--" in sys.argv:
        args = sys.argv[sys.argv.index("--") + 1:]
        inside(args[0], args[1])
    else:
        sys.exit(outside())
