"""Make a AssetBridge scene C4D file render-ready in Octane - runs INSIDE Cinema 4D.

    exec(open(r"<repo>\\tools\\c4d_scene_render_setup.py").read(),
         {"SCENE": r"...\\Medieval_Forge_scene_octane.c4d", "HDRI": r"D:\\...\\forest_grove_16k.hdr",
          "SAMPLES": 256, "RENDER": r"...\\preview.png" or None, "RES": (1280, 720)})

Adds (once) Octane as the renderer (path tracing, capped samples; other renderers' video posts removed), an
HDRI environment through AssetBridge's own receiver code, and four cameras framed from the scene bounds - hero 3/4,
reverse 3/4, high overview, eye level - then saves the file. With RENDER set, the hero camera is rendered via
RenderDocument and written there (works only if the installed Octane renders in batch / picture viewer mode).
"""
import glob
import json
import math
import os
import sys

import c4d

OCTANE_VP = 1029525
KERNEL, KERNEL_PT = 3001, 2
MAXSAMPLE_IDS = (3024, 3003, 3063, 3904, 3086)


def _receiver():
    prefs = c4d.storage.GeGetC4DPath(c4d.C4D_PATH_PREFS)
    for d in glob.glob(os.path.join(prefs, "plugins", "*")) + glob.glob(os.path.join(os.path.dirname(prefs), "plugins", "*")):
        if os.path.exists(os.path.join(d, "bridge2_import.py")):
            if d not in sys.path:
                sys.path.insert(0, d)
            import importlib
            import bridge2_import
            return importlib.reload(bridge2_import)
    raise RuntimeError("AssetBridge receiver not installed")


def walk(o):
    while o:
        yield o
        for c in walk(o.GetDown()):
            yield c
        o = o.GetNext()


def bounds(doc):
    lo, hi, found = c4d.Vector(1e18), c4d.Vector(-1e18), False
    for o in walk(doc.GetFirstObject()):
        if isinstance(o, c4d.PointObject) and o.GetPointCount():
            mg, mp, rad = o.GetMg(), o.GetMp(), o.GetRad()
            for sx in (-1, 1):
                for sy in (-1, 1):
                    for sz in (-1, 1):
                        p = mg * (mp + c4d.Vector(sx * rad.x, sy * rad.y, sz * rad.z))
                        lo = c4d.Vector(min(lo.x, p.x), min(lo.y, p.y), min(lo.z, p.z))
                        hi = c4d.Vector(max(hi.x, p.x), max(hi.y, p.y), max(hi.z, p.z))
                        found = True
    return (lo, hi) if found else (None, None)


def octane_settings(doc, samples):
    rd = doc.GetActiveRenderData()
    vp = rd.GetFirstVideoPost()
    while vp:  # other renderers / grading plugins the vendor scene carried
        nxt = vp.GetNext()
        if vp.GetType() != OCTANE_VP:
            vp.Remove()
        vp = nxt
    vp = rd.GetFirstVideoPost()
    if vp is None or vp.GetType() != OCTANE_VP:
        vp = c4d.documents.BaseVideoPost(OCTANE_VP)
        rd.InsertVideoPost(vp)
    vp[KERNEL] = KERNEL_PT
    for i in MAXSAMPLE_IDS:
        try:
            vp[i] = samples
        except Exception:
            pass
    rd[c4d.RDATA_RENDERENGINE] = OCTANE_VP
    return rd


def add_cameras(doc, lo, hi):
    c = (lo + hi) * 0.5
    ext = hi - lo
    r = ext.GetLength() * 0.5
    old = doc.SearchObject("AssetBridge Cameras")
    if old:
        old.Remove()
    grp = c4d.BaseObject(c4d.Onull)
    grp.SetName("AssetBridge Cameras")
    doc.InsertObject(grp)
    shots = []
    for name, d, dist_k, aim in (("Hero 3-4", c4d.Vector(0.75, 0.38, -1.0), 1.05, c),
                                 ("Reverse 3-4", c4d.Vector(-0.8, 0.32, 1.0), 1.05, c),
                                 ("Overview", c4d.Vector(0.35, 1.3, -0.6), 1.15, c),
                                 ("Eye level", c4d.Vector(0.55, 0.0, -1.0), 0.9, c4d.Vector(c.x, lo.y + 160, c.z))):
        cam = c4d.BaseObject(c4d.Ocamera)
        cam.SetName(name)
        cam[c4d.CAMERA_FOCUS] = 28.0
        fov = 2 * math.atan(36.0 / (2 * 28.0))
        dist = r / math.sin(fov / 2) * dist_k
        pos = aim + d.GetNormalized() * dist
        if name == "Eye level":
            pos.y = lo.y + 165  # a person's eye height (scene units: cm)
        cam.SetAbsPos(pos)
        cam.SetAbsRot(c4d.utils.VectorToHPB(aim - pos))
        cam.InsertUnderLast(grp)
        shots.append(cam)
    bd = doc.GetActiveBaseDraw()
    if bd:
        bd.SetSceneCamera(shots[0])
    return shots


def main(scene, hdri, samples, render, res):
    rx = _receiver()
    doc = c4d.documents.LoadDocument(scene, c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS, None)
    if doc is None:
        raise RuntimeError("could not load " + scene)
    out = {}
    try:
        lo, hi = bounds(doc)
        rd = octane_settings(doc, samples)
        rd[c4d.RDATA_XRES], rd[c4d.RDATA_YRES] = float(res[0]), float(res[1])
        rd[c4d.RDATA_FILMASPECT] = res[0] / float(res[1])
        if hdri:
            out["environment"] = rx._run_hdri(doc, {"hdri": hdri}, os.path.splitext(os.path.basename(hdri))[0], "octane",
                                              {"replace_environment": True})
        cams = add_cameras(doc, lo, hi)
        out["bounds_m"] = [round((hi - lo).x / 100, 1), round((hi - lo).y / 100, 1), round((hi - lo).z / 100, 1)]
        out["cameras"] = [c.GetName() for c in cams]
        out["saved"] = bool(c4d.documents.SaveDocument(doc, scene, c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST, c4d.FORMAT_C4DEXPORT))
        if render:
            bmp = c4d.bitmaps.MultipassBitmap(int(res[0]), int(res[1]), c4d.COLORMODE_RGB)
            bmp.AddChannel(True, True)
            r = c4d.documents.RenderDocument(doc, rd.GetData(), bmp, c4d.RENDERFLAGS_EXTERNAL | c4d.RENDERFLAGS_NODOCUMENTCLONE)
            out["render_result"] = r
            if r == c4d.RENDERRESULT_OK:
                out["render_saved"] = bmp.Save(render, c4d.FILTER_PNG) == c4d.IMAGERESULT_OK
    finally:
        c4d.documents.KillDocument(doc)
    return out


print("SETUP", json.dumps(main(SCENE, HDRI, SAMPLES, RENDER, RES), default=str))  # noqa: F821
