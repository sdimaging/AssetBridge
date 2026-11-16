"""Convert .max files with headless 3ds Max, in chunks (one Max session per chunk).

    py -3.13 tools/max/run_batch.py <folder or .max file> [...] [--chunk 60] [--overwrite] [--limit N]

Writes <name>.fbx next to each .max and appends one JSON line per file to cache/max/results.jsonl.
Files that already have an .fbx are skipped (their report is still refreshed) unless --overwrite.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "tools", "max", "convert_max.py")
OUT = os.path.join(ROOT, "cache", "max")


def find_max_batch():
    c = sorted(glob.glob(r"C:\Program Files\Autodesk\3ds Max 20*\3dsmaxbatch.exe"))
    if not c:
        sys.exit("3dsmaxbatch.exe not found (3ds Max 2023+ required)")
    return c[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--chunk", type=int, default=60)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ext", default=".max", help=".max, or .fbx for vendor FBX volumes (materials read, file kept)")
    ap.add_argument("--report", default=os.path.join(OUT, "results.jsonl"))
    ap.add_argument("--skip", nargs="*", default=["scene", "growfx"], help="skip files whose name contains these")
    args = ap.parse_args()
    files = []
    for p in args.paths:
        if os.path.isdir(p):
            for dp, _, fs in os.walk(p):
                files += [os.path.join(dp, f) for f in fs if f.lower().endswith(args.ext) and not f.startswith("._")
                          and not any(x in f.lower() for x in args.skip)]
        elif p.lower().endswith(args.ext) and not os.path.basename(p).startswith("._"):
            files.append(p)
    files = sorted(files)
    if args.limit:
        files = files[:args.limit]
    os.makedirs(OUT, exist_ok=True)
    report = os.path.abspath(args.report)
    exe = find_max_batch()
    print("%d %s files, %d per Max session" % (len(files), args.ext, args.chunk))
    t0 = time.time()
    for i in range(0, len(files), args.chunk):
        chunk = files[i:i + args.chunk]
        job = os.path.join(OUT, "job_%s_%d.json" % (os.path.splitext(os.path.basename(report))[0], i))
        with open(job, "w", encoding="utf-8") as fh:
            json.dump({"files": chunk, "report": report, "overwrite": args.overwrite}, fh)
        env = dict(os.environ, MAX_JOB=job)
        subprocess.run([exe, SCRIPT], env=env, capture_output=True)
        print("  %d/%d done (%.0fs)" % (min(i + args.chunk, len(files)), len(files), time.time() - t0), flush=True)
    print("Report:", report)


if __name__ == "__main__":
    main()
