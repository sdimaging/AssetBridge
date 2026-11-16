"""Fab (Quixel Megascans) as a remote library: the whole free catalogue is indexed with thumbnails, and an asset's files
are pulled only when it is needed.

    index   crawl() walks Fab's public listing search -> cache/fab/index.json (metadata + thumbnail URLs, no downloads)
    browse  library kind "fab": scan_fab() yields a light "remote" asset per listing; its thumbnail is fetched and cached
            the first time a card shows it (thumb_file)
    pull    pull() reads the listing's public file list, asks Fab for a signed link, downloads the zip, extracts it into
            the library folder and turns it into a normal local Megascans asset (same id, so favourites/labels stay)
    evict   evict() deletes the pulled files; the asset goes back to remote

    py -3.13 server/fab.py crawl          # (re)build the index from the command line
    py -3.13 server/fab.py stats          # what the index holds

Only fab.com and Fab's CDNs are contacted. Fab's own endpoints are undocumented: the ones used here are those its web
front end calls (listings/search, listings/<uid>/asset-formats/<fmt>, .../files/<file>/download-info).
"""
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAB_CACHE = os.path.join(ROOT, "cache", "fab")
INDEX = os.path.join(FAB_CACHE, "index.json")
THUMBS = os.path.join(FAB_CACHE, "thumbs")

API = "https://www.fab.com/i"
SITE = "https://www.fab.com/listings/"
QUIXEL = "o-cbf5bbfdb340d6a839b5520cb31328"  # the Quixel Megascans seller
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
SORTS = ("-firstPublishedAt", "firstPublishedAt")  # both directions: cursor paging alone can skip listings
TIERS = ("low", "mid", "high", "raw")
SIDE = "fab.json"  # written into every pulled folder: which listing, tier and format it came from
PAUSE = 0.25       # between catalogue pages: gentle on Fab


class FabError(RuntimeError):
    pass


def _get(url, timeout=30, tries=5):
    """JSON GET with backoff on rate limits / transient errors."""
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and k < tries - 1:
                time.sleep(min(60, 5 * 2 ** k))
                continue
            raise FabError("Fab answered HTTP %d for %s" % (e.code, url.split("?")[0]))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if k < tries - 1:
                time.sleep(3 * (k + 1))
                continue
            raise FabError("Can't reach Fab (%s)" % e)


# ------------------------------------------------------------------ index

def _thumbs(listing):
    imgs = []
    for t in listing.get("thumbnails") or []:
        imgs += t.get("images") or []
    by_w = {}
    for i in imgs:
        if i.get("url") and i.get("width"):
            by_w[int(i["width"])] = i["url"]
    if not by_w:
        return {}
    ws = sorted(by_w)
    pick = lambda want: by_w[min(ws, key=lambda w: (w < want, abs(w - want)))]
    return {"s": pick(320), "m": pick(640), "l": pick(1280)}


def _compact(r):
    return {
        "uid": r["uid"], "title": (r.get("title") or "").strip(),
        "listing_type": r.get("listingType") or "",
        "category": (r.get("category") or {}).get("name") or "",
        "category_path": (r.get("category") or {}).get("path") or "",
        "tags": [t["name"] for t in r.get("tags") or [] if t.get("name")],
        "thumb": _thumbs(r),
        "published": r.get("publishedAt") or "",
        "formats": {f["assetFormatType"]["code"]: [(f.get("fileSizesRange") or {}).get("lower"),
                                                  (f.get("fileSizesRange") or {}).get("upper")]
                    for f in r.get("assetFormats") or [] if (f.get("assetFormatType") or {}).get("code")},
    }


def crawl(progress=None, stop=None):
    """Every free Quixel Megascans listing -> cache/fab/index.json. -> {"count", "pages"}"""
    found, pages = {}, 0
    for sort in SORTS:
        cursor = None
        while True:
            if stop and stop():
                raise FabError("stopped")
            url = "%s/listings/search?is_free=1&sellers=%s&sort_by=%s" % (API, QUIXEL, sort) + \
                ("&cursor=" + urllib.request.quote(cursor, safe="") if cursor else "")
            d = _get(url)
            pages += 1
            for r in d.get("results") or []:
                if r.get("uid"):
                    found[r["uid"]] = _compact(r)
            if progress:
                progress(len(found), pages, sort)
            cursor = (d.get("cursors") or {}).get("next")
            if not cursor:
                break
            time.sleep(PAUSE)
    os.makedirs(FAB_CACHE, exist_ok=True)
    tmp = INDEX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"crawled_at": time.time(), "seller": QUIXEL, "count": len(found),
                   "items": sorted(found.values(), key=lambda x: x["published"], reverse=True)}, fh)
    os.replace(tmp, INDEX)
    return {"count": len(found), "pages": pages}


_index_cache = {"mtime": None, "data": None, "by_uid": {}}
_index_lock = threading.Lock()


def load_index():
    """The crawled catalogue (re-read only when the file changes)."""
    with _index_lock:
        try:
            mt = os.path.getmtime(INDEX)
        except OSError:
            return {"items": [], "count": 0, "crawled_at": None}
        if _index_cache["mtime"] != mt:
            try:
                with open(INDEX, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                return {"items": [], "count": 0, "crawled_at": None}
            _index_cache.update(mtime=mt, data=data, by_uid={i["uid"]: i for i in data.get("items", [])})
        return _index_cache["data"]


def index_item(uid):
    load_index()
    return _index_cache["by_uid"].get(uid)


# ------------------------------------------------------------------ library kind "fab"

PULLABLE = ("fbx", "texture-set", "image")  # formats AssetBridge can use (gltf here = Unreal-flavoured; unreal-engine = UE only)
SKIP_TYPES = {"brush"}                      # AssetBridge leaves brushes out on purpose


def assetbridge_type(item):
    """Fab listing type + category -> AssetBridge asset type. Fab files rocks under nature-plants/rocks-stones, so only
    its trees / plants / forest subcategories are plants."""
    lt, cat = item.get("listing_type", ""), (item.get("category_path") or "").lower()
    if lt == "material":
        return "surface"
    if lt == "decal":
        return "atlas" if cat.startswith("nature-terrain/vegetation") else "decal"
    if lt in ("3d-model", "environment"):
        return "plant" if re.match(r"nature-plants/(trees|plants|forest)", cat) else "3d"
    return "3d"


def listed(item):
    """Indexed listings AssetBridge shows: not brushes, and only with a format it can import."""
    return item.get("listing_type") not in SKIP_TYPES and any(f in (item.get("formats") or {}) for f in PULLABLE)


def _ts(iso):
    try:
        return time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None


def asset_id(lib, uid):
    return "%s:%s" % (re.sub(r"[^a-z0-9]+", "-", lib["name"].lower()).strip("-"), uid)


def remote_asset(lib, item, stamp):
    """A listing that isn't on disk: enough to browse, search and filter; no files."""
    uid = item["uid"]
    kind = assetbridge_type(item)
    cats = [kind] + [c for c in (item.get("category_path") or "").split("/") if c][:2]
    return {
        "id": asset_id(lib, uid), "library": lib["name"], "type": kind, "name": item["title"] or uid,
        "path": "fab://" + uid, "preview": thumb_path(uid), "color": None, "mtime": stamp,
        "added": _ts(item.get("published")), "tags": sorted({t.lower() for t in item.get("tags", [])}),
        "categories": cats, "subtitle": item.get("category") or kind,
        "remote": True, "importable": False, "format": None, "maps": [], "meshes": [], "native": None,
        "fab": {"uid": uid, "url": SITE + uid, "thumb": item.get("thumb") or {}, "formats": item.get("formats") or {}},
    }


def pulled_folders(lib):
    """{uid: folder} for everything pulled into this library (each folder carries a fab.json)."""
    out = {}
    root = lib["path"]
    if not os.path.isdir(root):
        return out
    for t in os.scandir(root):
        if not t.is_dir() or t.name.startswith("_"):
            continue
        for a in os.scandir(t.path):
            side = os.path.join(a.path, SIDE)
            if a.is_dir() and not a.name.endswith(".part") and os.path.exists(side):  # .part = mid-extract
                try:
                    with open(side, "r", encoding="utf-8") as fh:
                        out[json.load(fh)["uid"]] = a.path
                except (OSError, ValueError, KeyError):
                    pass
    return out


def local_asset(lib, folder):
    """A pulled folder -> the same asset a Megascans library would produce, under the listing's id."""
    import scanners
    with open(os.path.join(folder, SIDE), "r", encoding="utf-8") as fh:
        side = json.load(fh)
    item = index_item(side["uid"]) or side.get("listing") or {"uid": side["uid"], "title": side.get("title", "")}
    kind = side.get("type") or assetbridge_type(item)
    mtime = os.stat(folder).st_mtime
    # plants: the Bridge layout keeps textures in Textures/Atlas; Fab zips don't, so parse them like a 3D asset
    parse_as = {"decal": "atlas", "plant": "3d"}.get(kind, kind)
    a = scanners._megascans_asset(lib, folder, os.path.basename(folder), parse_as, mtime)
    a = _fill_nested(a, folder, kind)
    for m in a["meshes"]:  # plants: Fab names variations in the file (..._VarA_LOD0.fbx), not in Var folders
        if not m.get("variation"):
            v = re.search(r"_(Var[A-Z0-9]+)_", os.path.basename(m["path"]), re.I)
            m["variation"] = v.group(1) if v else None
    a["type"] = kind
    stub = remote_asset(lib, item, mtime)
    a.update({
        "id": stub["id"], "name": stub["name"], "added": stub["added"],
        "tags": sorted(set(a.get("tags", [])) | set(stub["tags"])),
        "subtitle": stub["subtitle"], "remote": False,
        "preview": os.path.join(folder, "fab_preview.jpg") if os.path.exists(os.path.join(folder, "fab_preview.jpg")) else a.get("preview"),
        "fab": dict(stub["fab"], tier=side.get("tier"), format=side.get("format"), pulled_at=side.get("pulled_at"),
                    bytes=side.get("bytes")),
    })
    if kind == "decal":
        a["type"] = "decal"
    return scanners._finish(a)


def _fill_nested(a, folder, kind):
    """Fab zips can nest files in sub-folders (plant variations, raw tiers). The Megascans parser only looks at the top
    level (and Textures/Atlas for plants), so add what it missed."""
    import scanners
    have = {os.path.normcase(m["path"]) for m in a["maps"] + a["meshes"]}
    for dirpath, _, files in os.walk(folder):
        if os.path.normcase(dirpath) == os.path.normcase(folder):
            continue
        rel = os.path.relpath(dirpath, folder)
        var = next((p for p in rel.split(os.sep) if re.match(r"var\d+$", p, re.I)), None)
        for f in files:
            path = os.path.join(dirpath, f)
            if os.path.normcase(path) in have:
                continue
            m = scanners.MS_TEX_RE.search(f)
            if m and scanners.MS_MAP_ALIASES.get(m.group(2).lower()):
                a["maps"].append({"type": scanners.MS_MAP_ALIASES[m.group(2).lower()], "res": m.group(1).upper(),
                                  "lod": "LOD%s" % m.group(3) if m.group(3) else None, "ext": m.group(4).lower(), "path": path})
                continue
            m = scanners.MS_MESH_RE.search(f)
            if m:
                lod = "LOD%s" % m.group(1) if m.group(1) else (m.group(2) or "LOD0")
                a["meshes"].append({"path": path, "format": m.group(3).lower(), "lod": lod,
                                    "variation": var or (re.search(r"_(Var\d+)", f, re.I) or [None, None])[1]})
    a["importable"] = bool(a["maps"] or a["meshes"])
    a["format"] = "fbx" if a["meshes"] else None
    return a


def scan_fab(lib, known, progress):
    """Library kind "fab": every indexed listing, as a local asset once pulled, else a remote one."""
    idx = load_index()
    pulled = pulled_folders(lib)
    stamp = idx.get("crawled_at") or 0
    seen = set()
    for item in idx.get("items", []):
        uid = item["uid"]
        seen.add(uid)
        folder = pulled.get(uid)
        if not folder and not listed(item):
            continue
        if folder:
            progress(item["title"])
            mt = os.stat(folder).st_mtime
            prev = known.get(folder)
            if prev and prev[1] == mt:
                yield ("unchanged", prev[0])
                continue
            try:
                yield ("asset", local_asset(lib, folder))
            except Exception as e:
                print("[fab] skip %s: %s" % (folder, e))
            continue
        vpath = "fab://" + uid
        prev = known.get(vpath)
        if prev and prev[1] == stamp:
            yield ("unchanged", prev[0])
            continue
        yield ("asset", remote_asset(lib, item, stamp))
    for uid, folder in pulled.items():  # pulled, but no longer in the index (delisted, or an older crawl)
        if uid not in seen:
            try:
                yield ("asset", local_asset(lib, folder))
            except Exception as e:
                print("[fab] skip %s: %s" % (folder, e))


def local_copies(msid, exclude_library):
    """Assets with this Megascans ID in other libraries (a Fab title is too generic to match on: "Rough Concrete"
    is 138 different listings). -> [{id, library, resolutions}]"""
    if not msid or not _catalog:
        return []
    with _catalog._lock:
        rows = _catalog._db.execute(
            "SELECT id, library, data FROM assets WHERE library != ? AND json_extract(data, '$.source_id') = ?",
            (exclude_library, msid)).fetchall()
    return [{"id": r["id"], "library": r["library"], "resolutions": json.loads(r["data"]).get("resolutions", [])} for r in rows]


_catalog = None  # set by the server (bind); the CLI runs without it


def bind(catalog):
    global _catalog
    _catalog = catalog


# ------------------------------------------------------------------ thumbnails

def thumb_path(uid):
    return os.path.join(THUMBS, uid[:2], uid + ".jpg")


_thumb_locks = {}
_thumb_guard = threading.Lock()


def thumb_file(asset):
    """The card thumbnail of a remote asset: fetched from Fab's CDN the first time it's shown, then served from disk
    (~20 KB each). -> path or None"""
    fab = asset.get("fab") or {}
    uid = fab.get("uid")
    url = (fab.get("thumb") or {}).get("m") or (fab.get("thumb") or {}).get("s")
    if not uid or not url:
        return None
    out = thumb_path(uid)
    if os.path.exists(out):
        return out
    with _thumb_guard:
        lock = _thumb_locks.setdefault(uid, threading.Lock())
    with lock:
        if os.path.exists(out):
            return out
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print("[fab] thumb %s: %s" % (uid, e))
            return None
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out + ".tmp", "wb") as fh:
            fh.write(data)
        os.replace(out + ".tmp", out)
        with _thumb_guard:
            _thumb_locks.pop(uid, None)
        return out


# ------------------------------------------------------------------ pull / evict

FORMAT_PREF = PULLABLE
TIER_RE = re.compile(r"_(low|mid|high|raw|\d+k)\.zip$", re.I)
RES_TIER = {"1k": "low", "2k": "mid", "4k": "high", "8k": "raw"}  # texture sets: one zip per resolution
TIER_LABEL = {"low": "Low", "mid": "Mid", "high": "High", "raw": "Raw"}
_files_cache = {}


def files_for(uid):
    """What can be downloaded for a listing, from Fab's public file list (cached for an hour).
    -> {"format", "msid", "tiers": {tier: {"uid", "name", "size"}}}"""
    hit = _files_cache.get(uid)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    item = index_item(uid) or {}
    formats = [f for f in FORMAT_PREF if f in (item.get("formats") or {})] or list(FORMAT_PREF)
    for fmt in formats:
        try:
            d = _get("%s/listings/%s/asset-formats/%s" % (API, uid, fmt))
        except FabError:
            continue
        tiers, msid = {}, None
        for f in d.get("files") or []:
            m = TIER_RE.search(f.get("name") or "")
            if f.get("status", "ready") != "ready" or not f.get("uid"):
                continue
            raw = m.group(1).lower() if m else "file"
            tier = RES_TIER.get(raw, "raw" if raw.endswith("k") else raw)
            if tier in tiers and raw.endswith("k"):  # e.g. 16k next to 8k: keep the larger one as raw
                if (f.get("size") or 0) < (tiers[tier]["size"] or 0):
                    continue
            tiers[tier] = {"uid": f["uid"], "name": f["name"], "size": f.get("size"), "faces": f.get("faceCount"),
                           "label": raw.upper() if raw.endswith("k") else TIER_LABEL.get(tier, tier.title())}
            mm = re.search(r"_([a-z0-9]{6,10})_(?:low|mid|high|raw|\d+k)\.zip$", f["name"], re.I)
            msid = msid or (mm.group(1).lower() if mm else None)
        if tiers:
            res = {"format": fmt, "msid": msid, "tiers": tiers}
            _files_cache[uid] = (time.time(), res)
            return res
    raise FabError("Fab lists no downloadable files for this asset")


def _download_url(uid, fmt, file_uid):
    d = _get("%s/listings/%s/asset-formats/%s/files/%s/download-info" % (API, uid, fmt, file_uid))
    info = (d.get("downloadInfo") or [{}])[0]
    if not info.get("downloadUrl"):
        raise FabError("Fab gave no download link")
    return info["downloadUrl"]


def _safe_extract(zpath, dest):
    """Extract every member under dest (no absolute paths or ..), CRC-checked by zipfile as it reads."""
    root = os.path.realpath(dest)
    with zipfile.ZipFile(zpath) as z:
        for m in z.infolist():
            target = os.path.realpath(os.path.join(dest, m.filename))
            if not (target == root or target.startswith(root + os.sep)):
                raise FabError("unsafe path in the zip: %s" % m.filename)
        z.extractall(dest)
        return len(z.infolist())


def _folder_name(title, msid, uid):
    base = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")[:80] or "asset"
    return "%s_%s" % (base, msid or uid[:8])


def pull(lib, uid, tier="high", job=None):
    """Download one listing at a quality tier into the library and return its local asset.
    Pulling again at another tier replaces the files."""
    item = index_item(uid)
    if not item:
        raise FabError("This listing isn't in the Fab index (refresh the index)")
    info = files_for(uid)
    tiers = info["tiers"]
    want = tier if tier in tiers else next((t for t in (tier, "high", "mid", "low", "raw", "file") if t in tiers), None)
    if not want:
        raise FabError("No %s file for this asset" % tier)
    f = tiers[want]
    kind = assetbridge_type(item)
    tdir = os.path.join(lib["path"], kind)
    dest = os.path.join(tdir, _folder_name(item["title"], info["msid"], uid))
    dl = os.path.join(lib["path"], "_downloads")
    os.makedirs(dl, exist_ok=True)
    zpath = os.path.join(dl, f["name"] + ".part")
    step = lambda s, done=None, total=None: job and job.progress(step=s, done=done, total=total)

    step("asking Fab for the %s file" % want)
    url = _download_url(uid, info["format"], f["uid"])
    total = f.get("size") or 0
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    got = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(zpath, "wb") as out:
        total = int(r.headers.get("Content-Length") or total or 0)
        last = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            got += len(chunk)
            if time.time() - last > 0.5:
                last = time.time()
                step("downloading %s (%d of %d MB)" % (want, got >> 20, total >> 20), got >> 20, total >> 20 or None)
    if total and got != total:
        os.remove(zpath)
        raise FabError("download cut short (%d of %d bytes)" % (got, total))

    step("unpacking")
    old = pulled_folders(lib).get(uid)  # an earlier pull (another tier) is replaced once this one is complete
    tmp = dest + ".part"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        n = _safe_extract(zpath, tmp)
    except (zipfile.BadZipFile, FabError) as e:
        shutil.rmtree(tmp, ignore_errors=True)
        os.remove(zpath)
        raise FabError("the download isn't a valid zip (%s)" % e)
    thumb = (item.get("thumb") or {}).get("l")
    if thumb:  # the listing's own render becomes the local preview (sharper than the card thumbnail)
        try:
            with urllib.request.urlopen(urllib.request.Request(thumb, headers={"User-Agent": UA}), timeout=20) as r:
                with open(os.path.join(tmp, "fab_preview.jpg"), "wb") as fh:
                    fh.write(r.read())
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
    with open(os.path.join(tmp, SIDE), "w", encoding="utf-8") as fh:
        json.dump({"uid": uid, "title": item["title"], "type": kind, "tier": want, "format": info["format"],
                   "file": f["name"], "bytes": got, "msid": info["msid"], "pulled_at": time.time(),
                   "url": SITE + uid, "listing": item}, fh, indent=1)
    for d in {old, dest} - {None}:
        if os.path.isdir(d):
            shutil.rmtree(d)
    os.makedirs(tdir, exist_ok=True)
    os.replace(tmp, dest)
    os.remove(zpath)
    step("indexing")
    a = local_asset(lib, dest)
    return a, {"tier": want, "label": f.get("label") or want, "bytes": got, "files": n, "folder": dest}


def evict(lib, uid):
    """Delete a pulled asset's files (only a folder inside this library that carries a fab.json). -> remote asset"""
    folder = pulled_folders(lib).get(uid)
    if not folder:
        raise FabError("Nothing pulled for this asset")
    root = os.path.realpath(lib["path"])
    if not os.path.realpath(folder).startswith(root + os.sep):
        raise FabError("refusing to delete outside the library: %s" % folder)
    size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(folder) for f in fs)
    shutil.rmtree(folder)
    item = index_item(uid) or {"uid": uid, "title": uid}
    return remote_asset(lib, item, load_index().get("crawled_at") or 0), size


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "crawl":
        t0 = time.time()
        res = crawl(progress=lambda n, p, s: p % 50 == 0 and print("  %d pages, %d listings (%s)" % (p, n, s), flush=True))
        print("indexed %d listings in %d pages, %.0f s" % (res["count"], res["pages"], time.time() - t0))
    elif cmd == "stats":
        import collections
        idx = load_index()
        items = idx["items"]
        print("%d listings, crawled %s" % (len(items), idx.get("crawled_at") and time.ctime(idx["crawled_at"])))
        print(collections.Counter(i["listing_type"] for i in items).most_common())
        print(collections.Counter((i["listing_type"], i["category_path"].split("/")[0]) for i in items).most_common(60))
