"""Run probe_max.py on one .max file:  py -3.13 tools/max/probe.py <file.max> <out.txt>"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_batch import find_max_batch  # noqa: E402

env = dict(os.environ, MAX_PROBE=sys.argv[1], MAX_PROBE_OUT=sys.argv[2])
r = subprocess.run([find_max_batch(), os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_max.py")], env=env, capture_output=True, text=True)
print("exit", r.returncode, (r.stdout or "")[-600:], (r.stderr or "")[-600:])
