# Asset Format v1

A conformed asset is a folder with a `asset.json` manifest. AssetBridge reads the manifest instead of
guessing from file names, so a conformed asset always imports the same way in every app.
`tools/conform.py` writes manifests, renames to the canonical names, and generates missing formats.

## Folder layout

```
<Library>/<Collection>/<Category>/<Asset_Name>/
    asset.json
    <Asset_Name>_preview.png        square, at least 512 px (a render, promo or swatch)
    ...files for the asset type (below)
```

- `Asset_Name` uses letters, digits and underscores, e.g. `Chalice_1`, `Fir_Tree_01`.
- Units are metres and up is +Y in the manifest. Each file's own frame is recorded under `frames`.
- Anything else in the folder (datasets, working files) is ignored unless the manifest lists it.

## Types

| type | required | optional |
|---|---|---|
| `splat` | `<Name>.ply` (3DGS) **or** `<Name>.spz` | the other one, `<Name>.glb` (KHR_gaussian_splatting), `<Name>.usdz`, `<Name>_proxy.glb` |
| `3d` | a mesh: `<Name>.glb`, `<Name>.fbx` or `<Name>_LOD<n>.fbx` | `<Name>.c4d` (native Octane), `<Name>.blend`, `textures/` |
| `surface` | `<Name>_<Res>_Albedo.<ext>` + any other maps | |
| `hdri` | `<Name>_<Res>.hdr` or `.exr` | `<Name>_preview.jpg` (tone-mapped) |

**Canonical map names:** Albedo, Normal (OpenGL), Roughness, Gloss, Metalness, Specular, AO,
Displacement, Bump, Opacity, Translucency, Emission. Resolution is `1K`…`16K`. Textures for a 3D asset
live in `textures/<Name>[_<Part>]_<Res>_<Map>.<ext>`; `<Part>` names a material slot in the mesh.

## Splat frames

| file | frame | loads upright in |
|---|---|---|
| `.ply` | `3dgs`: the PostShot/3DGS file frame (Y down, +Z front) | Octane (C4D), SuperSplat |
| `.spz` | `blender`: the PLY turned 180° about Z | Blender 5.3 `wm.spz_import` |
| `_proxy.glb` | `gltf`: +Y up, +Z front, metres | Blender and C4D as is (lines up with the splat) |

AssetBridge converts between `.ply` and `.spz` with that 180° Z turn (it's its own inverse), so an asset
needs only one of them.

## asset.json

```json
{
  "format_version": 1,
  "type": "splat",
  "name": "Chalice 1",
  "category": "Treasure and Gold",
  "tags": ["treasure", "gold", "pirate"],
  "real_size_m": 0.24,
  "preview": "Chalice_1_promo.png",
  "files": {
    "splat": {"ply": "Chalice_1.ply", "spz": "Chalice_1.spz"},
    "proxy": "Chalice_1_proxy.glb"
  },
  "frames": {"ply": "3dgs", "spz": "blender", "proxy": "gltf"},
  "stats": {"points": 1000000, "sh_degree": 3, "spz_version": 4, "extent_m": 0.908}
}
```

- `real_size_m` is the largest real-world side. AssetBridge scales by `real_size_m / extent_m` when
  "Real-world scale" is on.
- Paths are relative to the asset folder.
- `3d` manifests use `files.mesh` (format → path, or LOD → path), `files.native`, and
  `files.materials` (part → {map → path}). `surface` uses `files.maps`; `hdri` uses `files.hdri`
  (res → path).
