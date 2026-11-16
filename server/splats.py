"""Gaussian-splat file detection: cheap header reads only (never loads the splat data).

    sniff(path) -> {"format", "version", "points", "sh_degree", "splat": bool} or None

Formats: 3DGS .ply (vs mesh .ply), .spz (v2/v3 gzip, v4 NGSP), .splat, .ksplat, .sog, .lcc,
glTF/GLB with KHR_gaussian_splatting, USD (.usda/.usdc/.usdz) with a Gaussian/particle-field prim.
"""
import gzip
import json
import os
import re
import struct
import zipfile

SPLAT_ONLY_EXT = {"spz", "splat", "ksplat", "sog", "lcc"}
MAYBE_SPLAT_EXT = {"ply", "glb", "gltf", "usd", "usda", "usdc", "usdz"}
USD_SPLAT_RE = re.compile(rb"GaussianSplat|ParticleField3DGaussian|ParticleField|gaussian_splat", re.I)


def sniff(path):
    ext = os.path.splitext(path)[1][1:].lower()
    try:
        if ext == "ply":
            return _ply(path)
        if ext == "spz":
            return _spz(path)
        if ext in ("splat", "ksplat", "sog", "lcc"):
            pts = os.path.getsize(path) // 32 if ext == "splat" else None  # .splat = 32 B per splat
            return {"format": ext, "version": None, "points": pts, "sh_degree": 0 if ext == "splat" else None, "splat": True}
        if ext in ("glb", "gltf"):
            return _gltf(path, ext)
        if ext in ("usd", "usda", "usdc", "usdz"):
            return _usd(path, ext)
    except (OSError, ValueError, struct.error, zipfile.BadZipFile, EOFError):
        return None
    return None


def _ply(path):
    with open(path, "rb") as fh:
        head = fh.read(16384)
    end = head.find(b"end_header")
    if not head.startswith(b"ply") or end < 0:
        return None
    text = head[:end].decode("latin-1")
    props = re.findall(r"property\s+\S+\s+(\S+)", text)
    m = re.search(r"element\s+vertex\s+(\d+)", text)
    if not {"opacity", "rot_0", "scale_0"} <= set(props):
        return {"format": "ply", "splat": False}
    rest = sum(1 for p in props if p.startswith("f_rest_"))
    sh = {0: 0, 9: 1, 24: 2, 45: 3}.get(rest, None)
    return {"format": "ply", "version": None, "points": int(m.group(1)) if m else None, "sh_degree": sh, "splat": True}


def _spz(path):
    with open(path, "rb") as fh:
        head = fh.read(32)
    if head[:4] == b"NGSP":  # v4: raw header
        ver, pts, sh = struct.unpack("<IIB", head[4:13])
        return {"format": "spz", "version": ver, "points": pts, "sh_degree": sh, "splat": True}
    if head[:2] == b"\x1f\x8b":  # v2/v3: gzip-wrapped
        with gzip.open(path, "rb") as gz:
            h = gz.read(16)
        if h[:4] == b"NGSP":
            ver, pts, sh = struct.unpack("<IIB", h[4:13])
            return {"format": "spz", "version": ver, "points": pts, "sh_degree": sh, "splat": True}
    return None


def gltf_json(path, ext=None):
    ext = ext or os.path.splitext(path)[1][1:].lower()
    if ext == "gltf":
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), None
    with open(path, "rb") as fh:
        magic, _ver, _length = struct.unpack("<4sII", fh.read(12))
        if magic != b"glTF":
            raise ValueError("not a GLB")
        clen, ctype = struct.unpack("<I4s", fh.read(8))
        doc = json.loads(fh.read(clen).decode("utf-8"))
        return doc, 12 + 8 + clen  # offset where the BIN chunk header starts


def _gltf(path, ext):
    doc, _ = gltf_json(path, ext)
    used = set(doc.get("extensionsUsed", [])) | set(doc.get("extensionsRequired", []))
    is_splat = any("gaussian_splatting" in u.lower() for u in used)
    info = {"format": ext, "splat": is_splat, "version": None, "points": None, "sh_degree": None}
    if is_splat:
        for mesh in doc.get("meshes", []):
            for prim in mesh.get("primitives", []):
                pos = prim.get("attributes", {}).get("POSITION")
                if pos is not None:
                    info["points"] = (info["points"] or 0) + doc["accessors"][pos].get("count", 0)
                shs = [a for a in prim.get("attributes", {}) if "SH_DEGREE_" in a]
                if shs:
                    info["sh_degree"] = max(int(re.search(r"SH_DEGREE_(\d)", a).group(1)) for a in shs)
                elif pos is not None:
                    info["sh_degree"] = 0
    else:
        info["bounds"] = gltf_bounds(doc)
    return info


def gltf_bounds(doc):
    """Axis-aligned extent (glTF units) from POSITION accessor min/max (ignores node transforms)."""
    lo, hi = [1e30] * 3, [-1e30] * 3
    for mesh in doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            acc = doc["accessors"][prim["attributes"]["POSITION"]] if "POSITION" in prim.get("attributes", {}) else None
            if acc and "min" in acc and "max" in acc:
                lo = [min(a, b) for a, b in zip(lo, acc["min"])]
                hi = [max(a, b) for a, b in zip(hi, acc["max"])]
    return (lo, hi) if lo[0] < 1e29 else None


def _usd(path, ext):
    if ext == "usdz":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith((".usda", ".usdc", ".usd"))]
            data = b"".join(z.open(n).read(4 << 20) for n in names[:4])
    else:
        with open(path, "rb") as fh:
            data = fh.read(8 << 20)  # schema tokens live early in the token table
    return {"format": ext, "splat": bool(USD_SPLAT_RE.search(data)), "version": None, "points": None, "sh_degree": None}
