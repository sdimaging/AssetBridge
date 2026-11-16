import json, os
from pymxs import runtime as rt
rt.loadMaxFile(os.environ["MAX_PROBE_FILE"], quiet=True)
out = []

def bitmaps(node, seen, depth=0):
    found = []
    if node is None or depth > 6:
        return found
    h = rt.getHandleByAnim(node)
    if h in seen:
        return found
    seen.add(h)
    try:
        if rt.classOf(node) == rt.Bitmaptexture:
            found.append(str(node.filename))
    except Exception:
        pass
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
            found += bitmaps(r, seen, depth + 1)
    return found

for o in rt.objects:
    m = o.material
    if m is None:
        continue
    subs = [rt.getSubMtl(m, i + 1) for i in range(rt.getNumSubMtls(m))] if rt.classOf(m) == rt.Multimaterial else [m]
    ids = list(m.materialIDList) if rt.classOf(m) == rt.Multimaterial else [1]
    for mid, s in zip(ids, subs):
        out.append({"obj": str(o.name), "id": int(mid), "name": str(s.name) if s else None,
                    "maps": sorted({os.path.basename(b) for b in bitmaps(s, set())})})
json.dump(out, open(os.environ["MAX_PROBE_OUT"], "w"), indent=1)
