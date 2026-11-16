"""Explicit lifecycle for every background job in AssetBridge (scans, health checks, preview renders, sends, downloads,
SPZ rebuilds, splat conversions, and multi-request sessions).

Before this module each of those kept its own ad-hoc "running" flag: requests made while busy were silently dropped,
failures only reached print(), and nothing - the UI, a developer or an agent - could tell which request produced which
result or wait for it. Every job now moves through one declared state machine:

    queued ──> running ──> succeeded
       │          ├──────> failed
       └──────────┴──────> cancelled

TRANSITIONS is the single source of truth; any other move raises IllegalTransition.

Concurrency policy per kind+key (`if_busy`):
  "queue" - at most one running and one queued job; later requests MERGE into the queued one (scopes are unioned),
            so a rescan asked for during a scan always happens, exactly once, after it. (scan, health, previews)
  "join"  - later requests get the running job back; no extra work. (SPZ rebuild, conversion, downloads)

Jobs that span several HTTP requests (an upload, then a second request that completes it) are opened with `open()`
and moved on by the caller; a `deadline` turns one that never finishes into `failed` instead of hanging forever.

HTTP: GET /api/jobs[?active=1&kind=..&limit=..], GET /api/jobs/<id>[?wait=<s>] (blocks until the job ends or the
wait runs out - the way to await work without sleep loops). Jobs live in memory (newest KEEP kept).
"""
import collections
import itertools
import threading
import time
import traceback

QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED = "queued", "running", "succeeded", "failed", "cancelled"
TRANSITIONS = {
    QUEUED: {RUNNING, CANCELLED},
    RUNNING: {SUCCEEDED, FAILED, CANCELLED},
    SUCCEEDED: set(), FAILED: set(), CANCELLED: set(),
}
TERMINAL = {SUCCEEDED, FAILED, CANCELLED}
ACTIVE = {QUEUED, RUNNING}
KEEP = 200
LOG_LINES = 60


class IllegalTransition(RuntimeError):
    pass


class Job(object):
    def __init__(self, registry, jid, kind, key, label, scope, deadline):
        self._r = registry
        self.id = jid
        self.kind, self.key, self.label = kind, key, label
        self.scope = scope            # None = everything; else a set (merged across queued requests)
        self.state = QUEUED
        self.step = ""                # what it is doing now, in words
        self.done = self.total = 0    # progress counters (total 0 = unknown)
        self.message = ""             # one-line outcome
        self.error = None
        self.result = None            # JSON-able payload for callers/agents
        self.requests = 1             # how many requests this job answers (merged duplicates)
        self.created_at, self.started_at, self.finished_at = time.time(), None, None
        self.deadline = deadline      # absolute time; an open job past it fails ("never finished")
        self.log = collections.deque(maxlen=LOG_LINES)

    # -- transitions (all go through the registry lock) --
    def _to(self, new, **fields):
        with self._r._cv:
            if new not in TRANSITIONS[self.state]:
                raise IllegalTransition("%s %s: %s -> %s" % (self.kind, self.id, self.state, new))
            self.state = new
            for k, v in fields.items():
                setattr(self, k, v)
            if new == RUNNING:
                self.started_at = time.time()
            if new in TERMINAL:
                self.finished_at = time.time()
            self._r._cv.notify_all()
        if new in TERMINAL:
            self._r._on_terminal(self)

    def start(self):
        self._to(RUNNING)

    def succeed(self, message="", result=None):
        self._to(SUCCEEDED, message=message or self.message, result=result if result is not None else self.result)

    def fail(self, error):
        self._to(FAILED, error=str(error), message=str(error).splitlines()[0][:300] if str(error) else "failed")

    def cancel(self, why="cancelled"):
        self._to(CANCELLED, message=why)

    # -- progress (no state change) --
    def progress(self, step=None, done=None, total=None):
        with self._r._cv:
            if step is not None:
                self.step = step
            if done is not None:
                self.done = done
            if total is not None:
                self.total = total
            self._r._cv.notify_all()

    def write(self, line):
        line = str(line).rstrip()
        if line:
            self.log.append(line)

    @property
    def active(self):
        return self.state in ACTIVE

    def to_dict(self, full=False):
        d = {"id": self.id, "kind": self.kind, "key": self.key, "label": self.label, "state": self.state,
             "step": self.step, "done": self.done, "total": self.total, "message": self.message, "error": self.error,
             "requests": self.requests, "created_at": self.created_at, "started_at": self.started_at,
             "finished_at": self.finished_at,
             "scope": None if self.scope is None else sorted(str(s) for s in self.scope)}
        if full:
            d["result"] = self.result
            d["log"] = list(self.log)
        return d


class Jobs(object):
    def __init__(self, keep=KEEP):
        self._cv = threading.Condition()
        self._jobs = collections.OrderedDict()
        self._ids = itertools.count(1)
        self._keep = keep
        self._runners = {}   # job id -> (fn, if_busy) for queued jobs

    # ---------------------------------------------------------------- creation
    def _new(self, kind, key, label, scope, deadline=None):
        jid = "%s-%d" % (kind, next(self._ids))
        j = Job(self, jid, kind, key, label or kind, scope, deadline)
        self._jobs[jid] = j
        while len(self._jobs) > self._keep:
            old = next(iter(self._jobs))
            if self._jobs[old].active:
                break
            del self._jobs[old]
        return j

    def _find(self, kind, key, state):
        for j in reversed(self._jobs.values()):
            if j.kind == kind and j.key == key and j.state == state:
                return j
        return None

    def submit(self, kind, fn, key=None, label=None, scope=None, if_busy="queue"):
        """Run fn(job) on a worker thread. fn returns a message string or a dict (job.result) or None.
        Returns the job that will answer this request (a running one when joined, a queued one when merged)."""
        with self._cv:
            running = self._find(kind, key, RUNNING)
            queued = self._find(kind, key, QUEUED)
            if running and if_busy == "join":
                running.requests += 1
                return running
            if queued:  # merge into the follow-up that is already waiting
                queued.requests += 1
                queued.scope = None if (queued.scope is None or scope is None) else queued.scope | set(scope)
                return queued
            job = self._new(kind, key, label, None if scope is None else set(scope))
            self._runners[job.id] = fn
            if running:  # runs when the current one ends (see _on_terminal)
                return job
        self._launch(job)
        return job

    def open(self, kind, key=None, label=None, deadline_s=600):
        """A job driven by the caller across several requests: returned already running."""
        with self._cv:
            job = self._new(kind, key, label, None, time.time() + deadline_s if deadline_s else None)
        job.start()
        return job

    def run(self, kind, fn, key=None, label=None, if_busy="join", timeout=None):
        """Run fn(job) on THIS thread (inside a request). With if_busy='join' and the same kind+key already running
        elsewhere, wait for that one instead of doing the work twice. Returns the finished job."""
        with self._cv:
            running = self._find(kind, key, RUNNING)
            if running and if_busy == "join":
                running.requests += 1
            else:
                running = None
                job = self._new(kind, key, label, None)
        if running:
            return self.wait(running.id, timeout)
        self._execute(job, fn)
        return job

    # ---------------------------------------------------------------- execution
    def _launch(self, job):
        with self._cv:  # submit() and _on_terminal() can both reach a queued job; only one may start it
            fn = self._runners.pop(job.id, None)
        if fn is None:
            return
        threading.Thread(target=self._execute, args=(job, fn), name="job-" + job.id, daemon=True).start()

    def _execute(self, job, fn):
        try:
            job.start()
        except IllegalTransition:  # cancelled while queued
            return
        try:
            out = fn(job)
            if job.state != RUNNING:   # fn finished it itself (e.g. cancelled)
                return
            if isinstance(out, dict):
                job.succeed(out.get("message", ""), out)
            else:
                job.succeed(out or "")
        except Exception as e:
            traceback.print_exc()
            job.write(traceback.format_exc())
            if job.state == RUNNING:
                job.fail("%s: %s" % (type(e).__name__, e))

    def _on_terminal(self, job):
        with self._cv:
            nxt = self._find(job.kind, job.key, QUEUED)
            go = nxt is not None and nxt.id in self._runners and not self._find(job.kind, job.key, RUNNING)
        if go:
            self._launch(nxt)

    def cancel(self, jid, why="cancelled"):
        j = self.get(jid)
        if j and j.state == QUEUED:
            self._runners.pop(j.id, None)
            j.cancel(why)
        return j

    # ---------------------------------------------------------------- queries
    def _expire(self):
        now = time.time()
        for j in list(self._jobs.values()):
            if j.state == RUNNING and j.deadline and now > j.deadline:
                try:
                    j.fail("never finished (no update for %d s)" % int(j.deadline - (j.started_at or j.created_at)))
                except IllegalTransition:
                    pass

    def get(self, jid):
        self._expire()
        return self._jobs.get(jid)

    def latest(self, kind, key=None, any_key=False):
        self._expire()
        for j in reversed(list(self._jobs.values())):
            if j.kind == kind and (any_key or j.key == key):
                return j
        return None

    def list(self, kind=None, active_only=False, limit=50):
        self._expire()
        out = [j for j in reversed(list(self._jobs.values()))
               if (kind is None or j.kind == kind) and (not active_only or j.active)]
        return out[:limit]

    def wait(self, jid, timeout=None):
        """Block until the job is terminal or `timeout` seconds pass; returns the job (check .state)."""
        end = None if timeout is None else time.time() + timeout
        with self._cv:
            while True:
                j = self._jobs.get(jid)
                if j is None or j.state in TERMINAL:
                    return j
                if j.deadline and time.time() > j.deadline:
                    break
                left = None if end is None else end - time.time()
                if left is not None and left <= 0:
                    return j
                self._cv.wait(min(left, 1.0) if left is not None else 1.0)
        self._expire()
        return self._jobs.get(jid)
