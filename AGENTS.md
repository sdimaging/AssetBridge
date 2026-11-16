# AGENTS.md: AssetBridge

Instructions for a coding agent (Claude Code, Codex, Cursor, Copilot, …) working in this repository. They
are also written to be readable by the person the agent works for.

You will usually be asked one of two things:

- **"Set this up for me."** Follow [Part A](#part-a-set-it-up) top to bottom. Every step ends with a check.
  Don't go on until it passes, and tell the user what you found at each step.
- **"Change / fix / extend it."** Read [Part B](#part-b-how-it-works) for the map, use the recipes in
  [Part C](#part-c-recipes), and finish with the checks in [Part D](#part-d-before-you-say-its-done).

AssetBridge is a local asset browser: a Python server indexes asset folders on disk, a web UI browses them,
and receiver plugins inside Cinema 4D and Blender import whatever the user sends. Windows 10/11 only.

## Ground rules

1. **The user's asset folders are read-only.** Scanning never writes there, and neither should you. Don't
   move, rename, extract or delete anything inside a library folder unless the user asked for that exact
   operation. Tools that can write (`tools/conform.py --execute`, `tools/extract_archives.py`) dry-run
   first; show the user the plan and wait for a yes.
2. **Don't close or kill programs you didn't start.** Cinema 4D and Blender may hold unsaved work. When a
   plugin needs a restart, ask the user to restart the app.
3. **Test on a scratch library.** For anything that writes, make a small temporary library (a folder with
   a few copied assets) and point a test server at it, rather than the user's real libraries
   (see [D.3](#d3-run-a-test-server-on-other-ports)).
4. **Wait on jobs, don't sleep.** Every route that starts background work returns a job id.
   `GET /api/jobs/<id>?wait=60` returns when the job ends (or after 60 s); call it again until `state` is
   `succeeded`, `failed` or `cancelled`. Never guess how long a scan or render takes.
5. **Stay on localhost.** Every socket binds to `127.0.0.1`. The server refuses requests from other sites
   (`Handler._refused` in `server/bridge2.py`). Keep both when you change networking code.
6. **Leave internal names alone.** `bridge2_*` modules, `bridge_*` object properties, `cache/bridge2.db` and
   the `bridge2/1` wire protocol aren't user-facing. Renaming them breaks existing scenes and scripts.
7. **Never commit `config/libraries.json` or `cache/`.** They hold the user's own paths and data.

---

## Part A: set it up

Run every command in PowerShell from the repository root. `py -3` is the Python launcher that the
python.org installer adds.

### 0. Look before you install

Find out what's on the machine and tell the user:

```powershell
py -3 --version                                   # needs 3.11+
node --version                                    # optional (splat conversion)
Get-ChildItem "$env:APPDATA\Maxon" -Directory     # one folder per Cinema 4D version ("Maxon Cinema 4D 2026_XXXXXXXX")
Get-ChildItem "$env:APPDATA\Blender Foundation\Blender" -Directory   # Blender versions (4.2+, 5.3+ for splats)
Test-NetConnection 127.0.0.1 -Port 8642 -InformationLevel Quiet       # True = something already uses the web port
```

Also ask the user:

- where their asset folders are;
- whether they use Cinema 4D, Blender, or both;
- whether Octane is installed in Cinema 4D (optional: without it materials are C4D Standard);
- whether they want their own name and icon (step 2) or the default **AssetBridge**.

### 1. Prerequisites

- **Python 3.11+** from python.org, with *Add python.exe to PATH* ticked.
- **Node.js LTS** from nodejs.org (optional). Without it everything works except converting splats
  between PLY and SPZ.
- **Cinema 4D 2024+** and/or **Blender 4.2+** (5.3+ for Gaussian splats in Blender).

Check: `py -3 -c "import sys; print(sys.version)"` prints 3.11 or newer.

### 2. Name and look: `brand.json`

Skip this step if the user is happy with the defaults. Otherwise edit `brand.json`:

| key | what it controls |
|---|---|
| `name` | window title, shortcuts, the Cinema 4D **Extensions** menu entry, the Blender **File › Import** entry, messages |
| `wordmark` | the sidebar logo text, usually `name` in capitals |
| `wordmark_accent_letter` | index of one wordmark letter drawn in the accent colour (`-1` for none) |
| `exe_name` | desktop app executable and install folder (letters and digits only) |
| `app_id` | Windows taskbar identity, e.g. `YourStudio.AppName` |
| `publisher` | shown as the Blender add-on author |
| `accent` | default accent colour: `gold`, `violet`, `ember` or `teal` (users can switch in the sidebar) |
| `icon_svg` | the source of every icon (step 3) |
| `manifest`, `format_name` | file name of the optional per-asset manifest and the format's name (leave as is) |
| `ports` | local ports: `web` (UI), `c4d`, `blender`. Change them only if something else uses 8642, 24981 or 24982 |
| `c4d.command_id`, `c4d.receiver_id` | Cinema 4D plugin IDs (step 5) |

Check: `py -3 -c "import json; print(json.load(open('brand.json'))['name'])"` prints the new name.

### 3. The icon

Replace `icons/mark.svg` with the user's own square SVG (a `viewBox="0 0 64 64"` with its own background
tile works best), or point `icon_svg` at another SVG. Built-in alternatives:
`py -3 tools/make_icons.py --concept P1` to `P8`, with `--accent gold|violet|ember|teal`.
Step 6 renders the `.ico` and PNG sizes from it.

### 4. The libraries: `config/libraries.json`

Create it from `config/libraries.example.json` and list the user's asset folders. Folders can also be added
later from the app's sidebar (**+ Add**), so two or three to start is fine.

```json
{
  "libraries": [
    {"name": "Megascans", "kind": "megascans", "path": "D:\\Megascans Library", "enabled": true},
    {"name": "Downloads", "kind": "generic",   "path": "D:\\3D\\Downloads",     "enabled": true}
  ]
}
```

Use double backslashes in JSON paths. Pick the `kind` per folder:

| kind | use it for | options |
|---|---|---|
| `megascans` | a Megascans / Quixel Bridge library root (the folder that contains `Downloaded\`), or flat Megascans downloads (`<Name>_<id>_<res>\` folders with `<id>.json`) | `exclude`, `dedupe_against` |
| `generic` | everything else: Poly Haven, Poliigon, Textures.com, ShareTextures, CGAxis, KitBash3D, splat collections, loose models and texture sets. Files are grouped into assets by name; sub-folders are scanned | `exclude` (folder names), `max_depth` (default 8), `real_scale` |
| `splat_pack` | a finished Gaussian-splat pack: flat, or one folder per asset. Training datasets and work folders are skipped | `depth`, `layers`, `preview_prefer`, `tags`, `strip`, `pack` |
| `manifest` | folders that follow `ASSET_FORMAT.md` (one `asset.json` per asset) | |
| `fab` | the free Quixel Megascans catalogue on Fab, browsed without downloading it; files are pulled on demand into `path` | `tier` (default download quality: `low`, `mid`, `high`, `raw`) |

- `real_scale` (splats) points at a JSON of `{"AssetName": largest_side_in_metres}`; a `real_scale.json` in the
  library root is picked up without it.
- `layers` (splat packs), e.g. `["Volume", "Points"]`, makes `<stem>_Volume.ply` + `<stem>_Points.ply` one
  asset that imports as one splat object per layer.
- `preview_prefer` lists preview-name suffixes in order (`["thumb_both", "thumb_volume"]`); the first one
  found is the card image and the rest become view buttons.
- `"enabled": false` keeps a library in the list without scanning it.

Check: the file parses: `py -3 -c "import json; json.load(open('config/libraries.json')); print('ok')"`.

### 5. Cinema 4D plugin IDs

Every Cinema 4D plugin needs unique IDs. `brand.json` ships with Maxon's testing IDs (1000007 and 1000008).
They work, but two plugins using the same testing IDs clash. Before relying on it, the user should
register **two** free IDs at https://developers.maxon.net (Plugin IDs, needs a free Maxon account) and
put them in `brand.json` as `c4d.command_id` and `c4d.receiver_id`, then run step 6 again. You can't do
the registration for them; tell them.

### 6. Install

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

This installs the Python packages (`requirements.txt`), installs PlayCanvas `splat-transform` into
`tools/node_modules` when Node.js is present, copies `config/libraries.example.json` to
`config/libraries.json` if there isn't one yet, then runs `tools/apply_brand.py`, which:

1. renders the icon set;
2. writes the launchers `<name>.bat` and `<name> (console).bat`;
3. installs the Cinema 4D plugin into every Cinema 4D version's user plugin folder
   (`tools/install_c4d_plugin.py`; Cinema 4D 2027 and newer each get their own receiver port, so several
   versions can be open at once);
4. installs and enables the Blender add-on in the newest Blender it finds
   (`tools/install_blender_addon.py --blender "<path to blender.exe>"` if it picks the wrong one or none);
5. builds the desktop app into `%LOCALAPPDATA%\Programs\<exe_name>` with Desktop and Start-menu shortcuts.

After any later change to `brand.json` or the icon, run `py -3 tools/apply_brand.py` again (`--no-app` skips
the slower app build), then ask the user to restart Cinema 4D and Blender.

Check: the script ends without a red error. It prints a yellow warning while the testing plugin IDs are in use.

### 7. Check it works

1. Start it: the desktop shortcut, or `py -3 server\bridge2.py` (add `--no-browser` to skip opening a tab).
   The first scan of a large library can take minutes; the sidebar shows progress.
2. `curl.exe -s http://127.0.0.1:8642/api/facets` returns asset counts per library and type.
3. Wait for the scan to finish: `curl.exe -s "http://127.0.0.1:8642/api/jobs?active=1"` lists running jobs;
   wait on each id with `?wait=60` (rule 4).
4. Ask the user to restart Cinema 4D. **Extensions › <name>** exists and the app's sidebar shows Cinema 4D
   connected. They send a surface or a model; it lands in the active document.
5. The same for Blender: **File › Import › <name>**, sidebar shows Blender connected, send something.
6. **Library health** in the sidebar lists anything that needs attention.

### 8. Troubleshooting

| symptom | cause and fix |
|---|---|
| nothing happens on send | the receiver isn't running: restart the app after installing; the C4D console or Blender system console says `listening on 127.0.0.1:<port>` |
| `cannot listen on … port` | another program uses the port: change `ports` in `brand.json`, run `tools/apply_brand.py`, restart everything |
| an old window opens | the desktop app is single-instance: close every `<exe_name>.exe` first |
| splat conversion error | install Node.js LTS, run `setup.ps1` again |
| a library shows 0 assets | wrong `kind` or path (check backslashes); the server log says why |
| thumbnails missing for some models | they ship without a preview; Blender renders one in the background after a scan (a `previews` job) |
| `cross-site request refused` | something posted from another origin; only the app's own page may (rule 5) |

Logs: `cache/server.log`, or run `<name> (console).bat` to watch live. To start over, delete `cache/`
(index, thumbnails and conversions rebuild themselves).

---

## Part B: how it works

```
web/ (index.html, app.js, viewer.js)            the UI: grid, filters, detail panel, 3D preview (three.js + Spark)
   │  HTTP JSON, 127.0.0.1:<web port>
server/bridge2.py                               HTTP routes, App (config, scans, thumbnails, sends), startup
   ├── scanners.py   folders -> asset records   (one scan_* function per library kind)
   ├── catalog.py    SQLite + FTS5 index        (search, facets, favourites, labels, sends, removed)
   ├── send.py       asset + options -> payload (resolution / LOD / variation / splat format), TCP client
   ├── jobs.py       one lifecycle for every background job (see STATE_MACHINES.md)
   ├── health.py     library health report      tagging.py  smart tags     splats.py  splat headers
   ├── fab.py        the remote Fab library (index, thumbnails, pull, evict)
   │  TCP, one JSON line each way ("bridge2/1")
   ├── c4d/Bridge2/bridge2.pyp        receiver: socket thread -> main-thread job queue, menu command
   │   c4d/Bridge2/bridge2_import.py  importer: geometry, Octane / Standard materials, HDRIs, splats (reloaded per send)
   └── blender/bridge2_blender/       __init__.py receiver, core.py importer + Principled materials, api.py scripting
app/desktop_app.py    the native window (WebView2) that starts the server if it isn't running
```

### B.1 The life of an asset

1. **Scan.** `App.scan_async()` queues a `scan` job. For each enabled library, `scanners.SCANNERS[kind]` walks
   the folder and yields asset records. Megascans scans are incremental (folder mtime); `SCAN_VERSION` in
   `scanners.py` forces a full re-parse when bumped.
2. **Index.** `catalog.upsert()` stores each record as JSON plus searchable columns. Assets that disappear
   from disk drop out of the index; assets the user removed (the `removed` table) are never re-added by a scan.
3. **Browse.** `GET /api/assets` pages through `catalog.search()` (query, type, library, favourites, label,
   colour, filters). `/thumb/<id>` serves a cached WebP; `/preview/<id>` the full preview.
4. **Send.** `POST /api/send {id, options, target}` runs a `send` job: `send.build_payload()` resolves the
   exact files (resolution, LOD, variations, splat format, converting PLY↔SPZ if the target needs it), and
   `send.send()` ships the payload to the receiver. `GET /api/payload/<id>?target=c4d&lod=LOD2` shows the
   payload without sending.
5. **Import.** The receiver's socket thread queues the job; the main thread imports it (geometry, materials,
   HDRI, splats) and replies. Every imported root carries the asset id and a signature of the options, so a
   repeat send with the same signature becomes an instance instead of a second import.

### B.2 The asset record

What a scanner yields, and what `GET /api/asset/<id>` returns (plus a few UI fields):

| field | meaning |
|---|---|
| `id` | stable: `<library slug>:<folder name or a short hash of the relative path>`; favourites, labels and history hang off it |
| `library`, `type`, `name`, `path` | `type` is one of `3d`, `plant`, `surface`, `atlas`, `decal`, `hdri`, `splat` |
| `maps` | `[{type, res, path, …}]`; types: albedo, normal, roughness, gloss, metalness, specular, ao, displacement, bump, opacity, translucency, cavity, emission, … |
| `meshes` | `[{path, format, lod, res, variation, proxy}]` |
| `splats`, `layers` | splat files `[{path, format, version, layer}]`; `layers` for packs that import several splats together |
| `preview`, `previews` | the card image, plus extra views |
| `resolutions`, `lods`, `variations`, `map_types` | derived by `scanners._finish()`; always call it at the end of a scanner |
| `tags`, `color`, `subtitle` | vendor tags, average colour (sort by colour), the card's second line |

### B.3 Library kinds and the scanner contract

A scanner is `scan_<kind>(lib, known, progress)`, registered in `scanners.SCANNERS`:

- `lib` is the library's entry from `config/libraries.json` (its options included);
- `known` maps already-indexed paths to their stored state, for incremental scans;
- `progress(n)` reports how many folders it has seen;
- it yields `("asset", record)` for every asset it finds, and never writes to the library folder.

The generic scanner splits file names into tokens. Map synonyms are in `MAP_SYNONYMS` and `SINGLE_LETTER`,
two-word names in `TWO_TOKEN`, modifiers (resolution, LOD, bit depth, …) in `MOD_RE`, folders it skips in
`SKIP_DIRS` / `SKIP_DIR_RE`, texture sub-folders in `TEX_SUBDIRS`. Megascans parsing uses `MS_TEX_RE`,
`MS_MESH_RE` and `MS_MAP_ALIASES`.

### B.4 Receivers and the wire protocol

`send.py` opens a TCP connection to the receiver and sends one JSON object per line:
`{"proto": "bridge2/1", "action": "ping"}` or `{"proto": "bridge2/1", "action": "import", "target": …, …}`.
The reply is one JSON line. Receivers never touch the scene from the socket thread: they queue the job,
the main thread runs it, and the socket thread waits up to 600 s for the result. If the server stops
waiting first, the queued import is abandoned instead of landing later. The server waits slightly longer
than the receiver (`SEND_TIMEOUT`), so the receiver's own timeout message reaches the user.

- **Cinema 4D:** `bridge2.pyp` (restart Cinema 4D after changing it) and `bridge2_import.py` (reloaded on every
  send; re-run `tools/install_c4d_plugin.py`, no restart). Materials: `build_octane()` (Octane Universal),
  `build_standard()`; splats: `_run_splat()`; HDRIs: `_run_hdri()`. Cinema 4D 2026 and older listen on the
  brand's `c4d` port, 2027 and newer on `c4d + 2 × (version − 2026)`; `server/bridge2.py c4d_receivers()`
  pings them all, and `pick_c4d()` chooses: the release in config `c4d_prefer` if it's open, else one with
  Octane, newest first.
- **Blender:** `__init__.py` (receiver, menu entry), `core.py` (import, `build_material()`), `api.py` (headless
  scripting: `find_assets`, `build_payload`, `import_asset`; it reads `cache/bridge2.db` read-only).
  Re-run `tools/install_blender_addon.py` after changes, then restart Blender.

### B.5 Background work

Scans, health checks, preview renders, conversions and sends are jobs (`server/jobs.py`) with one state
machine: `queued → running → succeeded | failed | cancelled`. A second request while one runs either
queues and merges (scans, health, previews) or joins the running job (conversions, sends). The full
map of job kinds, receiver states and UI state is in `STATE_MACHINES.md`.

### B.6 Caches (all under `cache/`, safe to delete)

`bridge2.db` (the catalogue), `thumbs/` (card WebPs), `renders/` (Blender-rendered previews for models
without one), `splat_conv/` (converted splats), `server.log`.
`fab/index.json` and `fab/thumbs/` belong to the Fab library.

### B.7 Security model

The server and both receivers bind to `127.0.0.1` only. `Handler._refused()` rejects any request whose
`Host` isn't localhost (DNS rebinding) and any POST whose `Origin` isn't the app's own page (cross-site
request forgery). Non-browser clients send no `Origin` and are unaffected. `/asset-file/` serves only
files that belong to an indexed asset. There is no authentication beyond that: anything that can open a
TCP connection to localhost can use it, as with any local dev server.

### B.8 HTTP API

| method | path | does |
|---|---|---|
| GET | `/api/status[?c4d=1]` | version, scan state, receiver status |
| GET | `/api/assets?q=&type=&library=&fav=1&recent=1&label=&color=&f=&sort=&offset=&limit=` | a page of assets (`f` = comma-separated filters: `has:normal`, `no:preview`, `res>=4K`, `fmt:fbx`, `native`, `blender`, `conformed`) |
| GET | `/api/asset/<id>` | the full asset record |
| GET | `/api/facets`, `/api/tags` | counts per library / type, smart-tag counts |
| GET | `/api/payload/<id>?target=c4d&lod=&resolution=` | what a send would ship, without sending |
| GET | `/api/files/<id>` | the asset's files with download URLs |
| GET | `/api/jobs[?active=1&kind=]`, `/api/jobs/<id>[?wait=60]` | job list, one job (blocks up to `wait` seconds) |
| GET | `/api/health` | the library health report (starts a check on first call) |
| GET | `/api/libraries`, `/api/removed` | library config, assets the user removed |
| GET | `/thumb/<id>`, `/preview/<id>[?i=N]`, `/asset-file/<id>/<n>/<name>` | card image, preview image, a file of the asset |
| POST | `/api/send` `{id, options, target}` | send to `c4d` or `blender` (a job; replies when the import finishes) |
| POST | `/api/rescan` `{library}` | queue a scan (all libraries if none given) |
| POST | `/api/libraries` `{action: "add"/"remove", name, path, kind}` | edit the library list |
| POST | `/api/favorite`, `/api/label`, `/api/remove`, `/api/restore` | favourites, labels, take out of / back into the library (files are never touched) |
| POST | `/api/health/run`, `/api/health/action` | re-run the health check, act on an issue |
| POST | `/api/reveal` `{id}` | show the asset in Explorer |
| GET | `/api/fab/files?id=`, `/api/fab/index` | a Fab asset's quality tiers and what's on disk; index state |
| POST | `/api/fab/pull` `{id, tier}`, `/api/fab/evict` `{id}`, `/api/fab/index` | download, free up space, re-crawl (jobs) |

### B.9 The Fab library

`server/fab.py` crawls Fab's public listing search for the free Quixel Megascans listings into
`cache/fab/index.json` and serves each as a *remote* asset (virtual path `fab://<uid>`, thumbnail only).
A pull downloads one quality tier into `<library path>\<type>\<Title>_<id>\` with a `fab.json` beside it;
from then on it is a normal local Megascans asset with the same id. An evict deletes that folder again.
The state lives on disk (a folder with `fab.json` is local), so a scan always derives it. It relies on
Fab's website endpoints, which aren't a documented API and can change; if pulls start failing, check
`files_for()` and `_download_url()` first. Respect Fab's terms and each asset's licence.

---

## Part C: recipes

### C.1 A vendor's file names aren't recognised

1. Reproduce: add the folder as a `generic` library in a scratch config (D.3) and look at
   `GET /api/asset/<id>`. Which maps or meshes are missing or misnamed?
2. Add the token to `MAP_SYNONYMS` / `SINGLE_LETTER` / `TWO_TOKEN`, or the modifier to `MOD_RE`, in
   `server/scanners.py`. Keep the change narrow; other vendors use the same scanner.
3. Bump `SCAN_VERSION`, rescan, and check the asset again **and** an asset from another library, so
   nothing else changed.

### C.2 A new library kind

Write `scan_<kind>(lib, known, progress)` in `server/scanners.py` following B.3, end every record with
`_finish()`, register it in `SCANNERS`, document its options in this file's step 4 table, and add a test in
the style of `tools/test_packs.py` (it builds tiny fake files in a temp folder).

### C.3 A new send option

1. Add the control to the detail panel in `web/index.html` / `web/app.js` (`S.opts` holds the options; `saveOpts()`
   persists them).
2. Read it in `send.build_payload()` if it changes which files are sent, or pass it through the payload to the
   receivers if it changes how they import.
3. Handle it in **both** receivers (`bridge2_import.py` and `blender/bridge2_blender/core.py`), or disable the
   control for the target that can't do it.
4. If it changes the result, it must be part of `send.signature()`, so a repeat send with a different value
   imports again instead of becoming an instance.

### C.4 Change how materials are built

Cinema 4D: `build_octane()` / `build_standard()` in `c4d/Bridge2/bridge2_import.py`. Blender:
`build_material()` in `blender/bridge2_blender/core.py`. Colour maps are sRGB, data maps linear (Non-Color
in Blender); gloss maps are inverted into roughness. Normal maps are used as they are (the health check flags
libraries that only have DirectX normals). Change one app at a time and compare the same asset in both.

### C.5 A filter or a health check

Filters: the token is parsed in `catalog._filter_pred()` (or `AVAIL_SQL` for SQL-only ones), and listed in
`FILTER_GROUPS` in `web/app.js`. Health checks: add a `(code, severity, title, …)` entry next to the others in
`server/health.py`; each issue lists the asset ids it applies to, and the UI opens them in the grid.

### C.6 Anything that runs in the background

Make it a job: `APP.jobs.submit(kind, fn, key=…, label=…, if_busy="queue" | "join")`, report with
`job.progress(step=…, done=…, total=…)`, and return a JSON-able result. Return the job id from the route that
starts it. Add the kind to the table in `STATE_MACHINES.md`.

---

## Part D: before you say it's done

### D.1 Tests

```powershell
py -3 tools\test_jobs.py      # the job state machine
py -3 tools\test_packs.py     # splat packs and layered payloads
py -3 tools\test_fab.py       # the Fab library (offline; --live pulls one small real asset)
```

Each exits 0 and prints `all passed`.

### D.2 Syntax

```powershell
py -3 -m compileall -q server tools c4d blender app
node --check web\app.js; node --check web\viewer.js
```

### D.3 Run a test server on other ports

Don't test against the user's running copy or libraries. Copy the repository to a temporary folder, give the
copy other ports in its `brand.json` (for example `web` 8742), point its `config/libraries.json` at a scratch
library, and start it with `py -3 server\bridge2.py --no-browser`. Use `--scan-only` to scan once and print the
counts. Don't install the copy's plugins: they would replace the user's.

### D.4 Look at it

For UI changes, open the test server in a browser and check the browser console for errors. For import
changes, `GET /api/payload/<id>?target=…` shows the files and options, and the user can confirm the result in
Cinema 4D or Blender. Say plainly what you could and couldn't check yourself.

### D.5 Report

Tell the user what changed, what you tested and how, and anything left unverified (for example "not tried in
Cinema 4D 2024").
