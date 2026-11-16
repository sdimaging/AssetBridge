"""Extract vendor archives in place, verify every entry, then delete the archive.

    py -3.13 tools/extract_archives.py <archive> [...] [--keep] [--dry-run] [--dest <folder>]

Each archive is extracted next to itself with 7-Zip (zip/rar/7z). Entries 7-Zip writes are CRC-checked by
7-Zip during extraction; entries that already existed on disk (skipped, never overwritten) are compared by
size and CRC32. Only when every entry matches is the archive deleted (unless --keep). One JSON line per
archive goes to cache/extract_log.jsonl.
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "cache", "extract_log.jsonl")
SEVENZIP = next((p for p in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe") if os.path.exists(p)), None)


PASSWORD = None  # --password (never stored)


def _pw():
    return ["-p" + PASSWORD] if PASSWORD else ["-p"]  # "-p" alone: never block on a password prompt


def entries(archive):
    """[{path, size, crc, dir}] from 7z l -slt."""
    out = subprocess.run([SEVENZIP, "l", "-slt", "-ba"] + _pw() + [archive], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RuntimeError("cannot list: " + out.stderr.strip()[-300:])
    items, cur = [], {}
    for line in out.stdout.splitlines() + [""]:
        if not line.strip():
            if cur.get("Path"):
                items.append({"path": cur["Path"], "size": int(cur.get("Size") or 0), "crc": (cur.get("CRC") or "").upper(),
                              "dir": "D" in (cur.get("Attributes") or "") or cur.get("Folder") == "+",
                              "encrypted": cur.get("Encrypted") == "+"})
            cur = {}
            continue
        if " = " in line:
            k, v = line.split(" = ", 1)
            cur[k.strip()] = v.strip()
    return items


def crc32(path):
    out = subprocess.run([SEVENZIP, "h", "-scrcCRC32", path], capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and len(parts[0]) == 8 and parts[-1] == os.path.basename(path):
            return parts[0].upper()
    return None


def process(archive, keep, dry, dest=None):
    t0 = time.time()
    dest = dest or os.path.dirname(archive)
    rec = {"archive": archive, "ok": False}
    items = [e for e in entries(archive) if not e["dir"]]
    rec["entries"] = len(items)
    if any(e.get("encrypted") for e in items) and not PASSWORD:  # never leave empty placeholders from a password prompt
        rec["error"] = "password-protected (not extracted)"
        return rec
    pre = {e["path"] for e in items if os.path.exists(os.path.join(dest, e["path"]))}
    rec["preexisting"] = len(pre)
    if dry:
        rec.update(dry_run=True, bytes=sum(e["size"] for e in items))
        return rec
    x = subprocess.run([SEVENZIP, "x", "-y", "-aos"] + _pw() + ["-o" + dest, archive], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if x.returncode != 0:
        rec["error"] = "7z exit %d: %s" % (x.returncode, (x.stderr or x.stdout).strip()[-400:])
        return rec
    bad = []
    for e in items:
        p = os.path.join(dest, e["path"])
        if not os.path.exists(p) or os.path.getsize(p) != e["size"]:
            bad.append(e["path"])
        elif e["path"] in pre and e["crc"] and crc32(p) != e["crc"]:
            bad.append(e["path"] + " (crc)")
    rec["mismatched"] = bad[:20]
    rec["ok"] = not bad
    if rec["ok"] and not keep:
        os.remove(archive)
        rec["deleted"] = True
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("archives", nargs="+")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dest", help="extract here instead of next to the archive")
    ap.add_argument("--password", help="archive password (used for this run only, not stored)")
    args = ap.parse_args()
    global PASSWORD
    PASSWORD = args.password
    if not SEVENZIP:
        sys.exit("7-Zip not found")
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as log:
        for a in args.archives:
            try:
                r = process(a, args.keep, args.dry_run, args.dest)
            except Exception as e:
                r = {"archive": a, "ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            log.write(json.dumps(r) + "\n")
            log.flush()
            print("%s %s %s" % ("OK " if r.get("ok") or r.get("dry_run") else "ERR", os.path.basename(a),
                                {k: r[k] for k in ("entries", "preexisting", "deleted", "error", "mismatched", "seconds", "bytes") if r.get(k)}), flush=True)


if __name__ == "__main__":
    main()
