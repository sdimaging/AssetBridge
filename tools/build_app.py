"""Build the desktop app (<exe_name>.exe from brand.json) and install it with Desktop + Start menu shortcuts.

    py -3.13 tools/make_icons.py      # renders the icon set from brand.json's icon_svg
    py -3.13 tools/build_app.py

Installs to %LOCALAPPDATA%\\Programs\\<exe_name>. Pin it from the Start menu or the running taskbar button
(Windows doesn't let programs pin themselves).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
from brand import BRAND  # noqa: E402

NAME, EXE = BRAND["name"], BRAND["exe_name"]
ICON = os.path.join(ROOT, "icons", "app.ico")
DEST = os.path.join(os.environ["LOCALAPPDATA"], "Programs", EXE)
BUILD = os.path.join(ROOT, "cache", "build")


def shortcut(path, target, icon, workdir):
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');$s.TargetPath='%s';$s.IconLocation='%s,0';"
          "$s.WorkingDirectory='%s';$s.Description='%s asset library';$s.Save()") % (path, target, icon, workdir, NAME.replace("'", ""))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def main():
    if not os.path.exists(ICON):
        sys.exit("Run tools/make_icons.py first (no icons/app.ico)")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
                    "--name", EXE, "--icon", ICON,
                    "--distpath", os.path.join(BUILD, "dist"), "--workpath", os.path.join(BUILD, "work"),
                    "--specpath", BUILD, "--collect-all", "webview",
                    os.path.join(ROOT, "app", "desktop_app.py")], check=True)
    # close a running window and wait until it has really exited before replacing its files
    subprocess.run(["taskkill", "/IM", EXE + ".exe", "/F"], capture_output=True)
    deadline = time.time() + 30
    while EXE + ".exe" in subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s.exe" % EXE], capture_output=True, text=True).stdout:
        if time.time() > deadline:
            sys.exit("%s.exe is still running; close it and run again" % EXE)
        time.sleep(0.25)
    for _ in range(20):  # WebView2 children can hold DLLs for a moment after the parent exits
        try:
            if os.path.isdir(DEST):
                shutil.rmtree(DEST)
            break
        except PermissionError:
            time.sleep(0.5)
    else:
        sys.exit("Could not replace %s (files in use)" % DEST)
    shutil.copytree(os.path.join(BUILD, "dist", EXE), DEST)
    shutil.copy2(ICON, os.path.join(DEST, "app.ico"))  # title-bar icon (loaded fresh by the app)
    # Windows caches shortcut icons by file path, so give every icon build its own name
    tag = hashlib.sha1(open(ICON, "rb").read()).hexdigest()[:8]
    shell_icon = os.path.join(DEST, "app-%s.ico" % tag)
    shutil.copy2(ICON, shell_icon)
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    with open(os.path.join(DEST, "app.json"), "w", encoding="utf-8") as fh:
        json.dump({"root": ROOT, "pythonw": pyw}, fh, indent=2)
    exe = os.path.join(DEST, EXE + ".exe")
    desktop = os.path.join(os.environ["USERPROFILE"], "Desktop", NAME + ".lnk")
    start = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", NAME + ".lnk")
    pinned_dir = os.path.join(os.environ["APPDATA"], "Microsoft", "Internet Explorer", "Quick Launch", "User Pinned", "TaskBar")
    pinned = [os.path.join(pinned_dir, f) for f in os.listdir(pinned_dir)
              if f.lower() == (NAME + ".lnk").lower()] if os.path.isdir(pinned_dir) else []
    for lnk in [desktop, start] + pinned:
        shortcut(lnk, exe, shell_icon, DEST)
    if pinned:
        print("Updated pinned taskbar shortcut(s):", ", ".join(os.path.basename(p) for p in pinned))
    subprocess.run(["ie4uinit.exe", "-show"], capture_output=True)  # refresh the shell icon cache
    print("Installed %s\nShortcuts: %s\n           %s" % (exe, desktop, start))


if __name__ == "__main__":
    main()
