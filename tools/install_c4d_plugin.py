"""Install (or update) the Cinema 4D receiver plugin. Name, plugin IDs and ports come from brand.json.

    py -3.13 tools/install_c4d_plugin.py            # newest C4D found
    py -3.13 tools/install_c4d_plugin.py 2025 2026  # specific versions
    py -3.13 tools/install_c4d_plugin.py all        # every installed version

Restart Cinema 4D after the first install (and after a brand change). Later edits to bridge2_import.py are
picked up on the next send (it is reloaded per job) once re-installed.
"""
import glob
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "c4d", "Bridge2")
sys.path.insert(0, os.path.join(ROOT, "server"))
from brand import BRAND  # noqa: E402


def prefs_dirs(versions):
    base = os.path.join(os.environ["APPDATA"], "Maxon")
    found = {}
    for d in glob.glob(os.path.join(base, "Maxon Cinema 4D *_*")):
        m = re.match(r"Maxon Cinema 4D (\d{4})_[0-9A-F]{8}$", os.path.basename(d))
        if m:
            found.setdefault(m.group(1), []).append(d)
    if versions == ["all"]:
        versions = sorted(found)
    elif not versions:
        versions = [max(found)] if found else []
    return [(v, d) for v in versions for d in found.get(v, [])]


def c4d_port(ver):
    """2026 and older keep the brand port (24981); each newer release gets its own (2027 -> 24983, 2028 -> 24985),
    so two Cinema 4D versions can be open at once. AssetBridge pings them all (server/bridge2.py c4d_receivers)."""
    base = BRAND["ports"]["c4d"]
    return base if int(ver) <= 2026 else base + 2 * (int(ver) - 2026)


def main():
    targets = prefs_dirs(sys.argv[1:])
    if not targets:
        sys.exit("No Cinema 4D preferences folder found under %APPDATA%\\Maxon")
    folder = re.sub(r"[^A-Za-z0-9_-]", "", BRAND["exe_name"]) or "AssetBridge"
    for ver, prefs in targets:
        plugins = os.path.join(prefs, "plugins")
        dst = os.path.join(plugins, folder)
        # an older install under another folder name would register the same plugin IDs twice
        for old in glob.glob(os.path.join(plugins, "*", "bridge2.pyp")):
            od = os.path.dirname(old)
            if os.path.normcase(od) != os.path.normcase(dst):
                shutil.rmtree(od, ignore_errors=True)
                print("Removed previous install %s" % od)
        os.makedirs(dst, exist_ok=True)
        for name in ("bridge2.pyp", "bridge2_import.py"):
            shutil.copy2(os.path.join(SRC, name), os.path.join(dst, name))
        shutil.copytree(os.path.join(SRC, "res"), os.path.join(dst, "res"), dirs_exist_ok=True)
        for stale in ("bridge2_receiver.json", os.path.join("res", "bridge2.png")):
            if os.path.exists(os.path.join(dst, stale)):
                os.remove(os.path.join(dst, stale))
        cfg = {"name": BRAND["name"], "exe_name": BRAND["exe_name"],
               "command_id": BRAND["c4d"]["command_id"], "receiver_id": BRAND["c4d"]["receiver_id"],
               "c4d_port": c4d_port(ver), "c4d_release": int(ver), "web_port": BRAND["ports"]["web"],
               "launcher": os.path.join(ROOT, BRAND["name"] + ".bat")}
        with open(os.path.join(dst, "receiver.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        print("Installed %s receiver for C4D %s (port %d) -> %s" % (BRAND["name"], ver, c4d_port(ver), dst))
    if BRAND["c4d"]["command_id"] in range(1000001, 1000011) or BRAND["c4d"]["receiver_id"] in range(1000001, 1000011):
        print("NOTE: brand.json uses Maxon's testing plugin IDs. Register two IDs at developers.maxon.net "
              "and put them in brand.json before sharing the plugin.")


if __name__ == "__main__":
    main()
