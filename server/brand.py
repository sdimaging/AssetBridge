"""White-label settings, read from brand.json at the repo root (one file rebrands the whole app).

    from brand import BRAND        # dict with every key below filled in
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "name": "AssetBridge",                # app name: window title, menus, messages
    "wordmark": "ASSETBRIDGE",            # sidebar wordmark
    "wordmark_accent_letter": -1,         # index of one letter drawn in the accent colour, -1 = none
    "exe_name": "AssetBridge",            # desktop app executable / install folder / shortcut name
    "app_id": "AssetBridge.App",          # Windows AppUserModelID (taskbar grouping)
    "publisher": "",
    "accent": "teal",                     # default accent preset: gold | violet | ember | teal
    "icon_svg": "icons/mark.svg",         # source of every icon (rendered by tools/make_icons.py)
    "manifest": "asset.json",             # per-asset manifest file name (see ASSET_FORMAT.md)
    "format_name": "Asset Format",
    "ports": {"web": 8642, "c4d": 24981, "blender": 24982},
    # Cinema 4D plugin IDs. 1000001-1000010 are Maxon's testing IDs: register your own two at
    # developers.maxon.net before sharing the plugin with anyone.
    "c4d": {"command_id": 1000008, "receiver_id": 1000007},
}


def load(path=None):
    path = path or os.path.join(ROOT, "brand.json")
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        pass
    out = dict(DEFAULTS)
    for k, v in data.items():
        out[k] = dict(DEFAULTS[k], **v) if isinstance(DEFAULTS.get(k), dict) and isinstance(v, dict) else v
    return out


BRAND = load()
