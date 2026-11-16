"""Desktop app: a native window (WebView2) around the local asset server. Name/icon/IDs: brand.json.

Built into <exe_name>.exe by tools/build_app.py. The exe carries the icon, so it pins to the taskbar
and Start menu like any app. It starts the server (system Python, detached) if it isn't running,
and the server keeps running after the window closes so C4D/Blender sends and scripts still work.
"""
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request


def here():
    return os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


# install config (written by tools/build_app.py next to the exe) -> repo root -> brand.json there
_CFG = _load(os.path.join(here(), "app.json"))
ROOT = _CFG.get("root") or os.path.dirname(here())  # running from source: app/ lives in the repo root
_BRAND = _load(os.path.join(ROOT, "brand.json"))
TITLE = _BRAND.get("name", "AssetBridge")
APP_ID = _BRAND.get("app_id", "AssetBridge.App")
URL = "http://127.0.0.1:%d/" % (_BRAND.get("ports", {}).get("web", 8642))


def config():
    return {"root": ROOT, "pythonw": _CFG.get("pythonw")}


def server_up():
    try:
        urllib.request.urlopen(URL + "api/status", timeout=0.6)
        return True
    except OSError:
        return False


def start_server(cfg):
    import shutil
    pyw = cfg.get("pythonw") or shutil.which("pythonw") or "pythonw"
    script = os.path.join(cfg["root"], "server", "bridge2.py")
    flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    subprocess.Popen([pyw, script, "--no-browser"], cwd=cfg["root"], creationflags=flags, close_fds=True)


def focus_existing():
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, TITLE)
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    return False


def style_window(hwnd, icon_path):
    """Graphite title bar (Windows 11) + window icon."""
    dwm = ctypes.windll.dwmapi
    val = ctypes.c_int(1)
    dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), 4)  # DWMWA_USE_IMMERSIVE_DARK_MODE
    for attr, bgr in ((35, 0x001E1C1C), (36, 0x00EEECEC)):    # caption colour #1c1c1e, text #ececee
        c = ctypes.c_int(bgr)
        dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(c), 4)
    if icon_path and os.path.exists(icon_path):
        user32 = ctypes.windll.user32
        for size, which in ((256, 1), (16, 0)):  # ICON_BIG, ICON_SMALL
            h = user32.LoadImageW(None, icon_path, 1, size if which else 16, size if which else 16, 0x10)
            if h:
                user32.SendMessageW(hwnd, 0x0080, which, h)  # WM_SETICON


def main():
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    if focus_existing():
        return
    cfg = config()
    if not server_up():
        start_server(cfg)
        deadline = time.time() + 20
        while not server_up():
            if time.time() > deadline:
                ctypes.windll.user32.MessageBoxW(None, "%s's server didn't start.\nCheck %s\\cache\\server.log" % (TITLE, cfg["root"]),
                                                 TITLE, 0x10)
                return
            time.sleep(0.15)

    import webview
    icon = os.path.join(here(), "app.ico")
    if not os.path.exists(icon):
        icon = os.path.join(cfg["root"], "icons", "app.ico")
    # <app>.exe --select <asset id> [--hero 3d]  opens straight to an asset
    args = sys.argv[1:]
    params = {k.lstrip("-"): v for k, v in zip(args[::2], args[1::2]) if k in ("--select", "--hero")}
    url = URL + ("?" + urllib.parse.urlencode(params) if params else "")
    win = webview.create_window(TITLE, url, width=1560, height=980, min_size=(980, 640), background_color="#161617")

    def on_shown():
        try:
            style_window(int(win.native.Handle.ToInt64()), icon)
        except Exception as e:  # cosmetic only
            print("style_window:", e)

    win.events.shown += on_shown
    storage = os.path.join(cfg["root"], "cache", "webview")
    os.makedirs(storage, exist_ok=True)
    # Drop WebView2's HTTP + compiled-script caches on launch so an update is never served stale.
    # Local Storage (accent, options) lives elsewhere in the profile and is kept.
    import shutil
    for sub in ("Cache", "Code Cache", "GPUCache", "Service Worker"):
        shutil.rmtree(os.path.join(storage, "EBWebView", "Default", sub), ignore_errors=True)
    webview.start(private_mode=False, storage_path=storage)


if __name__ == "__main__":
    main()
