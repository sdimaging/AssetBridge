"""Tests for server/jobs.py - the explicit job state machine.  py -3.13 tools/test_jobs.py  (exit code 0 = all pass)"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
import jobs as J  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(detail)))
    if not cond:
        failures.append(name)


def gate():
    ev = threading.Event()
    return ev, (lambda job: (ev.wait(5), "done")[1])


# 1. legal path + result
r = J.Jobs()
j = r.submit("t", lambda job: {"message": "hi", "n": 3})
r.wait(j.id, 5)
check("succeeds with dict result", j.state == J.SUCCEEDED and j.result["n"] == 3 and j.message == "hi", j.to_dict(True))

# 2. failure is recorded, not lost
j = r.submit("t", lambda job: 1 / 0)
r.wait(j.id, 5)
check("exception -> failed with error text", j.state == J.FAILED and "ZeroDivisionError" in j.error, j.to_dict())

# 3. illegal transition raises
try:
    j.start()
    check("terminal job cannot restart", False)
except J.IllegalTransition:
    check("terminal job cannot restart", True)

# 4. queue policy: requests while running merge into ONE follow-up, scopes unioned
r = J.Jobs()
ev, fn = gate()
a = r.submit("scan", fn, scope=["A"])
time.sleep(0.1)
b = r.submit("scan", fn, scope=["B"])
c = r.submit("scan", fn, scope=["C"])
check("second request queues behind the running one", a.state == J.RUNNING and b.state == J.QUEUED, (a.state, b.state))
check("third request merges into the queued one", b is c and b.requests == 2 and b.scope == {"B", "C"}, b.to_dict())
ev.set()
r.wait(a.id, 5)
r.wait(b.id, 5)
check("queued follow-up runs after the first ends", a.state == J.SUCCEEDED and b.state == J.SUCCEEDED, (a.state, b.state))

# 5. scope None (everything) absorbs specific scopes
r = J.Jobs()
ev, fn = gate()
a = r.submit("scan", fn)
b = r.submit("scan", fn, scope=["X"])
r.submit("scan", fn)
check("full request widens the queued scope to everything", b.scope is None, b.to_dict())
ev.set()
r.wait(b.id, 5)

# 6. join policy: no duplicate work
r = J.Jobs()
ev, fn = gate()
a = r.submit("build", fn, if_busy="join")
b = r.submit("build", fn, if_busy="join")
check("join returns the running job", a is b and a.requests == 2)
ev.set()
r.wait(a.id, 5)

# 7. keys are independent
r = J.Jobs()
ev, fn = gate()
a = r.submit("promo", fn, key="x.png")
b = r.submit("promo", fn, key="y.png")
time.sleep(0.1)
check("different keys run side by side", a.state == J.RUNNING and b.state == J.RUNNING, (a.state, b.state))
ev.set()

# 8. wait with timeout returns while still running
r = J.Jobs()
ev, fn = gate()
a = r.submit("slow", fn)
t0 = time.time()
w = r.wait(a.id, 0.3)
check("wait times out without ending the job", w.state == J.RUNNING and time.time() - t0 < 2, w.state)
ev.set()
check("wait returns at the end", r.wait(a.id, 5).state == J.SUCCEEDED)

# 9. open() jobs: caller-driven, deadline fails a forgotten one
r = J.Jobs()
s = r.open("save", key="asset", deadline_s=0.2)
check("open job starts running", s.state == J.RUNNING)
time.sleep(0.3)
check("past its deadline it fails instead of hanging", r.get(s.id).state == J.FAILED and "never finished" in s.error, s.to_dict())
s2 = r.open("save", key="asset")
s2.progress(step="waiting for the promo")
s2.succeed("saved")
check("open job finished by its caller", s2.state == J.SUCCEEDED and s2.step == "waiting for the promo")

# 10. run() inline joins a running duplicate instead of converting twice
r = J.Jobs()
calls = []


def conv(job):
    calls.append(1)
    time.sleep(0.3)
    return "converted"


t = threading.Thread(target=lambda: r.run("convert", conv, key="k"))
t.start()
time.sleep(0.05)
j2 = r.run("convert", conv, key="k", timeout=5)
t.join()
check("inline duplicate waits for the first, work done once", len(calls) == 1 and j2.state == J.SUCCEEDED, calls)

# 11. cancel a queued job
r = J.Jobs()
ev, fn = gate()
a = r.submit("q", fn)
b = r.submit("q", fn)
r.cancel(b.id)
ev.set()
r.wait(a.id, 5)
time.sleep(0.1)
check("cancelled queued job never runs", b.state == J.CANCELLED)

# 12. no double start when submit races the end of the running job
r = J.Jobs()
runs = []
for _ in range(200):
    a = r.submit("race", lambda job: runs.append(job.id))
    r.wait(a.id, 5)
check("every job ran exactly once", len(runs) == len(set(runs)) == 200, len(runs))

print("\n%d failed" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
