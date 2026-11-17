"""Every automated check in one command.  py -3 tools/check.py  (exit code 0 = all pass)

1. every .py / .pyp file compiles
2. every web/*.js file parses (needs Node.js; skipped without it)
3. every tools/test_*.py passes (test_colorspace needs Blender and skips without it)

Run it before and after a change. Nothing here touches your libraries, Cinema 4D or Blender scenes: the tests use
temp folders and a background Blender.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "node_modules", "__pycache__", "cache", "dist", "build", "vendor"}


def main():
    failed = []
    n = 0
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith((".py", ".pyp")):
                p = os.path.join(root, f)
                n += 1
                try:
                    with open(p, "r", encoding="utf-8") as fh:
                        compile(fh.read(), p, "exec")
                except SyntaxError as e:
                    failed.append("compile %s: %s" % (os.path.relpath(p, ROOT), e))
    print("compile: %d Python files%s" % (n, "" if not failed else ", %d failed" % len(failed)))

    node = shutil.which("node")
    web = os.path.join(ROOT, "web")
    if node:
        js = sorted(f for f in os.listdir(web) if f.endswith(".js"))
        for f in js:
            r = subprocess.run([node, "--check", os.path.join(web, f)], capture_output=True, text=True)
            if r.returncode:
                failed.append("web/%s: %s" % (f, (r.stderr or r.stdout).strip()[-400:]))
        print("node --check: %d web scripts" % len(js))
    else:
        print("node --check: skipped (Node.js not found)")

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    tools = os.path.join(ROOT, "tools")
    for t in sorted(f for f in os.listdir(tools) if f.startswith("test_") and f.endswith(".py")):
        r = subprocess.run([sys.executable, os.path.join(tools, t)], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=900)
        out = (r.stdout or "").strip().splitlines()
        fails = [ln for ln in out if ln.startswith("FAIL")]
        print("%s: %s" % (t, "FAILED" if r.returncode else (out[-1] if out else "ok")))
        if r.returncode:
            failed.append("%s:\n%s" % (t, "\n".join(fails[:10]) or ((r.stdout or "") + (r.stderr or ""))[-1500:]))

    if failed:
        print("\n%d CHECK(S) FAILED" % len(failed))
        for f in failed:
            print(" - " + f)
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
