import json, os
from pymxs import runtime as rt
lib = rt.loadTempMaterialLibrary(os.environ["MAX_LIB"])
out = {"count": int(lib.count), "mats": []}
def desc(m, depth=0):
    d = {"name": str(m.name), "class": str(rt.classOf(m))}
    if rt.classOf(m) == rt.Multimaterial:
        d["subs"] = [desc(rt.getSubMtl(m, i + 1), depth + 1) if rt.getSubMtl(m, i + 1) else None for i in range(rt.getNumSubMtls(m))]
    elif rt.classOf(m) == rt.StandardMaterial:
        c = m.diffuse
        d["diffuse"] = [int(c.r), int(c.g), int(c.b)]
        d["maps"] = {}
        for slot in ("diffuseMap", "specularLevelMap", "glossinessMap", "opacityMap", "bumpMap", "specularMap", "selfIllumMap", "displacementMap"):
            try:
                v = getattr(m, slot)
            except Exception:
                v = None
            if v is not None:
                fn = None
                try: fn = str(v.filename)
                except Exception:
                    try:
                        sub = v.normal_map if hasattr(v, "normal_map") else None
                        fn = "%s(%s)" % (rt.classOf(v), sub.filename if sub else "")
                    except Exception: fn = str(rt.classOf(v))
                d["maps"][slot] = os.path.basename(fn or "")
        d["twosided"] = bool(m.twoSided)
    return d
for i in range(int(lib.count)):
    m = lib[i]
    if "allium" in str(m.name).lower() or i < 2:
        out["mats"].append(desc(m))
json.dump(out, open(os.environ["MAX_PROBE_OUT"], "w"), indent=1)
