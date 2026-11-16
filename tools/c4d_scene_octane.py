"""Full vendor scene (FBX) -> Cinema 4D file with every material rebuilt as Octane - runs INSIDE Cinema 4D.

    exec(open(r"<repo>\\tools\\c4d_scene_octane.py").read(),
         {"SCENE": r"...\\forge_fbx.FBX", "KITS": [r"...\\Medieval_Forge"], "OUT": r"...\\forge_scene_octane.c4d"})

The FBX is loaded into a scratch document (never the active one). Every material is matched by name to the
kit's texture sets on disk (model tex/ folders, PBR packs, tex_embedded/ pulled out of the FBX by
tools/fbx_extract_media.py): <name>_basecolor(_diffuse) / _roughness / _metallic / _normal_opengl / _emissive /
_opacity -> Octane Universal via AssetBridge's receiver (bridge2_import.build_octane). Materials without a texture
set fall back to the standard->Octane conversion. The result is saved as OUT and the scratch doc is freed.
"""
import glob
import json
import os
import re
import sys
import time

import c4d

IMG = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".tga")
SUFFIX = [  # (map type, filename suffixes in priority order)
    ("albedo", ("_basecolor_diffuse", "_basecolor", "_diffuse", "_albedo", "_color")),
    ("roughness", ("_roughness",)),
    ("gloss", ("_glossiness", "_gloss")),
    ("metalness", ("_metallic", "_metalness")),
    ("normal", ("_normal_opengl", "_normal")),
    ("bump", ("_bump",)),
    ("emission", ("_emissive", "_emission")),
    ("opacity", ("_opacity", "_alpha")),
    ("ao", ("_ao",)),
    ("refract", ("_refract", "_refraction", "_transmission")),
]
FLAME_WORDS = ("candlelight", "flame", "fire", "torch_light")


def _receiver():
    """AssetBridge's C4D import module (installed plugin folder), for build_octane / _standard_to_octane."""
    prefs = c4d.storage.GeGetC4DPath(c4d.C4D_PATH_PREFS)
    for d in glob.glob(os.path.join(prefs, "plugins", "*")) + glob.glob(os.path.join(os.path.dirname(prefs), "plugins", "*")):
        if os.path.exists(os.path.join(d, "bridge2_import.py")):
            if d not in sys.path:
                sys.path.insert(0, d)
            import importlib
            import bridge2_import
            return importlib.reload(bridge2_import)
    raise RuntimeError("AssetBridge receiver not installed")


def index_textures(roots):
    idx = {}
    for r in roots:
        for dp, dn, fn in os.walk(r):
            dn[:] = [d for d in dn if not d.lower().endswith(".fbm")]  # FBX importer's unpacked copies: never reference
            for f in fn:
                stem, ext = os.path.splitext(f)
                if ext.lower() in IMG:
                    key = stem.lower()
                    old = idx.get(key)
                    if old is None or os.path.getsize(os.path.join(dp, f)) > os.path.getsize(old):
                        idx[key] = os.path.join(dp, f)  # largest copy wins (8K pack over 2K embedded)
    return idx


def maps_for(name, idx):
    """Texture set for a material: <name>_<type> exactly, else the most specific kit file ending in _<name>_<type>
    (scene materials often drop the kit prefix: 'floor' -> throne_room_floor_basecolor_diffuse.png)."""
    base = re.sub(r"\.\d+$", "", name).lower()
    base = re.sub(r"^mat_", "", base)
    maps = {}
    for kind, suffixes in SUFFIX:
        for sfx in suffixes:
            exact = [k for k in (base + sfx, "mat_" + base + sfx) if k in idx]
            if exact:
                maps.setdefault(kind, idx[exact[0]])
                continue
            tail = "_" + base + sfx
            hits = sorted((k for k in idx if k.endswith(tail)), key=len)
            if hits:
                maps.setdefault(kind, idx[hits[0]])
    return maps


def _image(path, color, rx):
    t = c4d.BaseShader(rx.ID_OCTANE_IMAGE)
    t[c4d.IMAGETEXTURE_FILE] = path
    t[c4d.IMAGETEXTURE_GAMMA] = 2.2 if color else 1.0
    return t


def _finish_special(om, name, maps, rx):
    """Glass (refraction map) -> specular transmission; flame cards (candlelight / fire) -> emissive."""
    low = name.lower()
    if "refract" in maps:
        t = _image(maps["refract"], True, rx)
        om.InsertShader(t)
        om[c4d.OCT_MATERIAL_TRANSMISSION_LINK] = t
        om[c4d.UNIVMAT_TRANSMISSION_TYPE] = 0  # specular: clear glass, not diffuse translucency
        om[c4d.OCT_MATERIAL_TRANSMISSION_FLOAT] = 1.0
    if any(w in low for w in FLAME_WORDS) and "albedo" in maps and "emission" not in maps:
        t = _image(maps["albedo"], True, rx)
        em = c4d.BaseShader(rx.ID_OCTANE_TEX_EMISSION)
        em[c4d.TEXEMISSION_EFFIC_OR_TEX] = t
        em[c4d.TEXEMISSION_SURFACE_BRIGHTNESS] = True
        em[c4d.TEXEMISSION_POWER] = 4.0
        om.InsertShader(em)
        om.InsertShader(t)
        om[c4d.OCT_MATERIAL_EMISSION] = em


def walk(o):
    while o:
        yield o
        for c in walk(o.GetDown()):
            yield c
        o = o.GetNext()


def build(scene, kits, out):
    t0 = time.time()
    rx = _receiver()
    idx = index_textures(kits)
    doc = c4d.documents.LoadDocument(scene, c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS, None)
    if doc is None:
        raise RuntimeError("could not load " + scene)
    t_load = time.time() - t0
    report = {"materials": 0, "octane_from_sets": 0, "converted_standard": 0, "no_textures": [], "load_s": round(t_load, 1)}
    swap = []
    leftovers = []
    for m in doc.GetMaterials():
        report["materials"] += 1
        maps = maps_for(m.GetName(), idx)
        if "albedo" in maps or len(maps) >= 2:
            om = rx.build_octane(maps, re.sub(r"\.\d+$", "", m.GetName()), {}, {"ao": False}, True)
            _finish_special(om, m.GetName(), maps, rx)
            doc.InsertMaterial(om)
            swap.append((m, om))
            report["octane_from_sets"] += 1
        elif m.GetType() == c4d.Mmaterial:
            leftovers.append(m)
        else:
            report["no_textures"].append(m.GetName())
    for o in walk(doc.GetFirstObject()):
        for t in o.GetTags():
            if t.GetType() == c4d.Ttexture:
                for old, new in swap:
                    if t.GetMaterial() == old:
                        t.SetMaterial(new)
    for old, new in swap:
        name = old.GetName()
        old.Remove()
        new.SetName(name)
    if leftovers:  # plain colours / glass with no texture set: same conversion AssetBridge applies on import
        tops = []
        o = doc.GetFirstObject()
        while o:
            tops.append(o)
            o = o.GetNext()
        group = c4d.BaseObject(c4d.Onull)  # identity null: children keep their world transforms
        doc.InsertObject(group)
        for o in tops:
            o.Remove()
            o.InsertUnderLast(group)
        report["converted_standard"] = rx._standard_to_octane(doc, group, leftovers, {}, {"ao": False})
        prev = None
        for o in list(group.GetChildren()):  # back to the top level, original order
            o.Remove()
            doc.InsertObject(o, None, prev)
            prev = o
        group.Remove()
    doc.SetDocumentName(os.path.basename(out))
    doc.SetDocumentPath(os.path.dirname(out))
    ok = c4d.documents.SaveDocument(doc, out, c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST, c4d.FORMAT_C4DEXPORT)
    report.update(saved=bool(ok), out=out, objects=sum(1 for _ in walk(doc.GetFirstObject())),
                  seconds=round(time.time() - t0, 1), textures_indexed=len(idx))
    c4d.documents.KillDocument(doc)
    fbm = os.path.splitext(scene)[0] + ".fbm"  # FBX SDK unpacks embedded media here on load; nothing references it
    if os.path.isdir(fbm):
        import shutil
        shutil.rmtree(fbm, ignore_errors=True)
        report["removed_fbm"] = True
    return report


print("SCENE", json.dumps(build(SCENE, KITS, OUT)))  # noqa: F821 - provided by the exec() caller
