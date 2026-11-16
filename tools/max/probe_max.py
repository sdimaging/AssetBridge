"""Debug: dump each object's material tree (classes, names, bitmaps + map channels) for one .max file.

    MAX_PROBE=<file.max> MAX_PROBE_OUT=<out.txt> 3dsmaxbatch.exe tools/max/probe_max.py
"""
import os

from pymxs import runtime as rt

out = []


def walk(node, depth, seen):
    if node is None or depth > 10:
        return
    try:
        h = rt.getHandleByAnim(node)
    except Exception:
        return
    if h in seen:
        return
    seen.add(h)
    sc = str(rt.superClassOf(node))
    cls = str(rt.classOf(node))
    if sc in ("material", "textureMap"):
        extra = ""
        if cls == "Bitmaptexture":
            extra = " %s ch=%s" % (os.path.basename(str(node.filename)), node.coords.mapChannel)
        try:
            nm = str(node.name)
        except Exception:
            nm = ""
        if cls == "Color_Correction":
            props = []
            for pn in rt.getPropNames(node):
                try:
                    v = rt.getProperty(node, pn)
                    if not str(rt.superClassOf(v)).endswith("textureMap"):
                        props.append("%s=%s" % (pn, v))
                except Exception:
                    pass
            extra = " {" + ", ".join(props) + "}"
        out.append("%s%s %s '%s'%s" % ("  " * depth, sc[:3], cls, nm, extra))
        depth += 1
    try:
        n = rt.refs.getNumRefs(node)
    except Exception:
        n = 0
    for i in range(n):
        try:
            r = rt.refs.getReference(node, i + 1)
        except Exception:
            r = None
        if r is not None:
            walk(r, depth, seen)


src = os.environ["MAX_PROBE"]
if src.lower().endswith(".fbx"):
    rt.importFile(src, rt.name("noPrompt"), using=rt.FBXIMP)
else:
    rt.loadMaxFile(src, quiet=True, useFileUnits=True)
for o in rt.objects:
    if rt.superClassOf(o) != rt.GeometryClass:
        continue
    try:
        nm = int(rt.meshop.getNumMaps(o.mesh)) - 1
        faces = int(o.mesh.numfaces)
    except Exception:
        nm = faces = -1
    bb = o.max - o.min
    out.append("OBJ %s %s maps=%s faces=%s pos=(%.0f,%.0f,%.0f) size=(%.0f,%.0f,%.0f)" % (o.name, rt.classOf(o), nm, faces, o.pos.x, o.pos.y, o.pos.z, bb.x, bb.y, bb.z))
    walk(o.material, 1, set())
with open(os.environ["MAX_PROBE_OUT"], "w", encoding="utf-8") as fh:
    fh.write("\n".join(out))
