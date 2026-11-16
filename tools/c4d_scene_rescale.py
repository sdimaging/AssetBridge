"""Bake a uniform scale into a saved scene (vendor FBX authored in inches -> x2.54) - runs INSIDE Cinema 4D.

    exec(open(r"<repo>\\tools\\c4d_scene_rescale.py").read(), {"SCENE": r"...c4d", "FACTOR": 2.54})

Every object's position and every point are multiplied by FACTOR (a uniform scale about the world origin), so
objects keep 100% scale and match the kit's individual props. Cameras / HDRI added by AssetBridge are removed (the
render setup re-frames them). Saves in place.
"""
import json

import c4d


def walk(o):
    while o:
        yield o
        for c in walk(o.GetDown()):
            yield c
        o = o.GetNext()


def main(scene, f):
    doc = c4d.documents.LoadDocument(scene, c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS, None)
    n_obj = n_pts = 0
    try:
        for name in ("AssetBridge Cameras",):
            o = doc.SearchObject(name)
            if o:
                o.Remove()
        for o in list(walk(doc.GetFirstObject())):
            if o.GetType() == c4d.Osky and o.GetName().startswith("HDRI"):
                continue
            o.SetRelPos(o.GetRelPos() * f)
            n_obj += 1
            if isinstance(o, c4d.PointObject) and o.GetPointCount():
                o.SetAllPoints([p * f for p in o.GetAllPoints()])
                o.Message(c4d.MSG_UPDATE)
                n_pts += o.GetPointCount()
        ok = c4d.documents.SaveDocument(doc, scene, c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST, c4d.FORMAT_C4DEXPORT)
    finally:
        c4d.documents.KillDocument(doc)
    return {"objects": n_obj, "points": n_pts, "factor": f, "saved": bool(ok)}


print("RESCALE", json.dumps(main(SCENE, FACTOR)))  # noqa: F821
