"""Install (or update) the Blender add-on and enable it. Name and ports come from brand.json.

    py -3.13 tools/install_blender_addon.py --blender "C:\\path\\to\\blender.exe"

Copies blender/bridge2_blender to %APPDATA%\\Blender Foundation\\Blender\\<version>\\scripts\\addons,
writes config.json (where Bridge lives), then runs that Blender in background mode once to enable the
add-on and save preferences. Restart any open Blender of that version to pick it up.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "blender", "bridge2_blender")
sys.path.insert(0, os.path.join(ROOT, "server"))
from brand import BRAND  # noqa: E402


def _brand_bl_info(text):
    """bl_info must stay a literal (Blender reads it without running the module), so rewrite the copy."""
    n = BRAND["name"].replace('"', "")
    text = re.sub(r'"name": "[^"]*"', '"name": "%s"' % n, text, count=1)
    text = re.sub(r'"author": "[^"]*"', '"author": "%s"' % BRAND.get("publisher", "").replace('"', ""), text, count=1)
    text = re.sub(r'"location": "[^"]*"', '"location": "File > Import > %s"' % n, text, count=1)
    return re.sub(r'(from the )\w+( browser")', r"\g<1>%s\2" % n, text, count=1)
def _newest_desktop_blender():
    """Newest blender-*/blender.exe on the Desktop (alpha builds get replaced often), else Program Files."""
    import glob
    cands = glob.glob(os.path.join(os.environ.get("USERPROFILE", ""), "Desktop", "blender-*", "blender.exe"))
    cands += glob.glob(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Blender Foundation", "Blender *", "blender.exe"))
    return max(cands, key=os.path.getmtime) if cands else ""


DEFAULT_BLENDER = _newest_desktop_blender()
ENABLE = (
    "import addon_utils, bpy; "
    "addon_utils.enable('bridge2_blender', default_set=True, persistent=True); "
    "bpy.ops.wm.save_userpref(); "
    "print('BRIDGE2_ENABLED', 'bridge2_blender' in bpy.context.preferences.addons)"
)


def blender_version(exe):
    out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=120).stdout
    m = re.search(r"Blender (\d+)\.(\d+)", out)
    if not m:
        sys.exit("Could not read the Blender version from %s" % exe)
    return "%s.%s" % m.groups()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blender", default=DEFAULT_BLENDER)
    ap.add_argument("--no-enable", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(args.blender):
        sys.exit("Blender not found: %s (pass --blender)" % args.blender)
    ver = blender_version(args.blender)
    dst = os.path.join(os.environ["APPDATA"], "Blender Foundation", "Blender", ver, "scripts", "addons", "bridge2_blender")
    os.makedirs(dst, exist_ok=True)
    for f in os.listdir(SRC):
        if f.endswith(".py"):
            shutil.copy2(os.path.join(SRC, f), os.path.join(dst, f))
    init = os.path.join(dst, "__init__.py")
    with open(init, "r", encoding="utf-8") as fh:
        text = _brand_bl_info(fh.read())
    with open(init, "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(os.path.join(dst, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"root": ROOT, "name": BRAND["name"], "exe_name": BRAND["exe_name"],
                   "web_port": BRAND["ports"]["web"], "blender_port": BRAND["ports"]["blender"]}, fh, indent=2)
    print("Installed %s add-on for Blender %s -> %s" % (BRAND["name"], ver, dst))
    if not args.no_enable:
        r = subprocess.run([args.blender, "-b", "--python-expr", ENABLE], capture_output=True, text=True, timeout=300)
        ok = "BRIDGE2_ENABLED True" in r.stdout
        print("Enabled in preferences:" if ok else "Could NOT enable automatically:", ok)
        if not ok:
            print(r.stdout[-2000:], r.stderr[-2000:])


if __name__ == "__main__":
    main()
