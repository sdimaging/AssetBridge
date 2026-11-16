"""Static previews for C4D-only models (vendor .c4d with Octane / standard materials) - runs INSIDE Cinema 4D.

    exec(open(r"<repo>\\tools\\c4d_render_previews.py").read(), {"JOBS": r"<repo>\\cache\\c4d_preview_jobs.json"})

Jobs: [{"id", "src" (.c4d), "png" (output)}]. Each file is loaded into a scratch document that is never shown,
its Octane materials are mirrored as standard materials (albedo + opacity cut-out, so the standard renderer can
draw them), the model is framed at a three-quarter view and rendered with alpha to the job's png - the same
cache/renders file the AssetBridge server prefers for thumbnails. Never touches the active document.
"""
import json
import math
import os

import c4d

ID_OCT_MAT = 1029501
ID_OCT_IMAGE = 1029508
SIZE = 640


def _first_image(sh, depth=0):
    """First Octane ImageTexture under a shader (through CC / mix / multiply wrappers)."""
    while sh is not None and depth < 12:
        if sh.GetType() == ID_OCT_IMAGE:
            return sh[c4d.IMAGETEXTURE_FILE]
        hit = _first_image(sh.GetDown(), depth + 1)
        if hit:
            return hit
        sh = sh.GetNext()
    return None


def _linked_image(mat, sym):
    pid = getattr(c4d, sym, None)
    if pid is None:
        return None
    sh = mat[pid]
    if sh is None:
        return None
    if sh.GetType() == ID_OCT_IMAGE:
        return sh[c4d.IMAGETEXTURE_FILE]
    for bc, descid, _ in sh.GetDescription(c4d.DESCFLAGS_DESC_NONE):  # wrappers: follow their shader links
        try:
            v = sh[descid[0].id]
        except Exception:
            continue
        if isinstance(v, c4d.BaseShader):
            if v.GetType() == ID_OCT_IMAGE:
                return v[c4d.IMAGETEXTURE_FILE]
            hit = _first_image(v)
            if hit:
                return hit
    return _first_image(sh.GetDown())


def _resolve(path, src_dir):
    if not path:
        return None
    if os.path.isabs(path) and os.path.exists(path):
        return path
    name = os.path.basename(path.replace("\\", "/"))
    dirs = [src_dir, os.path.join(src_dir, "tex"), os.path.join(src_dir, "textures"), os.path.join(os.path.dirname(src_dir), "tex")]
    try:
        dirs += [os.path.join(src_dir, d) for d in os.listdir(src_dir)
                 if os.path.isdir(os.path.join(src_dir, d)) and ("map" in d.lower() or "tex" in d.lower())]
    except OSError:
        pass
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


def _bitmap(path):
    s = c4d.BaseShader(c4d.Xbitmap)
    s[c4d.BITMAPSHADER_FILENAME] = path
    return s


def _mirror_octane(doc, src_dir):
    """Standard material per Octane material (same name), swapped in on every texture tag."""
    swap = {}
    for m in doc.GetMaterials():
        if m.GetType() != ID_OCT_MAT:
            continue
        alb = _resolve(_linked_image(m, "OCT_MATERIAL_DIFFUSE_LINK"), src_dir)
        opa = _resolve(_linked_image(m, "OCT_MATERIAL_OPACITY_LINK"), src_dir)
        sm = c4d.BaseMaterial(c4d.Mmaterial)
        sm.SetName(m.GetName())
        sm[c4d.MATERIAL_USE_REFLECTION] = False
        if alb:
            b = _bitmap(alb)
            sm.InsertShader(b)
            sm[c4d.MATERIAL_COLOR_SHADER] = b
        if opa:
            a = _bitmap(opa)
            sm.InsertShader(a)
            sm[c4d.MATERIAL_USE_ALPHA] = True
            sm[c4d.MATERIAL_ALPHA_SHADER] = a
        doc.InsertMaterial(sm)
        swap[m] = sm
    if not swap:
        return 0
    o = doc.GetFirstObject()
    stack = [o] if o else []
    while stack:
        o = stack.pop()
        if o.GetNext():
            stack.append(o.GetNext())
        if o.GetDown():
            stack.append(o.GetDown())
        for t in o.GetTags():
            if t.GetType() == c4d.Ttexture:
                for old, new in swap.items():
                    if t.GetMaterial() == old:
                        t.SetMaterial(new)
    return len(swap)


def _bounds(doc):
    lo = c4d.Vector(1e18)
    hi = c4d.Vector(-1e18)
    o = doc.GetFirstObject()
    stack = [o] if o else []
    found = False
    while stack:
        o = stack.pop()
        if o.GetNext():
            stack.append(o.GetNext())
        if o.GetDown():
            stack.append(o.GetDown())
        if isinstance(o, c4d.PointObject) and o.GetPointCount():
            mg = o.GetMg()
            mp, rad = o.GetMp(), o.GetRad()
            for sx in (-1, 1):
                for sy in (-1, 1):
                    for sz in (-1, 1):
                        p = mg * (mp + c4d.Vector(sx * rad.x, sy * rad.y, sz * rad.z))
                        lo = c4d.Vector(min(lo.x, p.x), min(lo.y, p.y), min(lo.z, p.z))
                        hi = c4d.Vector(max(hi.x, p.x), max(hi.y, p.y), max(hi.z, p.z))
            found = True
    return (lo, hi) if found else (None, None)


def render_job(job):
    src = job["src"]
    doc = c4d.documents.LoadDocument(src, c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS, None)
    if doc is None:
        return "could not load"
    try:
        _mirror_octane(doc, os.path.dirname(src))
        if job.get("object"):  # kits: frame one hero object, drop the rest
            keep = doc.SearchObject(job["object"])
            if keep is not None:
                o = doc.GetFirstObject()
                while o:
                    nxt = o.GetNext()
                    if o != keep:
                        o.Remove()
                    o = nxt
        lo, hi = _bounds(doc)
        if lo is None:
            return "no geometry"
        c = (lo + hi) * 0.5
        r = (hi - lo).GetLength() * 0.5
        cam = doc.SearchObject(job["camera"]) if job.get("camera") else None  # scenes: their own hero camera
        if cam is None:
            cam = c4d.BaseObject(c4d.Ocamera)
            fov = cam[c4d.CAMERAOBJECT_FOV] if cam[c4d.CAMERAOBJECT_FOV] else math.radians(40)
            dist = r / math.sin(fov / 2) * 1.02
            d = c4d.Vector(0.62, 0.42, -1.0).GetNormalized()
            pos = c + d * dist
            cam.SetAbsPos(pos)
            cam.SetAbsRot(c4d.utils.VectorToHPB(c - pos))
            doc.InsertObject(cam)
        for hpb, inten in (((-0.6, -0.7, 0), 1.0), ((2.4, -0.3, 0), 0.6), ((1.2, -1.2, 0), 0.5)):
            lt = c4d.BaseObject(c4d.Olight)
            lt[c4d.LIGHT_TYPE] = c4d.LIGHT_TYPE_DISTANT
            lt[c4d.LIGHT_BRIGHTNESS] = inten
            lt[c4d.LIGHT_SHADOWTYPE_VIRTUAL] = 1 if inten == 1.0 else 0  # soft shadows from the key light
            lt.SetAbsRot(c4d.Vector(*hpb))
            doc.InsertObject(lt)
        sky = c4d.BaseObject(c4d.Osky)
        sm = c4d.BaseMaterial(c4d.Mmaterial)
        sm[c4d.MATERIAL_USE_COLOR] = False
        sm[c4d.MATERIAL_USE_LUMINANCE] = True
        sm[c4d.MATERIAL_LUMINANCE_COLOR] = c4d.Vector(0.75)
        doc.InsertMaterial(sm)
        tag = sky.MakeTag(c4d.Ttexture)
        tag.SetMaterial(sm)
        comp = sky.MakeTag(c4d.Tcompositing)
        comp[c4d.COMPOSITINGTAG_SEENBYCAMERA] = False
        doc.InsertObject(sky)
        bd = doc.GetActiveBaseDraw()
        if bd:
            bd.SetSceneCamera(cam)
        rdo = doc.GetActiveRenderData()
        vp = rdo.GetFirstVideoPost()
        while vp:  # never let Octane / Redshift video posts run inside RenderDocument (crashed C4D 2026-10-08)
            nxt = vp.GetNext()
            vp.Remove()
            vp = nxt
        rd = rdo.GetData()
        rd[c4d.RDATA_RENDERENGINE] = c4d.RDATA_RENDERENGINE_STANDARD
        rd[c4d.RDATA_XRES] = SIZE
        rd[c4d.RDATA_YRES] = SIZE
        rd[c4d.RDATA_FILMASPECT] = 1.0
        rd[c4d.RDATA_ALPHACHANNEL] = True
        rd[c4d.RDATA_ANTIALIASING] = c4d.RDATA_ANTIALIASING_BEST
        bmp = c4d.bitmaps.MultipassBitmap(SIZE, SIZE, c4d.COLORMODE_RGB)
        bmp.AddChannel(True, True)
        res = c4d.documents.RenderDocument(doc, rd, bmp, c4d.RENDERFLAGS_EXTERNAL | c4d.RENDERFLAGS_NODOCUMENTCLONE)
        if res != c4d.RENDERRESULT_OK:
            return "render failed (%s)" % res
        os.makedirs(os.path.dirname(job["png"]), exist_ok=True)
        tmp = job["png"][:-4] + ".part.png"
        if bmp.Save(tmp, c4d.FILTER_PNG, None, c4d.SAVEBIT_ALPHA) != c4d.IMAGERESULT_OK:
            return "save failed"
        os.replace(tmp, job["png"])
        return None
    finally:
        c4d.documents.KillDocument(doc)


def main(jobs_path, only=None):
    with open(jobs_path, "r", encoding="utf-8") as fh:
        jobs = json.load(fh)
    for job in jobs:
        if only and job["id"] not in only:
            continue
        try:
            err = render_job(job)
        except Exception as e:
            err = "%s: %s" % (type(e).__name__, e)
        print("PREVIEW", job["id"], err or "ok")


main(JOBS, globals().get("ONLY"))  # noqa: F821 - provided by the exec() caller
