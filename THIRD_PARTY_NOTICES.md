# Third-party code

AssetBridge bundles the following, unmodified, under `web/vendor/`. Each keeps its own licence.

| component | where | licence |
|---|---|---|
| three.js r180 | `web/vendor/three/` | MIT, see `web/vendor/three/LICENSE` |
| Spark (Gaussian splat renderer for three.js) | `web/vendor/spark/` | MIT, see `web/vendor/spark/LICENSE` |
| Decoders shipped with three.js examples (Draco, Basis Universal, rhino3dm, ammo.js, …) | `web/vendor/three/examples/jsm/libs/` | their own licences, noted in or next to each file |

Installed at setup time, not bundled: Python packages from `requirements.txt` (Pillow, NumPy, OpenCV,
pywebview, PyInstaller, resvg-py) and, optionally, PlayCanvas `splat-transform` (MIT) from npm.
