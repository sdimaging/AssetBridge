# AssetBridge

A local asset browser for Cinema 4D and Blender. Point it at the folders where your downloads already
live (Megascans, Poly Haven, Poliigon, Textures.com, CGAxis, KitBash3D, Gaussian splats, loose models
and texture sets). It gives you one fast, searchable thumbnail grid and sends any asset into either app
in one click, with the materials already built.

Everything runs on your own machine. Nothing is uploaded: the window talks to a small server on
`127.0.0.1`, and the server talks to a receiver plugin inside each app.

```
Desktop app / browser  ──HTTP 127.0.0.1:8642──▶  server/  (scan, index, thumbnails, 3D previews, send logic)
                                                  ├──TCP 127.0.0.1:24981──▶ Cinema 4D plugin  (Octane or Standard materials, Octane splats)
                                                  └──TCP 127.0.0.1:24982──▶ Blender add-on    (Principled BSDF, native splats in 5.3+)
```

> **Setting it up?** Run `setup.ps1`, or open this folder in a coding agent (Claude Code, Codex, Cursor, …)
> and say *"set this up for me"*. [`AGENTS.md`](AGENTS.md) walks the agent through every step, with a check
> after each one, and explains the code for when you want to change it.

## What it does

- **Libraries are just folders.** Megascans libraries are read from their JSON metadata. Everything else
  is grouped by file name (`Rock_COL_4K.jpg`, `Rock_NRM…`, `Rock.fbx`, `textures/` subfolders …).
  It understands 3D models, surfaces, plants (with variations and LODs), atlases, decals, HDRIs and
  Gaussian splats. Multi-part models get one material per part. Nothing in your folders is moved,
  renamed or deleted.
- **Browse:** search, type and library filters, sort by colour, favourites, recently sent, smart tags,
  custom labels, filters (maps, minimum resolution, format, proxy, …) and saved views. Ctrl+click
  selects several cards.
- **Preview in 3D:** spin models, splats, surfaces and HDRIs in the detail panel, view each map on its
  own (normal, roughness, AO …), or open any map full size. GLB, FBX and OBJ load directly; USD and
  Alembic are converted once in the background by Blender and cached. Animated models play (glTF, FBX,
  and USD / Alembic transform animation), with Play/Pause and a clip menu. Models that only ship a
  texture swatch get a rendered thumbnail (Blender, in the background).
- **Send to Cinema 4D:** geometry at the LOD and resolution you pick, Octane Universal materials (C4D
  Standard when Octane isn't installed), HDRIs as Octane environments, splats as Octane Gaussian Splat
  objects with an optional proxy mesh. Sending the same asset again adds an instance. Several Cinema 4D
  versions can be open at once.
- **Send to Blender:** Principled BSDF materials with the right colour spaces, HDRI worlds, native
  splats, linked duplicates for repeats. There is also a scripting API for headless layouts:

  ```python
  from bridge2_blender import api
  root = api.import_asset(api.find_assets("mossy rock", type="3d")[0]["id"], lod="LOD2", resolution="2K")
  ```
- **Gaussian splats:** 3DGS PLY, SPZ v2 to v4, SPLAT/KSPLAT/SOG, glTF/GLB (`KHR_gaussian_splatting`)
  and USD splats are recognised by their file headers, so a splat `.ply` is never mistaken for a mesh.
  Each app gets a format it can read; anything else is converted on the fly (needs Node.js). Splat
  packs can pair several layers (for example a volume and a star field) into one asset that imports
  as one object per layer.
- **The Megascans catalogue on Fab, without downloading it:** a `fab` library indexes every free Quixel
  Megascans listing on Fab (title, tags, category, thumbnail) so it can be searched like everything
  else. An asset's files are downloaded only when you need them (Download, Get, or any send), at the
  quality you pick, and **Free up space** deletes them again.
- **Colour spaces that follow your OCIO config:** every texture is imported as sRGB colour, linear colour
  (EXR / HDR) or non-colour data. In Blender the names come from whatever OCIO config it runs with (its
  own, ACES, or a studio config through `$OCIO`), so imports don't break under a custom config.
- **Light on memory:** the catalogue lives in SQLite on disk, and the server streams files without holding
  them (about 100 MB with 25,000 assets indexed). The grid keeps only the rows near the screen however
  far you scroll, and closing the 3D preview hands its RAM and GPU memory back. Closing the window
  frees the rest; the server keeps running for sends.
- **Library health:** missing files, broken previews, duplicates, models without meshes or previews,
  splats without a proxy or real size, and more, each with a jump to the affected assets.
- **Jobs you can watch:** scans, renders, conversions and sends are jobs with one lifecycle. The status
  pill shows what's running, and scripts can wait on any of them (`GET /api/jobs/<id>?wait=60`).

## Quick start (Windows)

1. Install **Python 3.11+** from python.org (tick *Add python.exe to PATH*), and optionally **Node.js LTS**
   (for converting splats between PLY and SPZ).
2. In PowerShell, from this folder:

   ```powershell
   powershell -ExecutionPolicy Bypass -File setup.ps1
   ```

   This installs the Python packages, the Cinema 4D plugin into every Cinema 4D version it finds, the
   Blender add-on into the newest Blender, and a desktop app with Start-menu and desktop shortcuts.
3. Add your asset folders: **+ Add** in the sidebar, or edit `config/libraries.json`
   (see `config/libraries.example.json`).
4. Restart Cinema 4D and Blender, open **AssetBridge** from the desktop shortcut, and send something.

To use your own name, colours, icon and plugin IDs, edit `brand.json` and run
`py -3 tools/apply_brand.py` (details in [`AGENTS.md`](AGENTS.md#2-name-and-look-brandjson)).

## Everyday use

- Open it from the desktop shortcut, **Extensions › AssetBridge** in Cinema 4D, or **File › Import › AssetBridge**
  in Blender.
- Hover a card for the **C4D** and **Blender** buttons, or double-click to send to the app you used last.
  In the detail panel, **Enter** sends to Cinema 4D and **Shift+Enter** to Blender.
- Surfaces go onto the objects selected in the app. With nothing selected, only the material is created.
- Keys: `/` search · arrows move · `F` favourite · `Delete` remove from the library · `Esc` close.

## Requirements

- Windows 10 or 11
- Python 3.11 or newer
- Cinema 4D 2024 or newer (developed on 2026 and 2027); Octane is optional
- Blender 4.2 or newer; 5.3 or newer for Gaussian splats
- Node.js LTS (optional, for splat conversion)

## Layout

```
brand.json                  name, wordmark, accent, icon, ports, Cinema 4D plugin IDs
config/libraries.json       your library folders (created on first run; not in git)
server/                     HTTP server, scanners, catalog (SQLite), send logic, jobs, health checks
web/                        the UI (vanilla JS, no build step; three.js and Spark vendored for previews)
app/desktop_app.py          the desktop window (built into <exe_name>.exe)
c4d/Bridge2/                Cinema 4D plugin (receiver + importer)
blender/bridge2_blender/    Blender add-on (receiver, importer, scripting API)
tools/                      setup, branding, installers, conform tools, vendor converters, tests
cache/                      index, thumbnails, converted splats, logs (safe to delete; not in git)
```

`STATE_MACHINES.md` maps every background job and state, `ASSET_FORMAT.md` describes an optional standard
folder layout with a per-asset `asset.json` manifest, and `AGENTS.md` is the developer guide.

## Roadmap and suggestions

[`ROADMAP.md`](ROADMAP.md) lists what's being considered, each item with where to start in the code.
Suggestions are welcome: open an issue with the *Feature request* form, or a pull request that adds a line
to the roadmap. To work on something, see [`CONTRIBUTING.md`](CONTRIBUTING.md).

The repository is set up for coding agents: point Claude Code, Codex or Cursor at it and ask for a roadmap
item. [`AGENTS.md`](AGENTS.md) gives the agent the map, the recipes and the checks, and `py -3 tools\check.py`
verifies a change in one command.

## Licence

MIT, see [`LICENSE`](LICENSE). Bundled third-party code (three.js, Spark) keeps its own licence, see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

Megascans, Quixel, Fab, Poly Haven, Poliigon, Textures.com, CGAxis, KitBash3D, Cinema 4D, Octane and
Blender are trademarks of their owners. This project isn't affiliated with any of them. It reads files
you already have; respect each vendor's licence for the assets themselves.
