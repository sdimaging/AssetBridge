"""Asset browser receiver for Cinema 4D (name, IDs and ports come from receiver.json, written by the installer).

Listens on 127.0.0.1:<c4d port> for one-line JSON messages from the browser
(see server/send.py) and imports on the main thread via bridge2_import.run().
Also adds Extensions > <name> to open/start the browser.
"""
import importlib
import json
import os
import queue
import socket
import sys
import threading
import traceback
import urllib.request
import webbrowser

import c4d

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)
import bridge2_import  # noqa: E402

# Registered plugin IDs (plugincafe): C4DBridge21 = receiver, C4DBridge20 = menu command.
def _cfg():
    for name in ("receiver.json", "bridge2_receiver.json"):
        try:
            with open(os.path.join(PLUGIN_DIR, name), "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            continue
    return {}


CFG = _cfg()
NAME = CFG.get("name", "AssetBridge")
ID_RECEIVER = int(CFG.get("receiver_id", 1000007))  # 1000001-1000010 = Maxon testing IDs; register real ones
ID_OPEN = int(CFG.get("command_id", 1000008))
HOST, PORT = "127.0.0.1", int(CFG.get("c4d_port", 24981))
VERSION = "0.2.0"
BROWSER_URL = "http://127.0.0.1:%d/" % int(CFG.get("web_port", 8642))

_jobs = queue.Queue()
_server = None
_info = {"octane": False}
_state = {}  # survives bridge2_import reloads


class _Job(object):
    """queued -> running -> done, or queued -> abandoned when AssetBridge stops waiting before the main thread got to it
    (a modal dialog / busy main thread). An abandoned import is skipped instead of landing in the scene minutes
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
        super(_Server, self).__init__(name="Bridge2Receiver", daemon=True)
        self._halt = threading.Event()

    def stop(self):
        self._halt.set()

    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((HOST, PORT))
            s.listen(8)
        except OSError as e:
            print("[%s] cannot listen on %s:%d (%s)" % (NAME, HOST, PORT, e))
            s.close()
            return
        s.settimeout(0.5)
        print("[%s] receiver %s listening on %s:%d" % (NAME, VERSION, HOST, PORT))
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
                    reply = {"ok": True, "app": "Cinema 4D", "c4d_version": c4d.GetC4DVersion(),
                             "receiver": VERSION, "octane": _info["octane"], "queued": _jobs.qsize()}
                elif action == "import":
                    job = _Job(msg)
                    _jobs.put(job)
                    c4d.SpecialEventAdd(ID_RECEIVER)
                    if job.done.wait(600):
                        reply = job.result
                    elif job.abandon():
                        reply = {"ok": False, "error": "Timed out waiting for Cinema 4D (modal dialog open?) - "
                                 "the import was cancelled, nothing was added"}
                    else:
                        reply = {"ok": False, "error": "Cinema 4D is still importing after 10 minutes; "
                                 "it will appear when it finishes"}
                else:
                    reply = {"ok": False, "error": "unknown action %r" % action}
            except Exception as e:
                reply = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            try:
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
            except OSError:
                pass


class Receiver(c4d.plugins.MessageData):
    def CoreMessage(self, id, bc):
        if id != ID_RECEIVER:
            return True
        while True:
            try:
                job = _jobs.get_nowait()
            except queue.Empty:
                break
            if not job.claim():  # AssetBridge stopped waiting before C4D got here: don't import a ghost
                continue
            try:
                importlib.reload(bridge2_import)
                job.result = bridge2_import.run(job.payload, _state)
                print("[%s] %s" % (NAME, job.result.get("message", "done")))
            except Exception as e:
                traceback.print_exc()
                job.result = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            finally:
                job.done.set()
        return True


class OpenBridge(c4d.plugins.CommandData):
    def Execute(self, doc):
        exe = CFG.get("exe_name", NAME)
        app = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", exe, exe + ".exe")
        if os.path.exists(app):  # desktop app focuses its window if already open
            os.startfile(app)
            return True
        try:
            urllib.request.urlopen(BROWSER_URL + "api/status", timeout=0.5)
            webbrowser.open(BROWSER_URL)
            return True
        except OSError:
            pass
        launcher = _launcher()
        if launcher and os.path.exists(launcher):
            os.startfile(launcher)
        else:
            c4d.gui.MessageDialog("%s not found.\nSet \"launcher\" in receiver.json next to the plugin." % NAME)
        return True


def _launcher():
    return CFG.get("launcher")


def _start():
    global _server
    if _server is None:
        _info["octane"] = bridge2_import.octane_available()
        _server = _Server()
        _server.start()


def PluginMessage(id, data):
    global _server
    if id == c4d.C4DPL_PROGRAM_STARTED:
        _start()
    elif id == c4d.C4DPL_ENDPROGRAM and _server is not None:
        _server.stop()
        _server = None
    return False


if __name__ == "__main__":
    icon = c4d.bitmaps.BaseBitmap()
    if icon.InitWith(os.path.join(PLUGIN_DIR, "res", "app.png"))[0] != c4d.IMAGERESULT_OK:
        icon = None
    c4d.plugins.RegisterMessagePlugin(ID_RECEIVER, NAME + " Receiver", 0, Receiver())
    c4d.plugins.RegisterCommandPlugin(ID_OPEN, NAME, 0, icon, "Open the %s asset browser" % NAME, OpenBridge())
