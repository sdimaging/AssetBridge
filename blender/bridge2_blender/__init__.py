"""Asset browser for Blender: one-click import from the browser + a scripting API (name/ports: config.json).

Interactive: the add-on listens on 127.0.0.1:24982 for sends from the browser (same bridge2/1
protocol as the Cinema 4D receiver) and imports on Blender's main thread via a timer.
Scripting / background mode: see api.py (`from bridge2_blender import api`).
"""
import json
import os
import queue
import socket
import threading
import webbrowser

import bpy

bl_info = {
    "name": "AssetBridge",
    "author": "",
    "version": (0, 2, 0),
    "blender": (4, 2, 0),
    "location": "File > Import > AssetBridge",
    "description": "One-click Megascans / Poly Haven / Poliigon / splat import from the AssetBridge browser",
    "category": "Import-Export",
}

def _cfg():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


CFG = _cfg()
NAME = CFG.get("name", bl_info["name"])
HOST, PORT = "127.0.0.1", int(CFG.get("blender_port", 24982))
VERSION = ".".join(map(str, bl_info["version"]))
BROWSER_URL = "http://127.0.0.1:%d/" % int(CFG.get("web_port", 8642))

_jobs = queue.Queue()
_server = None


class _Job:
    """queued -> running -> done, or queued -> abandoned when AssetBridge stops waiting before the main thread got to it
    (a modal operator / busy main thread). An abandoned import is skipped instead of landing in the scene minutes
    later as a "ghost" nobody is waiting for."""
    def __init__(self, payload):
        self.payload = payload
        self.done = threading.Event()
        self.result = None
        self.state = "queued"
        self.lock = threading.Lock()

    def claim(self):
        """Main thread: True if this job should run (it wasn't abandoned)."""
        with self.lock:
            if self.state != "queued":
                return False
            self.state = "running"
            return True

    def abandon(self):
        """Socket thread, after its wait ran out: True if the job never started (and now never will)."""
        with self.lock:
            if self.state == "queued":
                self.state = "abandoned"
                return True
            return False


class _Server(threading.Thread):
    def __init__(self):
        super().__init__(name="Bridge2BlenderReceiver", daemon=True)
        self._halt = threading.Event()
        self.listening = False

    def stop(self):
        self._halt.set()

    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((HOST, PORT))
            s.listen(8)
        except OSError as e:
            print("[%s] can't listen on %s:%d (%s) — another Blender already receiving?" % (NAME, HOST, PORT, e))
            s.close()
            return
        self.listening = True
        s.settimeout(0.5)
        print("[%s] Blender receiver %s listening on %s:%d" % (NAME, VERSION, HOST, PORT))
        while not self._halt.is_set():
            try:
                conn, _ = s.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve_client, args=(conn,), daemon=True).start()
        s.close()

    def _serve_client(self, conn):
        with conn:
            try:
                conn.settimeout(15)
                buf = b""
                while b"\n" not in buf:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                msg = json.loads(buf.decode("utf-8").strip() or "{}")
                action = msg.get("action")
                if action == "ping":
                    reply = {"ok": True, "app": "Blender", "blender_version": bpy.app.version_string,
                             "receiver": VERSION, "file": os.path.basename(bpy.data.filepath) or "untitled",
                             "queued": _jobs.qsize()}
                elif action == "import":
                    job = _Job(msg)
                    _jobs.put(job)
                    if job.done.wait(600):
                        reply = job.result
                    elif job.abandon():
                        reply = {"ok": False, "error": "Timed out waiting for Blender (modal operator running?) - "
                                 "the import was cancelled, nothing was added"}
                    else:
                        reply = {"ok": False, "error": "Blender is still importing after 10 minutes; "
                                 "it will appear when it finishes"}
                else:
                    reply = {"ok": False, "error": "unknown action %r" % action}
            except Exception as e:
                reply = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            try:
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
            except OSError:
                pass


def _drain():
    """Main-thread timer: run queued imports."""
    from . import core
    import importlib
    while True:
        try:
            job = _jobs.get_nowait()
        except queue.Empty:
            break
        if not job.claim():  # AssetBridge stopped waiting before Blender got here: don't import a ghost
            continue
        try:
            importlib.reload(core)  # pick up edits without restarting Blender
            job.result, _ = core.run(job.payload)
            print("[%s] %s" % (NAME, job.result.get("message", "done")))
        except Exception as e:
            import traceback
            traceback.print_exc()
            job.result = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        finally:
            job.done.set()
    return 0.2


class BRIDGE2_OT_open_browser(bpy.types.Operator):
    bl_idname = "bridge2.open_browser"
    bl_label = "Open asset browser"
    bl_description = "Open the asset browser (starts it if needed)"

    def execute(self, context):
        import urllib.request
        exe = CFG.get("exe_name", NAME)
        app = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", exe, exe + ".exe")
        if os.path.exists(app):
            os.startfile(app)
            return {"FINISHED"}
        try:
            urllib.request.urlopen(BROWSER_URL + "api/status", timeout=0.5)
            webbrowser.open(BROWSER_URL)
            return {"FINISHED"}
        except OSError:
            pass
        from .api import bridge_root
        launcher = os.path.join(bridge_root(), NAME + ".bat")
        if os.path.exists(launcher):
            os.startfile(launcher)
            return {"FINISHED"}
        self.report({"ERROR"}, "%s not found at %s" % (NAME, launcher))
        return {"CANCELLED"}


def _menu(self, context):
    self.layout.operator(BRIDGE2_OT_open_browser.bl_idname, text=NAME)


def register():
    global _server
    bpy.utils.register_class(BRIDGE2_OT_open_browser)
    bpy.types.TOPBAR_MT_file_import.append(_menu)
    if bpy.app.background:
        return  # scripts use api.import_asset directly; no socket in background mode
    if _server is None:
        _server = _Server()
        _server.start()
    if not bpy.app.timers.is_registered(_drain):
        bpy.app.timers.register(_drain, first_interval=0.5, persistent=True)


def unregister():
    global _server
    if bpy.app.timers.is_registered(_drain):
        bpy.app.timers.unregister(_drain)
    if _server is not None:
        _server.stop()
        _server = None
    bpy.types.TOPBAR_MT_file_import.remove(_menu)
    bpy.utils.unregister_class(BRIDGE2_OT_open_browser)
