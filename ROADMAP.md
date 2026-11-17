# Roadmap

What's being considered for AssetBridge. Nothing here is scheduled or promised; it's a list of good next steps,
each with where to start in the code. Suggestions are welcome: open an issue with the *Feature request* form,
or a pull request that adds a line here. To take an item, see [`CONTRIBUTING.md`](CONTRIBUTING.md) (people) or
[`AGENTS.md` Part E](AGENTS.md#part-e-roadmap-issues-and-pull-requests) (coding agents).

## Ideas and next steps

### Formats

- **USD variants and assemblies.** A USD file is one asset today, sent whole. Next: list a file's variant sets
  and let the user pick one before sending, and show the components it references. *Start:* read variant sets
  in a background Blender (`pxr` is bundled with Blender; `tools/preview_mesh.py` shows the pattern), a send
  option ([AGENTS C.3](AGENTS.md#c3-a-new-send-option)), and both importers.
- **Vertex animation in the preview.** Alembic and USD meshes whose points move show their first frame (moving
  transforms already play). Short clips could become glTF morph targets. *Start:* `tools/preview_mesh.py`.
- **An "Animated" filter and card badge.** Find clips at scan time (glTF `animations`, USD time codes, Alembic
  samples, FBX takes) and keep the result in the asset record. *Start:* `server/scanners.py` (`_finish()`) and a
  filter token ([AGENTS C.5](AGENTS.md#c5-a-filter-or-a-health-check)).
- **Splat sequences.** A folder of per-frame splat files as one asset, with a frame scrubber in the preview,
  sent to the apps as a sequence. *Start:* a scanner rule for numbered splat files, `showSplat()` in
  `web/viewer.js`, both importers.

### Colour

- **Per-texture colour-space overrides.** Let an asset say which colour space a map is in (`maps[].colorspace`,
  from `asset.json` or the detail panel), honoured by both importers. *Start:* `set_colorspace()` in
  `blender/bridge2_blender/core.py`, `build_octane()` in `c4d/Bridge2/bridge2_import.py`.
- **The OCIO view in the 3D preview.** The preview uses a fixed sRGB + ACES Filmic view. Matching the user's
  OCIO display and view (for example through a baked 3D LUT) would make it match their renders. *Start:* the
  renderer setup in `web/viewer.js`.

### Apps and platforms

- **More receivers: Houdini, Maya, Unreal.** The wire protocol is one JSON line each way
  ([AGENTS B.4](AGENTS.md#b4-receivers-and-the-wire-protocol)); the Blender add-on is the smallest receiver to
  copy.
- **macOS and Linux.** The server is plain Python and the UI runs in any browser; the desktop window,
  installers and a few paths are Windows-only. *Start:* `app/desktop_app.py`, `tools/install_*.py`, `setup.ps1`.

### Libraries

- **More vendors' file names.** Usually a few synonyms each ([AGENTS C.1](AGENTS.md#c1-a-vendors-file-names-arent-recognised)).
  Open an issue with the vendor's name and a folder listing.
- **More of Fab.** The remote library covers the free Megascans listings; other free Fab listings could use
  the same browse-first, download-on-demand path. *Start:* `listed()` and `files_for()` in `server/fab.py`.

### Memory and speed

- **A smaller card size for very large libraries.** The grid is windowed, but the browser still caches the
  thumbnails it has shown. A 128 px thumbnail option would roughly halve that. *Start:* `make_thumb()` and the
  `/thumb/` route in `server/bridge2.py`, the card size slider in `web/app.js`.

## Done

- Windowed grid: only the rows near the screen exist, however far you scroll.
- The 3D preview hands back its RAM and GPU memory when it closes.
- USD and Alembic in the 3D preview (converted once by Blender, cached), and animation playback for glTF, FBX,
  USD and Alembic.
- Texture colour spaces that follow Blender's OCIO config, and linear EXR / HDR colour maps in both apps.
- `tools/check.py`: every automated check in one command.
- The remote Fab library: browse the free Megascans catalogue without downloading it, pull files on demand.
