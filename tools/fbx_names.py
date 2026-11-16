"""List object/material/texture names from a binary FBX without importing it (fast byte scan).

    py -3.13 tools/fbx_names.py <file.fbx> [--kind Material|Model|Texture|Video] [--json]
"""
import json
import mmap
import re
import sys


def names(path, kind="Material"):
    """Every <name>\\x00\\x01<kind> marker (FBX object name + class), found with a plain substring search."""
    marker = b"\x00\x01" + kind.encode()
    out = []
    with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        i = mm.find(marker)
        while i != -1:
            nxt = mm[i + len(marker):i + len(marker) + 1]
            if not (nxt.isalpha() and nxt.islower()):  # "Material"+type code, not "Materials"
                j = i
                while j > 0 and i - j < 160 and 0x20 <= mm[j - 1] <= 0x7e:
                    j -= 1
                n = mm[j:i].decode("ascii", "replace").split("::")[-1]
                if n and n not in out:
                    out.append(n)
            i = mm.find(marker, i + 1)
    return out


if __name__ == "__main__":
    kind = sys.argv[sys.argv.index("--kind") + 1] if "--kind" in sys.argv else "Material"
    res = names(sys.argv[1], kind)
    print(json.dumps(res) if "--json" in sys.argv else "\n".join(res))
