"""SQLite catalog: one row per asset, FTS5 over name/tags/categories."""
import json
import sqlite3
import threading

import tagging

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id        TEXT PRIMARY KEY,
    library   TEXT NOT NULL,
    type      TEXT NOT NULL,
    name      TEXT NOT NULL,
    color     TEXT,
    path      TEXT NOT NULL,
    preview   TEXT,
    mtime     REAL,
    added     REAL,
    data      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS assets_library ON assets(library);
CREATE INDEX IF NOT EXISTS assets_type ON assets(type);
CREATE INDEX IF NOT EXISTS assets_name ON assets(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS assets_added ON assets(added);
CREATE INDEX IF NOT EXISTS assets_lib_type ON assets(library, type);
CREATE INDEX IF NOT EXISTS assets_path ON assets(path);
CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5(id UNINDEXED, name, tags, categories);
CREATE TABLE IF NOT EXISTS favorites (id TEXT PRIMARY KEY, at REAL);
CREATE TABLE IF NOT EXISTS sends (id TEXT, at REAL, ok INTEGER);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS labels (id TEXT, label TEXT, at REAL, PRIMARY KEY (id, label));
CREATE INDEX IF NOT EXISTS labels_label ON labels(label);
CREATE TABLE IF NOT EXISTS saved_filters (name TEXT PRIMARY KEY, query TEXT, at REAL);
CREATE TABLE IF NOT EXISTS removed (id TEXT PRIMARY KEY, library TEXT, name TEXT, path TEXT, at REAL);
CREATE TABLE IF NOT EXISTS asset_tags (id TEXT, facet TEXT, tag TEXT, PRIMARY KEY (id, facet, tag));
CREATE INDEX IF NOT EXISTS asset_tags_tag ON asset_tags(tag);
"""


class Catalog:
    def __init__(self, path):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.execute("PRAGMA journal_mode=WAL")

    def get_meta(self, k, default=None):
        with self._lock:
            r = self._db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set_meta(self, k, v):
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, str(v)))
            self._db.commit()

    # ---- writes -------------------------------------------------------
    def known_mtimes(self, library):
        with self._lock:
            rows = self._db.execute("SELECT id, path, mtime FROM assets WHERE library=?", (library,))
            return {r["path"]: (r["id"], r["mtime"]) for r in rows}

    def _write_tags(self, asset):
        smart = tagging.smart_tags(asset)
        self._db.execute("DELETE FROM asset_tags WHERE id=?", (asset["id"],))
        self._db.executemany("INSERT OR IGNORE INTO asset_tags(id,facet,tag) VALUES(?,?,?)",
                             [(asset["id"], f, t) for f, v in smart.items() for t in v])
        return smart

    def retag_all(self):
        """Recompute smart tags + search text for every asset (vocabulary changed)."""
        with self._lock:
            rows = self._db.execute("SELECT id, name, data FROM assets").fetchall()
            for r in rows:
                d = json.loads(r["data"])
                smart = self._write_tags(d)
                self._db.execute("DELETE FROM assets_fts WHERE id=?", (r["id"],))
                self._db.execute("INSERT INTO assets_fts(id,name,tags,categories) VALUES(?,?,?,?)",
                                 (r["id"], r["name"], " ".join(tagging.clean_tags(d.get("tags", [])) + tagging.flat(smart)),
                                  " ".join(d.get("categories", []))))
            self._db.commit()
        return len(rows)

    def tag_counts(self, library=None, type_=None):
        """{facet: [(tag, n), ...]} over the current library / type scope."""
        where, args = [], []
        if library:
            where.append("a.library=?")
            args.append(library)
        if type_:
            where.append("a.type=?")
            args.append(type_)
        sql = ("SELECT t.facet, t.tag, COUNT(*) n FROM asset_tags t JOIN assets a ON a.id=t.id" +
               (" WHERE " + " AND ".join(where) if where else "") + " GROUP BY t.facet, t.tag ORDER BY n DESC")
        out = {}
        with self._lock:
            key = (library, type_, self._db.total_changes)  # any write on this connection invalidates it
            cache = getattr(self, "_tag_cache", None)
            if cache and cache[0] == key:
                return cache[1]
            for r in self._db.execute(sql, args):
                out.setdefault(r["facet"], []).append((r["tag"], r["n"]))
            self._tag_cache = (key, out)
        return out

    def tags_for(self, asset_id):
        with self._lock:
            out = {}
            for r in self._db.execute("SELECT facet, tag FROM asset_tags WHERE id=? ORDER BY facet, tag", (asset_id,)):
                out.setdefault(r["facet"], []).append(r["tag"])
            return out

    def upsert(self, asset, now):
        smart = tagging.smart_tags(asset)
        tags = " ".join(tagging.clean_tags(asset.get("tags", [])) + tagging.flat(smart))
        cats = " ".join(asset.get("categories", []))
        with self._lock:
            row = self._db.execute("SELECT added FROM assets WHERE id=?", (asset["id"],)).fetchone()
            added = row["added"] if row else (asset.get("added") or asset.get("mtime") or now)
            self._db.execute(
                "INSERT OR REPLACE INTO assets(id,library,type,name,color,path,preview,mtime,added,data)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (asset["id"], asset["library"], asset["type"], asset["name"], asset.get("color"),
                 asset["path"], asset.get("preview"), asset.get("mtime"), added, json.dumps(asset)))
            self._db.execute("DELETE FROM assets_fts WHERE id=?", (asset["id"],))
            self._db.execute("INSERT INTO assets_fts(id,name,tags,categories) VALUES(?,?,?,?)",
                             (asset["id"], asset["name"], tags, cats))
            self._write_tags(asset)

    def remove_missing(self, library, seen_ids):
        with self._lock:
            ids = [r["id"] for r in self._db.execute("SELECT id FROM assets WHERE library=?", (library,))]
            gone = [i for i in ids if i not in seen_ids]
            for i in gone:
                self._db.execute("DELETE FROM assets WHERE id=?", (i,))
                self._db.execute("DELETE FROM assets_fts WHERE id=?", (i,))
                self._db.execute("DELETE FROM asset_tags WHERE id=?", (i,))
            return len(gone)

    def remove_library(self, library):
        with self._lock:
            self._db.execute("DELETE FROM assets_fts WHERE id IN (SELECT id FROM assets WHERE library=?)", (library,))
            self._db.execute("DELETE FROM assets WHERE library=?", (library,))
            self._db.commit()

    # ---- removed from the library (files on disk untouched; scans skip these ids) ----------
    def remove_assets(self, ids, now):
        n = 0
        with self._lock:
            for i in ids:
                r = self._db.execute("SELECT library, name, path FROM assets WHERE id=?", (i,)).fetchone()
                if not r:
                    continue
                self._db.execute("INSERT OR REPLACE INTO removed(id,library,name,path,at) VALUES(?,?,?,?,?)",
                                 (i, r["library"], r["name"], r["path"], now))
                for t in ("assets", "assets_fts", "favorites", "labels", "asset_tags"):
                    self._db.execute("DELETE FROM %s WHERE id=?" % t, (i,))
                n += 1
            self._db.commit()
        return n

    def splat_assets(self):
        with self._lock:
            return [json.loads(r["data"]) for r in self._db.execute("SELECT data FROM assets WHERE type='splat'")]

    def removed_ids(self):
        with self._lock:
            return {r["id"] for r in self._db.execute("SELECT id FROM removed")}

    def removed_list(self):
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT id, library, name, path, at FROM removed ORDER BY at DESC")]

    def restore(self, ids):
        """Forget the removal; the next scan of each library brings the assets back. -> libraries to rescan."""
        libs = set()
        with self._lock:
            for i in ids:
                r = self._db.execute("SELECT library FROM removed WHERE id=?", (i,)).fetchone()
                if r:
                    libs.add(r["library"])
                    self._db.execute("DELETE FROM removed WHERE id=?", (i,))
            self._db.commit()
        return sorted(libs)

    def commit(self):
        with self._lock:
            self._db.commit()

    def set_favorite(self, asset_id, on, now):
        with self._lock:
            if on:
                self._db.execute("INSERT OR REPLACE INTO favorites(id,at) VALUES(?,?)", (asset_id, now))
            else:
                self._db.execute("DELETE FROM favorites WHERE id=?", (asset_id,))
            self._db.commit()

    # ---- labels / saved filters ----------------------------------------
    def set_labels(self, ids, label, on, now):
        label = " ".join(label.split())[:48]
        if not label:
            return
        with self._lock:
            for i in ids:
                if on:
                    self._db.execute("INSERT OR IGNORE INTO labels(id,label,at) VALUES(?,?,?)", (i, label, now))
                else:
                    self._db.execute("DELETE FROM labels WHERE id=? AND label=?", (i, label))
            self._db.commit()

    def labels_for(self, asset_id):
        with self._lock:
            return [r["label"] for r in self._db.execute("SELECT label FROM labels WHERE id=? ORDER BY label COLLATE NOCASE", (asset_id,))]

    def label_counts(self):
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT l.label, COUNT(*) n FROM labels l JOIN assets a ON a.id=l.id GROUP BY l.label ORDER BY l.label COLLATE NOCASE")]

    def saved_filters(self):
        with self._lock:
            return [{"name": r["name"], "query": json.loads(r["query"])} for r in
                    self._db.execute("SELECT name, query FROM saved_filters ORDER BY name COLLATE NOCASE")]

    def save_filter(self, name, query, now):
        with self._lock:
            if query is None:
                self._db.execute("DELETE FROM saved_filters WHERE name=?", (name,))
            else:
                self._db.execute("INSERT OR REPLACE INTO saved_filters(name,query,at) VALUES(?,?,?)", (name, json.dumps(query), now))
            self._db.commit()

    def log_send(self, asset_id, ok, now):
        with self._lock:
            self._db.execute("INSERT INTO sends(id,at,ok) VALUES(?,?,?)", (asset_id, now, 1 if ok else 0))
            self._db.commit()

    # ---- reads --------------------------------------------------------
    def get(self, asset_id):
        with self._lock:
            r = self._db.execute(
                "SELECT a.data, f.id IS NOT NULL AS fav FROM assets a LEFT JOIN favorites f ON f.id=a.id WHERE a.id=?",
                (asset_id,)).fetchone()
        if not r:
            return None
        d = json.loads(r["data"])
        d["favorite"] = bool(r["fav"])
        return d

    def set_color(self, asset_id, color):
        with self._lock:
            self._db.execute("UPDATE assets SET color=? WHERE id=? AND color IS NULL", (color, asset_id))

    def search(self, q="", library=None, type_=None, fav=False, recent=False, sort="name",
               offset=0, limit=200, color=None, label=None, filters=None, ids=None):
        where, args, joins = [], [], ""
        if label:
            where.append("a.id IN (SELECT id FROM labels WHERE label=?)")
            args.append(label)
        for f in [f for f in (filters or []) if f.startswith("tag:")]:  # smart tags: indexed, AND-ed
            where.append("a.id IN (SELECT id FROM asset_tags WHERE tag=?)")
            args.append(f[4:].lower())
        filters = [f for f in (filters or []) if not f.startswith("tag:")]
        for f in [f for f in filters if f in AVAIL_SQL]:  # on disk / only on Fab: in SQL, no JSON per row
            where.append(AVAIL_SQL[f])
        filters = [f for f in filters if f not in AVAIL_SQL]
        if ids is not None:
            where.append("a.id IN (%s)" % ",".join("?" * len(ids)) if ids else "0")
            args.extend(ids)
        if q:
            joins += " JOIN assets_fts ON assets_fts.id=a.id"
            where.append("assets_fts MATCH ?")
            args.append(_fts_query(q))
        if library:
            where.append("a.library=?")
            args.append(library)
        if type_:
            where.append("a.type=?")
            args.append(type_)
        if fav:
            where.append("f.id IS NOT NULL")
        if recent:
            where.append("a.id IN (SELECT id FROM sends)")
        order = {
            "name": "a.name COLLATE NOCASE",
            "newest": "a.added DESC",
            "oldest": "a.added ASC",
            "type": "a.type, a.name COLLATE NOCASE",
            "used": "(SELECT MAX(at) FROM sends s WHERE s.id=a.id) DESC, a.name COLLATE NOCASE",
        }.get(sort, "a.name COLLATE NOCASE")
        if q and sort == "relevance":
            order = "bm25(assets_fts)"
        preds = [p for p in (_filter_pred(f) for f in filters) if p]
        if preds:  # map / resolution / format filters are about files: only assets on disk have any
            where.append(AVAIL_SQL["local"])
        tail = joins + (" WHERE " + " AND ".join(where) if where else "")
        cols = "a.id,a.library,a.type,a.name,a.color," + ("a.data," if preds else "") + " f.id IS NOT NULL AS fav"
        sql = "SELECT " + cols + " FROM assets a LEFT JOIN favorites f ON f.id=a.id" + tail + " ORDER BY " + order
        paged = not preds and not (color and _rgb(color))  # the database can count and page by itself
        with self._lock:
            try:
                if paged:
                    total = self._db.execute("SELECT COUNT(*) FROM assets a LEFT JOIN favorites f ON f.id=a.id" + tail,
                                             args).fetchone()[0]
                    rows = self._db.execute(sql + " LIMIT ? OFFSET ?", args + [int(limit), int(offset)]).fetchall()
                else:
                    rows = self._db.execute(sql, args).fetchall()
            except sqlite3.OperationalError:
                return 0, []
        if not paged:
            # the row JSON is read for every match only when a Python filter needs it; otherwise just for the page
            if preds:
                rows = [r for r in rows if all(p(json.loads(r["data"])) for p in preds)]
            total = len(rows)
            if color:
                target = _rgb(color)
                rows = sorted(rows, key=lambda r: _dist(_rgb(r["color"]), target))
            rows = rows[offset:offset + limit]
        page = rows
        if preds:
            datas = {r["id"]: r["data"] for r in page}
        else:
            with self._lock:
                datas = {}
                ids_ = [r["id"] for r in page]
                for k in range(0, len(ids_), 500):
                    chunk = ids_[k:k + 500]
                    datas.update({x["id"]: x["data"] for x in self._db.execute(
                        "SELECT id, data FROM assets WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk)})
        out = []
        for r in page:
            d = json.loads(datas.get(r["id"]) or "{}")
            out.append({
                "id": r["id"], "library": r["library"], "type": r["type"], "name": r["name"],
                "color": r["color"], "fav": bool(r["fav"]),
                "resolutions": d.get("resolutions", []), "has_thumb": bool(d.get("preview")),
                "format": d.get("format"), "importable": d.get("importable", True),
                "subtitle": d.get("subtitle", ""),
                "blender_ok": blender_ok(d),
                "remote": bool(d.get("remote")),
                "fab_tier": (d.get("fab") or {}).get("tier"),
                **({"tv": int(d["fab"]["pulled_at"])} if (d.get("fab") or {}).get("pulled_at") else {}),
            })
        return total, out

    def all_colors(self, ids_filter=None):
        with self._lock:
            return {r["id"]: r["color"] for r in self._db.execute("SELECT id,color FROM assets")}

    def facets(self):
        with self._lock:
            libs = [dict(r) for r in self._db.execute(
                "SELECT library, COUNT(*) n FROM assets GROUP BY library ORDER BY library")]
            types = [dict(r) for r in self._db.execute(
                "SELECT type, COUNT(*) n FROM assets GROUP BY type ORDER BY n DESC")]
            lt = [dict(r) for r in self._db.execute(
                "SELECT library, type, COUNT(*) n FROM assets GROUP BY library, type")]
            fav = self._db.execute("SELECT COUNT(*) FROM favorites f JOIN assets a ON a.id=f.id").fetchone()[0]
            rec = self._db.execute("SELECT COUNT(DISTINCT s.id) FROM sends s JOIN assets a ON a.id=s.id").fetchone()[0]
            total = self._db.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
            remote = self._db.execute("SELECT COUNT(*) FROM assets WHERE path GLOB 'fab://*'").fetchone()[0]
        return {"libraries": libs, "types": types, "library_types": lt,
                "favorites": fav, "recent": rec, "total": total, "remote": remote}

    def previews(self):
        with self._lock:
            return [(r["id"], r["preview"], r["color"]) for r in self._db.execute(
                "SELECT id, preview, color FROM assets WHERE preview IS NOT NULL AND path NOT GLOB 'fab://*'")]


# availability filters (Fab libraries): remote assets have a virtual fab://<uid> path until they are pulled
AVAIL_SQL = {"local": "a.path NOT GLOB 'fab://*'", "remote": "a.path GLOB 'fab://*'"}  # GLOB: can use assets_path

BLENDER_MESH = {"fbx", "glb", "gltf", "abc", "usd", "usda", "usdc", "usdz", "obj", "ply", "stl"}  # Blender 5 dropped Collada (.dae)


def blender_ok(d):
    """Geometry assets need a mesh Blender can open (a bundled .c4d alone isn't enough)."""
    if d.get("type") not in ("3d", "plant"):
        return True
    return any(m.get("format") in BLENDER_MESH for m in d.get("meshes", []))


MESH_FORMATS = {"fbx", "glb", "gltf", "obj", "abc", "usd", "usda", "usdc", "usdz", "dae", "3ds", "stl", "ply", "c4d"}


def _res_n(r):
    try:
        return int(str(r).upper().rstrip("K"))
    except ValueError:
        return 0


def _filter_pred(f):
    """Advanced filter tokens: has:<map|proxy|preview|mesh>, no:<same>, res>=<N>K, fmt:<ext>, native, blender, conformed."""
    kind, _, val = f.partition(":")
    val = val.lower()
    if f.startswith("res>="):
        need = _res_n(f[5:])
        return lambda d: max([_res_n(r) for r in d.get("resolutions", [])] or [0]) >= need
    def has(d, what):
        if what == "proxy":
            return any(m.get("proxy") for m in d.get("meshes", []))
        if what == "preview":
            return bool(d.get("preview"))
        if what == "mesh":
            return bool(d.get("meshes")) or bool(d.get("native"))
        return what in (d.get("map_types") or [])
    if kind == "has":
        return lambda d: has(d, val)
    if kind == "no":
        return lambda d: not has(d, val)
    if kind == "fmt":
        def fmt(d):
            exts = {m["format"] for m in d.get("meshes", [])} | {s["format"] for s in d.get("splats", [])}
            if d.get("native"):
                exts.add("c4d")
            return val in exts
        return fmt
    if f == "native":
        return lambda d: bool(d.get("native"))
    if f == "blender":
        return lambda d: d.get("type") not in ("3d", "plant") or blender_ok(d)
    if f == "conformed":
        return lambda d: bool(d.get("conformed"))
    return None


def _rgb(hexstr):
    try:
        h = hexstr.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (AttributeError, ValueError, IndexError):
        return None


def _dist(a, b):
    if a is None:
        return 1e9
    # weighted RGB ("redmean") is plenty for browsing
    rm = (a[0] + b[0]) / 2
    dr, dg, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return (2 + rm / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rm) / 256) * db * db


def _fts_query(q):
    # every word must match as a prefix: "red bri" -> "red"* AND "bri"*
    words = [w.replace('"', "") for w in q.split() if w.strip('"')]
    return " AND ".join('"%s"*' % w for w in words) or '""'
