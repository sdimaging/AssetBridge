"""Local asset browser + one-click send to Cinema 4D and Blender (name/branding: brand.json).

    py -3.13 server/bridge2.py            # start (opens the browser)
    py -3.13 server/bridge2.py --no-browser
    py -3.13 server/bridge2.py --scan-only  # index and exit
"""
import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jobs
from urllib.parse import parse_qs, quote, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from brand import BRAND  # noqa: E402
from catalog import Catalog  # noqa: E402
import scanners  # noqa: E402
import health  # noqa: E402
import send as sender  # noqa: E402
import fab  # noqa: E402

scanners.SCANNERS["fab"] = fab.scan_fab  # Fab (Quixel Megascans): indexed remotely, pulled on demand (server/fab.py)
FAB_PULL_SLOTS = threading.Semaphore(3)  # downloads at once; more pulls wait their turn

VERSION = "0.1.0"
WEB = os.path.join(ROOT, "web")
CACHE = os.path.join(ROOT, "cache")
THUMBS = os.path.join(CACHE, "thumbs")
RENDERS = os.path.join(CACHE, "renders")  # static 3D renders for models whose preview is only a texture (tools/render_previews.py)


def find_blender(cfg):
    """Configured Blender ("blender_exe"), else the newest installed release (stable before Desktop alpha builds).
    "blender" is the receiver's {host, port}; an old config that put the exe path there still works."""
    import glob
    for exe in (cfg.get("blender_exe"), cfg.get("blender")):
        if isinstance(exe, str) and os.path.exists(exe):
            return exe
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    cands = sorted(glob.glob(os.path.join(pf, "Blender Foundation", "Blender *", "blender.exe")))
    return cands[-1] if cands else None


def render_path(asset_id):
    return os.path.join(RENDERS, hashlib.sha1(asset_id.encode("utf-8")).hexdigest()[:20] + ".png")


def preview_of(a):
    """A model's own render beats a texture-map preview (never a real preview image the vendor shipped)."""
    if not a:
        return None
    pv = a.get("preview")
    maps = {os.path.normcase(m.get("path", "")) for m in a.get("maps", [])}
    if not pv or os.path.normcase(pv) in maps:
        r = render_path(a["id"])
        if os.path.exists(r):
            return r
    return pv
CONFIG = os.path.join(ROOT, "config", "libraries.json")
DEFAULT_CONFIG = {
    "port": BRAND["ports"]["web"],
    "c4d": {"host": "127.0.0.1", "port": BRAND["ports"]["c4d"]},
    "blender": {"host": "127.0.0.1", "port": BRAND["ports"]["blender"]},
    "thumb_size": 384,
    "libraries": [],
    "node": "node",
    "splat_transform": "",  # path to splat-transform's cli.mjs; empty = auto-detect (see find_splat_transform)
}


def quick_ping(host, port):
    """sender.ping without Windows' ~2 s wait on a closed localhost port."""
    try:
        socket.create_connection((host, port), timeout=0.3).close()
    except OSError as e:
        return {"online": False, "error": str(e)}
    return sender.ping(host, port)


# AssetBridge waits longer than the receivers do (their main-thread wait is 600 s), so a receiver's own timeout reply
# ("modal dialog open?") reaches the user instead of a bare socket timeout.
SEND_TIMEOUT = 620


def c4d_receivers(cfg):
    """Every Cinema 4D with the receiver open: the brand port (2026 and older) plus one port per newer release
    (tools/install_c4d_plugin.py c4d_port). -> [{port, version(int), octane, ...ping}]"""
    c = cfg["c4d"]

    def probe(port):
        try:  # Windows takes ~2 s to refuse a closed localhost port: a short connect probe first, all ports at once
            socket.create_connection((c["host"], port), timeout=0.3).close()
        except OSError:
            return None
        r = sender.ping(c["host"], port)
        if not r.get("online"):
            return None
        r["port"] = port
        r["release"] = int(str(r.get("c4d_version") or 0)[:4] or 0)
        return r
    with ThreadPoolExecutor(4) as ex:
        return [r for r in ex.map(probe, [int(c["port"]) + 2 * k for k in range(4)]) if r]


def pick_c4d(cfg, receivers=None):
    """Which open Cinema 4D gets a send: cfg c4d_prefer (a release, e.g. 2027) if it's open, else one with Octane
    (AssetBridge builds Octane materials), newest first, else the newest."""
    rs = receivers if receivers is not None else c4d_receivers(cfg)
    if not rs:
        return None
    pref = str(cfg.get("c4d_prefer") or "")
    for r in rs:
        if pref and str(r["release"]) == pref:
            return r
    return sorted(rs, key=lambda r: (bool(r.get("octane")), r["release"]), reverse=True)[0]


def find_splat_transform(cfg):
    """Configured path, else tools/node_modules (setup.ps1 installs it there), else a global npm install."""
    cand = [cfg.get("splat_transform") or "",
            os.path.join(ROOT, "tools", "node_modules", "@playcanvas", "splat-transform", "bin", "cli.mjs"),
            os.path.join(os.environ.get("APPDATA", ""), "npm", "node_modules", "@playcanvas", "splat-transform", "bin", "cli.mjs")]
    return next((c for c in cand if c and os.path.exists(c)), None)

try:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
except ImportError:  # thumbnails fall back to serving the original preview
    Image = None


def load_config():
    if not os.path.exists(CONFIG):
        save_config(DEFAULT_CONFIG)
    with open(CONFIG, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    os.replace(tmp, CONFIG)


class App:
    def __init__(self):
        os.makedirs(THUMBS, exist_ok=True)
        self.cfg = load_config()
        self.catalog = Catalog(os.path.join(CACHE, "bridge2.db"))
        fab.bind(self.catalog)
        import tagging
        if self.catalog.get_meta("tags_version") != str(tagging.TAGS_VERSION):  # vocabulary changed: re-tag once
            n = self.catalog.retag_all()
            self.catalog.set_meta("tags_version", tagging.TAGS_VERSION)
            print("[tags] re-tagged %d assets" % n)
        self.scan_state = {"running": False, "library": None, "current": "", "seen": 0,
                           "changed": 0, "removed": 0, "finished_at": None, "errors": []}
        self._scan_lock = threading.Lock()
        self._thumb_pool = ThreadPoolExecutor(max_workers=6)
        self._thumb_locks = {}
        self._thumb_locks_guard = threading.Lock()
        self.thumb_failures = set()
        self.health = None
        self.health_state = {"running": False, "done": 0, "total": 0}
        self.jobs = jobs.Jobs()  # every background job's explicit lifecycle (server/jobs.py, GET /api/jobs)

    # ---- scanning ----------------------------------------------------------
    def scan_async(self, only=None):
        """Queue a scan (one library or all). Never dropped: while a scan runs, requests merge into one follow-up
        scan of the union of what was asked. -> the Job that will cover this request."""
        return self.jobs.submit("scan", lambda job: self.scan(job.scope, job), label="Scanning libraries",
                                scope=None if only is None else [only], if_busy="queue")

    def scan(self, only=None, job=None):
        """only: None (all), a library name, or a set of names."""
        if isinstance(only, str):
            only = {only}
        with self._scan_lock:
            return self._scan(only, job)

    def _scan(self, only, job):
        st = self.scan_state
        st.update(running=True, seen=0, changed=0, removed=0, errors=[], library=None,
                  job=job.id if job else None, scope=None if only is None else sorted(only))
        full = self.catalog.get_meta("scan_version") != str(scanners.SCAN_VERSION)
        try:
            for lib in self.cfg["libraries"]:
                if only and lib["name"] not in only:
                    continue
                if not lib.get("enabled", True):
                    continue
                st["library"] = lib["name"]
                if job:
                    job.progress(step=lib["name"], done=st["seen"])
                if not os.path.isdir(lib["path"]):
                    st["errors"].append("%s: folder not found (%s)" % (lib["name"], lib["path"]))
                    continue
                scan_fn = scanners.SCANNERS.get(lib.get("kind", "generic"), scanners.scan_generic)
                known = {} if full else self.catalog.known_mtimes(lib["name"])
                removed = self.catalog.removed_ids()  # taken out of AssetBridge by the user: never re-added by a scan
                seen = set()
                now = time.time()
                batch = 0

                def progress(name):
                    st["current"] = name
                    if job:
                        job.progress(step="%s / %s" % (lib["name"], name), done=st["seen"])

                for kind, item in scan_fn(lib, known, progress):
                    st["seen"] += 1
                    if kind == "unchanged":
                        seen.add(item)
                        continue
                    if item["id"] in seen or item["id"] in removed:  # duplicate key / removed by the user
                        continue
                    seen.add(item["id"])
                    self.catalog.upsert(item, now)
                    st["changed"] += 1
                    batch += 1
                    if batch >= 100:
                        self.catalog.commit()
                        batch = 0
                st["removed"] += self.catalog.remove_missing(lib["name"], seen)
                self.catalog.commit()
            if full and not only and not st["errors"]:
                self.catalog.set_meta("scan_version", scanners.SCAN_VERSION)
        except Exception as e:
            traceback.print_exc()
            st["errors"].append("%s: %s" % (type(e).__name__, e))
        finally:
            st.update(running=False, current="", finished_at=time.time())
        threading.Thread(target=self.prewarm_thumbs, daemon=True).start()
        for follow_up in (self.render_previews_async, lambda: self.health is not None and self.health_async()):
            try:  # these are their own jobs; a problem starting one must not fail a scan that completed
                follow_up()
            except Exception as e:
                traceback.print_exc()
                if job:
                    job.write("after the scan: %s: %s" % (type(e).__name__, e))
        msg = "%d seen, %d changed, %d removed" % (st["seen"], st["changed"], st["removed"])
        if st["errors"]:
            raise RuntimeError("%s; %d error(s): %s" % (msg, len(st["errors"]), "; ".join(st["errors"][:3])))
        return {"message": msg, "seen": st["seen"], "changed": st["changed"], "removed": st["removed"]}

    # ---- 3D previews --------------------------------------------------------
    def render_previews_async(self):
        """Models whose only preview is a texture map get a static render (tools/render_previews.py, Blender).
        A job (kind "previews"): progress from the script's "PREVIEW n/m" lines, exit code and output kept."""
        if self.cfg.get("auto_render_previews", True) is False:
            return None
        exe = find_blender(self.cfg)
        if not exe:
            return None
        return self.jobs.submit("previews", lambda job: self._render_previews(job, exe),
                                label="Rendering 3D previews", if_busy="queue")

    def _render_previews(self, job, exe):
        script = os.path.join(ROOT, "tools", "render_previews.py")
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: never flash a console
        job.progress(step="starting Blender")
        self._render_proc = subprocess.Popen([exe, "-b", "--factory-startup", "--python", script], stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags,
                                             text=True, encoding="utf-8", errors="replace")
        rendered = 0
        for line in self._render_proc.stdout:
            job.write(line)
            m = re.search(r"PREVIEW\s+(\d+)\s*/\s*(\d+)", line)
            if m:
                rendered = int(m.group(1))
                job.progress(step="rendering", done=rendered, total=int(m.group(2)))
        code = self._render_proc.wait()
        if code != 0:
            raise RuntimeError("Blender exited with code %d (see the job log)" % code)
        return "%d preview(s) rendered" % rendered if rendered else "nothing to render"

    # ---- thumbnails --------------------------------------------------------
    def thumb_path(self, preview, size=None):
        try:
            mt = os.path.getmtime(preview)
        except OSError:
            return None
        size = size or self.cfg["thumb_size"]
        h = hashlib.sha1(("%s|%s|%s" % (preview, mt, size)).encode()).hexdigest()
        return os.path.join(THUMBS, h[:2], h + ".webp")

    def make_thumb(self, asset_id, preview, need_color=False, size=None):
        size = size or self.cfg["thumb_size"]
        out = self.thumb_path(preview, size)
        if not out or Image is None:
            return out
        if os.path.exists(out) and not need_color:
            return out
        with self._thumb_locks_guard:
            lock = self._thumb_locks.setdefault(out, threading.Lock())
        with lock:
            try:
                if os.path.exists(out):
                    im = Image.open(out)
                elif preview.lower().endswith((".hdr", ".exr")):
                    im = _open_hdr(preview, size)
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    im.save(out + ".tmp", "WEBP", quality=86, method=4)
                    os.replace(out + ".tmp", out)
                else:
                    im = Image.open(preview)
                    if im.format == "JPEG":
                        im.draft("RGB", (size * 2, size * 2))  # decode at reduced scale: 8K jpgs in ms
                    im.thumbnail((size, size), Image.LANCZOS)
                    if im.mode not in ("RGB", "RGBA"):
                        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    tmp = out + ".tmp"
                    im.save(tmp, "WEBP", quality=84, method=4)
                    os.replace(tmp, out)
                self.catalog.set_color(asset_id, _avg_color(im))
            except Exception as e:
                print("[thumb] %s: %s" % (preview, e))
                self.thumb_failures.add(preview)
                return out if os.path.exists(out) else None
        return out

    def prewarm_thumbs(self):
        def run():
            todo = []
            for i, p, color in self.catalog.previews():
                if os.path.exists(render_path(i)):
                    p = preview_of(self.catalog.get(i)) or p
                tp = self.thumb_path(p)
                if tp and (color is None or not os.path.exists(tp)):
                    todo.append((i, p, color is None))
            for f in [self._thumb_pool.submit(self.make_thumb, *t) for t in todo]:
                f.result()
            self.catalog.commit()
        threading.Thread(target=run, daemon=True).start()

    # ---- health -------------------------------------------------------------
    def health_async(self):
        """Queue a library health check (kind "health"); a request during a check queues exactly one re-check.
        health_state stays the legacy view the UI polls."""
        st = self.health_state
        st["running"] = True  # set before the thread starts, so an immediate poll already sees it

        def go(job):
            st.update(running=True, done=0, total=0)

            def prog(n, t):
                st.update(done=n, total=t)
                job.progress(step="checking", done=n, total=t)
            try:
                self.health = health.run(self.catalog, self.thumb_failures, CACHE, progress=prog)
            except Exception as e:
                self.health = {"error": str(e), "issues": [], "generated_at": time.time()}
                raise
            finally:
                st["running"] = bool(self.jobs.list("health", active_only=True)[1:])  # a queued re-check follows
            n = sum(i.get("count", 0) for i in self.health.get("issues", []))
            return {"message": "%d issue(s)" % n, "issues": len(self.health.get("issues", []))}
        return self.jobs.submit("health", go, label="Checking library health", if_busy="queue")

    def health_ids(self, code):
        for i in (self.health or {}).get("issues", []):
            if i["code"] == code:
                return i["ids"]
        return []

    # ---- splat conversion -------------------------------------------------
    def convert_splat(self, src, out_fmt, scale=1.0):
        """splat-transform src -> cache/splat_conv/<hash>.<out_fmt>. PLY<->SPZ get a 180 Z turn (3DGS PLY frame vs
        the SPZ frame Blender loads upright); the turn is its own inverse.
        `scale` is a uniform bake (-s): positions and every gaussian's size scale together."""
        st = os.path.getmtime(src["path"])
        rot = src["format"] in ("ply", "spz") and src["format"] != out_fmt
        scale = round(float(scale), 6)
        key = hashlib.sha1(("%s|%s|%s|%s|%s" % (src["path"], st, out_fmt, rot, scale)).encode()).hexdigest()[:16]
        out_dir = os.path.join(CACHE, "splat_conv")
        os.makedirs(out_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(src["path"]))[0]
        out = os.path.join(out_dir, "%s_%s.%s" % (stem, key, out_fmt))
        if os.path.exists(out):
            return out
        cli = find_splat_transform(self.cfg)
        if not cli:
            raise RuntimeError("splat-transform isn't installed (run setup.ps1, or set \"splat_transform\" in config/libraries.json)")

        def convert(job):
            # a job keyed on the output: two sends of the same splat wait for one conversion instead of both
            # writing the same .part file
            if os.path.exists(out):
                return out
            job.progress(step="%s -> %s" % (os.path.basename(src["path"]), out_fmt.upper()))
            cmd = [self.cfg.get("node", "node"), cli, "-q", "-w", src["path"]] + (["-r", "0,0,180"] if rot else []) \
                + (["-s", repr(scale)] if abs(scale - 1.0) > 1e-6 else []) + [out + ".part." + out_fmt]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                                   creationflags=0x08000000 if os.name == "nt" else 0)
            except subprocess.TimeoutExpired:
                raise RuntimeError("splat-transform took longer than 15 minutes")
            if r.returncode != 0 or not os.path.exists(out + ".part." + out_fmt):
                raise RuntimeError("splat-transform failed: %s" % (r.stderr or r.stdout)[-400:])
            os.replace(out + ".part." + out_fmt, out)
            return {"message": "converted", "path": out}
        job = self.jobs.run("convert", convert, key=out, label="Converting %s" % os.path.basename(src["path"]), timeout=960)
        if job.state != jobs.SUCCEEDED:
            raise RuntimeError(job.error or "conversion did not finish")
        return out

    # ---- Fab: remote library, pulled on demand (server/fab.py) ----------------
    def fab_lib(self, asset):
        lib = next((l for l in self.cfg["libraries"] if asset and l["name"] == asset.get("library")), None)
        if not lib or lib.get("kind") != "fab":
            raise fab.FabError("Not a Fab asset")
        return lib

    def fab_index_async(self):
        """Re-crawl the Fab catalogue (kind "fab_index"), then rescan every Fab library."""
        def run(job):
            res = fab.crawl(progress=lambda n, pages, sort: job.progress(step="%d listings (%d pages)" % (n, pages), done=n))
            for lib in self.cfg["libraries"]:
                if lib.get("kind") == "fab":
                    self.scan_async(lib["name"])
            return {"message": "%d listings indexed" % res["count"], **res}
        return self.jobs.submit("fab_index", run, label="Indexing Fab", if_busy="join")

    def fab_pull(self, asset_id, tier=None, wait=False):
        """Download a Fab asset at a quality tier and make it local (kind "fab_pull", keyed on the asset: a second
        request while one runs joins it). wait=True runs it inline (a send of a remote asset)."""
        a = self.catalog.get(asset_id)
        lib = self.fab_lib(a)
        tier = tier if tier in fab.TIERS else (lib.get("tier") or "high")

        def run(job):
            job.progress(step="waiting for a download slot")
            with FAB_PULL_SLOTS:
                asset, info = fab.pull(lib, a["fab"]["uid"], tier, job)
            self.catalog.upsert(asset, time.time())
            self.catalog.commit()
            return {"message": "%s (%s, %.0f MB)" % (asset["name"], info["label"], max(1, info["bytes"] / 1e6)),
                    "id": asset["id"], **info}
        label = "Pulling %s from Fab" % a["name"]
        if wait:
            return self.jobs.run("fab_pull", run, key=asset_id, label=label, timeout=3600)
        return self.jobs.submit("fab_pull", run, key=asset_id, label=label, if_busy="join")

    def fab_evict(self, asset_id):
        a = self.catalog.get(asset_id)
        lib = self.fab_lib(a)
        if any(j.key == asset_id for j in self.jobs.list("fab_pull", active_only=True)):
            raise fab.FabError("It's still downloading")
        stub, size = fab.evict(lib, a["fab"]["uid"])
        self.catalog.upsert(stub, time.time())
        self.catalog.commit()
        return size

    # ---- send --------------------------------------------------------------
    def send(self, asset_id, opts, target="c4d"):
        """A send is a job (kind "send") keyed on asset + target + options: an identical send that arrives while one
        is in flight (double-click, Enter twice) joins it and gets the same reply instead of importing twice."""
        key = "%s|%s|%s" % (asset_id, target, json.dumps(opts or {}, sort_keys=True))
        asset = self.catalog.get(asset_id)
        label = "Sending %s to %s" % ((asset or {}).get("name", asset_id), sender.RECEIVER_NAMES.get(target, target))
        def run(job):
            res = self._send(job, asset_id, opts, target)
            job.result = res  # the receiver's full reply stays on the job either way
            if not res.get("ok"):
                job.fail(res.get("error") or "send failed")
            return res
        job = self.jobs.run("send", run, key=key, label=label, timeout=SEND_TIMEOUT + 960)
        res = dict(job.result) if isinstance(job.result, dict) else {"ok": False, "error": job.error or "send did not finish"}
        res["job"] = job.id
        return res

    def _send(self, job, asset_id, opts, target):
        asset = self.catalog.get(asset_id)
        if not asset:
            return {"ok": False, "error": "Unknown asset"}
        if asset.get("remote"):  # only on Fab: pull it first (the default quality, or the one asked for), then send
            job.progress(step="pulling from Fab")
            try:
                pj = self.fab_pull(asset_id, (opts or {}).get("fab_tier"), wait=True)
            except fab.FabError as e:
                return {"ok": False, "error": str(e)}
            if pj.state != jobs.SUCCEEDED:
                return {"ok": False, "error": "Pull from Fab failed: %s" % (pj.error or pj.state)}
            asset = self.catalog.get(asset_id)
        if target not in ("c4d", "blender"):
            return {"ok": False, "error": "Unknown target %r" % target}
        try:
            payload = sender.build_payload(asset, opts, target, converter=self.convert_splat)
        except RuntimeError as e:
            return {"ok": False, "error": str(e)}
        c = self.cfg[target]
        port, chosen, open_c4ds = c["port"], None, []
        if target == "c4d":
            open_c4ds = c4d_receivers(self.cfg)
            chosen = pick_c4d(self.cfg, open_c4ds)
            if chosen:
                port = chosen["port"]
        t0 = time.time()
        job.progress(step="waiting for %s" % sender.RECEIVER_NAMES.get(target, target))
        res = sender.send(c["host"], port, payload, timeout=SEND_TIMEOUT)
        if chosen:
            res["c4d_release"] = chosen["release"]
            if len(open_c4ds) > 1 and res.get("ok"):  # two versions open: say which one got it
                res["message"] = "%s (Cinema 4D %d)" % (res.get("message", ""), chosen["release"])
        res["elapsed"] = round(time.time() - t0, 2)
        res["target"] = target
        if payload.get("note") and res.get("ok"):
            res["message"] = "%s (%s)" % (res.get("message", ""), payload["note"])
        res["payload"] = {"maps": payload["maps"], "geometry": payload["geometry"], "native": payload["native"],
                          "resolution": payload["options"].get("resolution"), "splat": payload.get("splat"),
                          "proxy": payload.get("proxy"), "scale": payload.get("scale")}
        self.catalog.log_send(asset_id, res.get("ok"), time.time())
        return res


def asset_files(a):
    """Every file the preview may load for an asset: (role, kind, path, extra). Index = position in this list."""
    out = []
    for m in a.get("maps", []):
        out.append(("map", m["type"], m["path"], {"res": m.get("res"), "lod": m.get("lod"), "variant": m.get("variant")}))
    for part, ms in (a.get("material_sets") or {}).items():
        for m in ms:
            out.append(("part_map", m["type"], m["path"], {"res": m.get("res"), "part": part, "variant": m.get("variant")}))
    for m in a.get("meshes", []):
        out.append(("mesh", m["format"], m["path"], {"lod": m.get("lod"), "variation": m.get("variation"),
                                                      "res": m.get("res"), "proxy": m.get("proxy")}))
    for s in a.get("splats", []):
        out.append(("splat", s["format"], s["path"], {"version": s.get("version"), "points": s.get("points"),
                                                      "size": s.get("size"), "layer": s.get("layer")}))
    if a.get("preview"):
        out.append(("preview", "image", a["preview"], {}))
    return out


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def wordmark_html():
    w, i = BRAND["wordmark"], BRAND.get("wordmark_accent_letter", -1)
    if isinstance(i, int) and 0 <= i < len(w):
        return "%s<em>%s</em>%s" % (_esc(w[:i]), _esc(w[i]), _esc(w[i + 1:]))
    return _esc(w)


APP = None
_HDR_LOCK = threading.Lock()  # a 16K float HDR is ~1.6 GB decoded — one at a time
WEB_IMAGE = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _open_hdr(path, size):
    """Decode .hdr/.exr with OpenCV, tone-map (Reinhard on log-average luminance), return a PIL image."""
    os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
    import cv2
    import numpy as np
    with _HDR_LOCK:
        img = cv2.imread(path, cv2.IMREAD_ANYDEPTH | cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("OpenCV could not read " + path)
        h, w = img.shape[:2]
        scale = min(1.0, float(size * 2) / max(h, w))
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    img = np.nan_to_num(img.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    lum = 0.0722 * img[..., 0] + 0.7152 * img[..., 1] + 0.2126 * img[..., 2]
    key = float(np.exp(np.mean(np.log(lum + 1e-4))))
    x = img * (0.18 / max(key, 1e-4))
    x = x / (1.0 + x)
    x = np.clip(x, 0, 1) ** (1 / 2.2)
    rgb = (x[..., ::-1] * 255 + 0.5).astype(np.uint8)
    im = Image.fromarray(rgb)
    im.thumbnail((size * 2 if im.width > im.height * 1.5 else size, size), Image.LANCZOS)
    return im


def _avg_color(im):
    """Average of the visible pixels (ignores transparent background on cut-out previews)."""
    rgba = im.convert("RGBA").resize((32, 32), Image.BOX)
    px = [p for p in rgba.getdata() if p[3] > 128] or list(rgba.getdata())
    n = len(px)
    return "#%02X%02X%02X" % tuple(int(sum(p[c] for p in px) / n) for c in range(3))


class Handler(BaseHTTPRequestHandler):
    # ---- who may call: this app's own page, non-browser clients, and (CORS) the origins _cors_allowed names
    LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")

    def _same_origin(self, origin):
        port = self.server.server_port
        return origin in ("http://localhost:%d" % port, "http://127.0.0.1:%d" % port, "http://[::1]:%d" % port)

    def _cors_allowed(self, origin):
        """Pages on another origin that may call this server. None by default."""
        return False

    def _refused(self, post):
        """Requests a web page on another site could make are refused: any request whose Host isn't localhost (DNS
        rebinding), and any POST whose Origin isn't this app's own page (cross-site request forgery). Receivers,
        scripts and curl send no Origin header and are not affected."""
        host = (self.headers.get("Host") or "").strip().lower()
        if host and (host if host.endswith("]") else host.rsplit(":", 1)[0]) not in self.LOCAL_HOSTS:
            return "unknown host"
        origin = self.headers.get("Origin")
        if post and origin and not self._same_origin(origin) and not self._cors_allowed(origin):
            return "cross-site request refused"
        return None

    def end_headers(self):
        origin = self.headers.get("Origin")
        if origin and self._cors_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self):
        origin = self.headers.get("Origin")
        allowed = bool(origin) and self._cors_allowed(origin)
        self.send_response(204 if allowed else 403)
        if allowed:
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")  # https page -> localhost (Chrome PNA)
            self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cross_origin_denied(self, asset_id, q):
        """A page from another origin may only read the files of an asset it was given a session for."""
        origin = self.headers.get("Origin")
        if not origin or self._same_origin(origin):
            return False
        return True

    server_version = "%s/%s" % (BRAND["exe_name"], VERSION)

    def log_message(self, fmt, *args):
        pass

    # ---- helpers
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _not_modified(self, path):
        """Files are revalidated on every view (ETag = mtime + size): saving over a splat, model, texture or
        preview shows the new version immediately; unchanged files cost a 304 with no body."""
        try:
            st = os.stat(path)
        except OSError:
            return None, False
        tag = '"%x-%x"' % (st.st_mtime_ns, st.st_size)
        if self.headers.get("If-None-Match") == tag:
            self.send_response(304)
            self.send_header("ETag", tag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return tag, True
        return tag, False

    def _file(self, path, ctype=None, cache=True):
        tag, done = self._not_modified(path)
        if done:
            return
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            return self._json({"error": "not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype or mimetypes.guess_type(path)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        if tag:
            self.send_header("ETag", tag)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}

    # ---- routes
    def do_GET(self):
        why = self._refused(post=False)
        if why:
            return self._json({"error": why}, 403)
        try:
            self._get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            traceback.print_exc()
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        why = self._refused(post=True)
        if why:
            return self._json({"ok": False, "error": why}, 403)
        try:
            self._post()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            traceback.print_exc()
            self._json({"ok": False, "error": str(e)}, 500)

    def _get(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        if p in ("/", "/index.html"):
            # stamp the page with the newest web-file time so module/script URLs change on every update
            stamp = str(int(max(os.path.getmtime(os.path.join(WEB, f)) for f in os.listdir(WEB) if f.endswith((".js", ".css", ".html")))))
            with open(os.path.join(WEB, "index.html"), "r", encoding="utf-8") as fh:
                html = fh.read().replace('/web/app.js"', '/web/app.js?v=%s"' % stamp).replace('/web/app.css"', '/web/app.css?v=%s"' % stamp)
            html = html.replace("{{NAME}}", _esc(BRAND["name"])).replace("{{WORDMARK}}", wordmark_html())
            boot = {"name": BRAND["name"], "accent": BRAND["accent"], "format_name": BRAND["format_name"], "build": stamp}
            html = html.replace("<head>", '<head><script>window.APP_BUILD="%s";window.BRAND=%s</script>' % (stamp, json.dumps(boot)), 1)
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if p.startswith("/web/"):
            f = os.path.normpath(os.path.join(WEB, p[5:]))
            if not f.startswith(WEB):
                return self._json({"error": "bad path"}, 400)
            return self._file(f, "text/javascript; charset=utf-8" if f.endswith((".js", ".mjs")) else None, cache=False)
        if p == "/api/tags":
            return self._json({"facets": APP.catalog.tag_counts(q.get("library") or None, q.get("type") or None)})
        if p == "/api/removed":
            return self._json({"items": APP.catalog.removed_list()})
        if p == "/api/assets":
            total, items = APP.catalog.search(
                q=q.get("q", "").strip(), library=q.get("library") or None, type_=q.get("type") or None,
                fav=q.get("fav") == "1", recent=q.get("recent") == "1", sort=q.get("sort", "name"),
                offset=int(q.get("offset", 0)), limit=min(int(q.get("limit", 200)), 1000),
                color=q.get("color") or None, label=q.get("label") or None,
                filters=[f for f in (q.get("f") or "").split(",") if f],
                ids=APP.health_ids(q["health"]) if q.get("health") else None)
            for it in items:  # thumbnail version: a new 3D render must not hide behind the browser cache
                if os.path.exists(render_path(it["id"])):
                    a = APP.catalog.get(it["id"])
                    pv = preview_of(a)
                    if pv and pv == render_path(it["id"]):
                        it["tv"] = int(os.path.getmtime(pv))
                        it["has_thumb"] = True
            return self._json({"total": total, "items": items})
        if p.startswith("/api/asset/"):
            a = APP.catalog.get(unquote(p[len("/api/asset/"):]))
            if a:
                from catalog import blender_ok
                a["blender_ok"] = blender_ok(a)
                a["labels"] = APP.catalog.labels_for(a["id"])
                a["smart_tags"] = APP.catalog.tags_for(a["id"])
                if preview_of(a) == render_path(a["id"]):
                    a["tv"] = int(os.path.getmtime(render_path(a["id"])))
                elif (a.get("fab") or {}).get("pulled_at"):
                    a["tv"] = int(a["fab"]["pulled_at"])
            return self._json(a) if a else self._json({"error": "not found"}, 404)
        if p == "/api/facets":
            f = APP.catalog.facets()
            f["labels"] = APP.catalog.label_counts()
            f["saved_filters"] = APP.catalog.saved_filters()
            return self._json(f)
        if p == "/api/jobs":  # every background job: ?active=1 &kind=scan &limit=50
            js = APP.jobs.list(kind=q.get("kind") or None, active_only=q.get("active") == "1",
                               limit=min(int(q.get("limit", 50)), 200))
            return self._json({"jobs": [j.to_dict() for j in js], "states": sorted(jobs.TRANSITIONS),
                               "transitions": {k: sorted(v) for k, v in jobs.TRANSITIONS.items()}})
        if p.startswith("/api/jobs/"):  # one job, with result + log; ?wait=<s> blocks until it ends (max 120 s)
            jid = unquote(p[len("/api/jobs/"):])
            wait = min(float(q.get("wait", 0) or 0), 120.0)
            j = APP.jobs.wait(jid, wait) if wait > 0 else APP.jobs.get(jid)
            return self._json(j.to_dict(full=True)) if j else self._json({"error": "unknown job %s" % jid}, 404)
        if p == "/api/status":
            c, b = APP.cfg["c4d"], APP.cfg["blender"]
            c4ds = c4d_receivers(APP.cfg) if q.get("c4d") == "1" else []
            chosen = pick_c4d(APP.cfg, c4ds)
            c4d_st = dict(chosen, others=[r["release"] for r in c4ds if r is not chosen]) if chosen else \
                ({"online": False} if q.get("c4d") == "1" else None)
            return self._json({"version": VERSION, "scan": APP.scan_state,
                               "jobs": [j.to_dict() for j in APP.jobs.list(active_only=True)],
                               "c4d": c4d_st,
                               "blender": quick_ping(b["host"], b["port"]) if q.get("c4d") == "1" else None})
        if p.startswith("/api/payload/"):  # what a send would ship — for scripts and debugging
            a = APP.catalog.get(unquote(p[len("/api/payload/"):]))
            if not a:
                return self._json({"error": "not found"}, 404)
            opts = {k: v for k, v in q.items() if k not in ("target",)}
            for k in ("displacement", "ao", "disp_exr", "use_native", "apply_to_selection"):
                if k in opts:
                    opts[k] = opts[k] in ("1", "true", "True")
            if "variations" in opts:
                opts["variations"] = opts["variations"].split(",")
            if "splat_layers" in opts:
                opts["splat_layers"] = opts["splat_layers"].split(",")
            if "real_scale" in opts:
                opts["real_scale"] = opts["real_scale"] in ("1", "true", "True")
            try:
                return self._json(sender.build_payload(a, opts, q.get("target", "c4d"), converter=None))
            except RuntimeError as e:
                return self._json({"error": str(e)})
        if p == "/api/libraries":
            return self._json(APP.cfg["libraries"])
        if p == "/api/fab/files":  # ?id=<asset>: the quality tiers Fab offers (public file list) + what's on disk
            a = APP.catalog.get(q.get("id", ""))
            try:
                lib = APP.fab_lib(a)
                info = fab.files_for(a["fab"]["uid"])
            except fab.FabError as e:
                return self._json({"ok": False, "error": str(e)})
            return self._json({"ok": True, "format": info["format"], "msid": info["msid"],
                               "tiers": {t: {"size": f["size"], "faces": f.get("faces"), "label": f.get("label")} for t, f in info["tiers"].items()},
                               "default": lib.get("tier") or "high", "pulled": (a.get("fab") or {}).get("tier"),
                               "bytes": (a.get("fab") or {}).get("bytes"), "url": a["fab"]["url"],
                               "local_copies": fab.local_copies(info["msid"], lib["name"])})
        if p == "/api/fab/index":
            idx = fab.load_index()
            return self._json({"count": idx.get("count", 0), "crawled_at": idx.get("crawled_at"),
                               "job": (APP.jobs.latest("fab_index") or None) and APP.jobs.latest("fab_index").to_dict()})
        if p == "/api/health":
            if APP.health is None and not APP.health_state["running"]:
                APP.health_async()
            rep = dict(APP.health or {}, state=APP.health_state)
            hj = APP.jobs.latest("health")
            rep["job"] = hj.to_dict() if hj else None
            rep["issues"] = [dict(i, ids=None) for i in rep.get("issues", [])]  # ids stay server-side
            return self._json(rep)
        if p.startswith("/thumb/"):
            a = APP.catalog.get(unquote(p[len("/thumb/"):]))
            if a and a.get("remote"):  # only on Fab: its listing thumbnail, fetched once and kept (~20 KB)
                t = fab.thumb_file(a)
                if not t:
                    return self._placeholder(a)
                if a.get("color") is None and Image is not None:
                    try:
                        APP.catalog.set_color(a["id"], _avg_color(Image.open(t).convert("RGB").resize((24, 14))))
                    except Exception:
                        pass
                return self._file(t, "image/jpeg")
            pv = preview_of(a)
            if not pv:
                return self._placeholder(a)
            t = APP.make_thumb(a["id"], pv)
            if t and os.path.exists(t):
                return self._file(t, "image/webp")
            return self._file(pv)
        if p.startswith("/preview/"):
            a = APP.catalog.get(unquote(p[len("/preview/"):]))
            if a and a.get("remote"):  # only on Fab: the listing's large render, straight from Fab's CDN (not stored)
                big = ((a.get("fab") or {}).get("thumb") or {}).get("l")
                if big:
                    self.send_response(302)
                    self.send_header("Location", big)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                return self._placeholder(a)
            pv = preview_of(a)
            if a and q.get("i") and (a.get("previews") or [])[1:]:  # another of the asset's preview images
                pv = a["previews"][max(0, min(int(q["i"]), len(a["previews"]) - 1))]
            if not pv:
                return self._placeholder(a)
            want = min(int(q.get("s", 1024)), 4096)
            if pv.lower().endswith(WEB_IMAGE) and os.path.getsize(pv) < 12_000_000 and "s" not in q:
                return self._file(pv)
            t = APP.make_thumb(a["id"], pv, size=want)  # tif/exr/hdr or huge: serve a web-sized render
            return self._file(t, "image/webp") if t and os.path.exists(t) else self._placeholder(a)
        if p.startswith("/api/files/"):  # what the 3D preview can load for this asset
            a = APP.catalog.get(unquote(p[len("/api/files/"):]))
            if not a:
                return self._json({"error": "not found"}, 404)
            items = []
            for i, (role, kind, path, extra) in enumerate(asset_files(a)):
                name = os.path.basename(path)
                items.append(dict(extra, idx=i, role=role, kind=kind, name=name,
                                  url="/asset-file/%s/%d/%s" % (quote(a["id"], safe=""), i, quote(name))))
            return self._json({"id": a["id"], "type": a["type"], "files": items})
        if p.startswith("/asset-file/"):  # /asset-file/<id>/<idx>/<filename>[?s=1024 for a resized texture]
            parts = p[len("/asset-file/"):].split("/")
            a = APP.catalog.get(unquote(parts[0])) if len(parts) >= 3 else None
            if a and self._cross_origin_denied(a["id"], q):
                return self._json({"error": "forbidden"}, 403)
            files = asset_files(a) if a else []
            try:
                role, kind, path, _ = files[int(parts[1])]
            except (ValueError, IndexError):
                return self._json({"error": "not found"}, 404)
            if q.get("s") and (role in ("map", "part_map", "preview")):
                t = APP.make_thumb(a["id"] + "#tex", path, size=int(q["s"]))
                if t and os.path.exists(t):
                    return self._file(t, "image/webp")
            return self._stream(path)
        if p == "/api/map":  # full-size map for the detail view
            a = APP.catalog.get(q.get("id", ""))
            path = q.get("path", "")
            if a and any(m["path"] == path for m in a.get("maps", [])):
                t = APP.make_thumb(a["id"] + "#m", path)
                return self._file(t if t and os.path.exists(t) else path)
            return self._json({"error": "not found"}, 404)
        return self._json({"error": "not found"}, 404)

    def _stream(self, path):
        """Large files (splats, meshes) in chunks with a correct length, no full read into memory."""
        tag, done = self._not_modified(path)
        if done:
            return
        try:
            size = os.path.getsize(path)
            fh = open(path, "rb")
        except OSError:
            return self._json({"error": "not found"}, 404)
        with fh:
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-cache")
            if tag:
                self.send_header("ETag", tag)
            self.end_headers()
            shutil.copyfileobj(fh, self.wfile, 4 << 20)

    def _placeholder(self, a):
        label = ((a or {}).get("format") or (a or {}).get("type") or "?").upper()
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
               '<rect width="100" height="100" fill="#20232b"/>'
               '<path d="M50 22 74 36v28L50 78 26 64V36z" fill="none" stroke="#4b5263" stroke-width="2.5"/>'
               '<path d="M26 36l24 14 24-14M50 50v28" fill="none" stroke="#4b5263" stroke-width="2.5"/>'
               '<text x="50" y="93" font-family="Segoe UI,Arial" font-size="9" fill="#7d8597" '
               'text-anchor="middle">%s</text></svg>') % label
        data = svg.encode()
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=600")
        self.end_headers()
        self.wfile.write(data)

    def _post(self):
        u = urlparse(self.path)
        p = u.path
        b = self._body()
        if p == "/api/send":
            return self._json(APP.send(b["id"], b.get("options", {}), b.get("target", "c4d")))
        if p == "/api/fab/pull":  # {id, tier: low|mid|high|raw} -> job (GET /api/jobs/<job>?wait=60 until it ends)
            try:
                job = APP.fab_pull(b.get("id", ""), b.get("tier"))
            except fab.FabError as e:
                return self._json({"ok": False, "error": str(e)})
            return self._json({"ok": True, "job": job.id, "joined": job.requests > 1})
        if p == "/api/fab/evict":  # {id}: delete the pulled files; the asset stays listed (remote again)
            try:
                size = APP.fab_evict(b.get("id", ""))
            except fab.FabError as e:
                return self._json({"ok": False, "error": str(e)})
            return self._json({"ok": True, "freed": size, "message": "Freed %.0f MB" % (size / 1e6)})
        if p == "/api/fab/index":  # re-crawl the Fab catalogue, then rescan the Fab libraries
            return self._json({"ok": True, "job": APP.fab_index_async().id})
        if p == "/api/label":  # {ids:[...], label, on}
            ids = b.get("ids") or ([b["id"]] if b.get("id") else [])
            APP.catalog.set_labels(ids, b.get("label", ""), bool(b.get("on", True)), time.time())
            return self._json({"ok": True})
        if p == "/api/saved-filter":  # {name, query} saves; {name, query: null} deletes
            name = " ".join((b.get("name") or "").split())[:60]
            if not name:
                return self._json({"ok": False, "error": "Give the filter a name"})
            APP.catalog.save_filter(name, b.get("query"), time.time())
            return self._json({"ok": True})
        if p == "/api/favorite":
            APP.catalog.set_favorite(b["id"], bool(b.get("on")), time.time())
            return self._json({"ok": True})
        if p == "/api/health/run":
            return self._json({"ok": True, "job": APP.health_async().id})
        if p == "/api/health/action":
            if b.get("do") == "clear_splat_cache":
                shutil.rmtree(os.path.join(CACHE, "splat_conv"), ignore_errors=True)
                APP.health_async()  # queued even when a check is running, so the cleared issue goes away
                return self._json({"ok": True, "message": "Cleared the converted-splat cache"})
            if b.get("do") == "rescan":
                return self._json({"ok": True, "job": APP.scan_async().id, "message": "Rescanning libraries…"})
            return self._json({"ok": False, "error": "Unknown action"}, 400)
        if p == "/api/remove":  # take assets out of AssetBridge; files on disk are never touched
            ids = [i for i in (b.get("ids") or []) if isinstance(i, str)]
            n = APP.catalog.remove_assets(ids, time.time())
            return self._json({"ok": True, "removed": n})
        if p == "/api/restore":
            libs = APP.catalog.restore([i for i in (b.get("ids") or []) if isinstance(i, str)])
            scan_jobs = sorted({APP.scan_async(lib).id for lib in libs})  # merged into one queued scan
            return self._json({"ok": True, "rescan": libs, "jobs": scan_jobs})
        if p == "/api/rescan":
            return self._json({"ok": True, "job": APP.scan_async(b.get("library")).id})
        if p == "/api/reveal":
            a = APP.catalog.get(b["id"])
            if not a:
                return self._json({"ok": False}, 404)
            if a.get("remote"):  # nothing on disk yet: show its Fab page
                webbrowser.open(a["fab"]["url"])
                return self._json({"ok": True})
            target = a.get("preview") or a["path"]
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(target)])
            return self._json({"ok": True})
        if p == "/api/pickfolder":
            return self._json({"path": pick_folder()})
        if p == "/api/libraries":
            libs = APP.cfg["libraries"]
            act = b.get("action")
            if act == "add":
                name, path = b.get("name", "").strip(), b.get("path", "").strip().strip('"')
                if not name or not path:
                    return self._json({"ok": False, "error": "Name and folder are required"})
                if not os.path.isdir(path):
                    return self._json({"ok": False, "error": "Folder not found: " + path})
                if any(l["name"] == name for l in libs):
                    return self._json({"ok": False, "error": "A library with that name exists"})
                kind = b.get("kind") or ("megascans" if os.path.exists(os.path.join(path, "library.qms")) else "generic")
                libs.append({"name": name, "kind": kind, "path": path, "enabled": True})
                save_config(APP.cfg)
                APP.scan_async(name)
                return self._json({"ok": True, "kind": kind})
            if act == "remove":
                APP.cfg["libraries"] = [l for l in libs if l["name"] != b.get("name")]
                save_config(APP.cfg)
                APP.catalog.remove_library(b.get("name"))
                return self._json({"ok": True})
            return self._json({"ok": False, "error": "unknown action"}, 400)
        return self._json({"error": "not found"}, 404)


_pick_lock = threading.Lock()


def pick_folder():
    """Native folder dialog (the browser can't hand us a real path)."""
    with _pick_lock:
        try:
            import tkinter
            from tkinter import filedialog
            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askdirectory(parent=root, title="Choose an asset library folder", mustexist=True)
            root.destroy()
            return os.path.normpath(path) if path else None
        except Exception as e:
            print("[pickfolder]", e)
            return None


def main():
    global APP
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--scan-only", action="store_true")
    ap.add_argument("--no-scan", action="store_true")
    args = ap.parse_args()

    if sys.stdout is None or sys.stderr is None:  # pythonw: no console
        os.makedirs(CACHE, exist_ok=True)
        log = open(os.path.join(CACHE, "server.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log

    cfg = load_config()
    url = "http://127.0.0.1:%d/" % cfg["port"]
    if not args.scan_only:
        try:  # already running? just open it
            import urllib.request
            urllib.request.urlopen(url + "api/status", timeout=1)
            if not args.no_browser:
                webbrowser.open(url)
            print("%s already running at %s" % (BRAND["name"], url))
            return
        except OSError:
            pass

    APP = App()
    if args.scan_only:
        t0 = time.time()
        APP.scan()
        print(json.dumps(APP.scan_state, indent=1), "in %.1fs" % (time.time() - t0))
        print(json.dumps(APP.catalog.facets(), indent=1))
        return

    srv = ThreadingHTTPServer(("127.0.0.1", cfg["port"]), Handler)
    srv.daemon_threads = True
    print("%s v%s  ->  %s" % (BRAND["name"], VERSION, url))
    if not args.no_scan:
        APP.scan_async()
        if any(l.get("kind") == "fab" for l in cfg["libraries"]) and not fab.load_index().get("items"):
            APP.fab_index_async()  # first run with a Fab library: build the index (then it rescans)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
