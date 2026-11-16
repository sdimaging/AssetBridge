# State machines in AssetBridge

Everything AssetBridge does in the background has one explicit lifecycle. This file is the map: what each process is, which states it moves through, where the state lives, and how a person, a developer or an agent can watch it or wait for it.

## The job model (`server/jobs.py`)

Every background process is a **job**. A job moves through exactly these states, and any other move raises `IllegalTransition`:

```
queued ──> running ──> succeeded
   │          ├──────> failed
   └──────────┴──────> cancelled
```

A job records:
- `id` (for example `scan-12`), `kind`, `key` and `label`
- `step` (what it is doing now, in words) and `done`/`total`
- `message` (a one-line outcome) and `error`
- `result` (a JSON payload) and `log` (the last 60 lines of output)
- `requests` (how many requests it answers) and its timestamps

The newest 200 jobs are kept in memory.

### What happens when a second request arrives

The `if_busy` policy is set for each kind:
- **`queue`**: at most one job runs and one waits. Later requests merge into the waiting job, and their scopes are combined. A rescan asked for during a scan is therefore never dropped; it runs exactly once, after the current one.
- **`join`**: later requests get the running job back, and no extra work is done.

### Jobs that span several requests

These are opened with `jobs.open()` and moved on by their caller. For example, an upload starts it and a second request finishes it. A `deadline` turns one that never finishes into `failed` ("never finished") instead of leaving it running forever.

## Job kinds

| kind | key | policy | started by | ends when |
|---|---|---|---|---|
| `scan` | none | queue (scope = libraries, merged) | startup, Rescan, add library, restore, any save that changes an asset's files | the catalogue is updated; message `N seen, N changed, N removed`; fails if a library errored |
| `previews` | none | queue | the end of every scan | Blender exits; progress comes from the `PREVIEW n/m` lines, and a non-zero exit is a failure with the log kept |
| `health` | none | queue | first view, Check again, the end of a scan, clearing the splat cache | the report is written (`/api/health` still returns it, plus `job`) |
| `convert` | output path | join (runs inline in the send) | a send that needs a PLY↔SPZ conversion | the converted file exists; two sends of the same splat share one conversion |
| `send` | asset + target + options | join (runs inline in the request) | `POST /api/send` | the receiver replies; a refused or failed import is `failed`, and the full reply is in `result` |
| `fab_index` | none | join | `POST /api/fab/index`, or the first start with a Fab library and no index | `cache/fab/index.json` is rewritten from Fab's listing search; then every Fab library is rescanned |
| `fab_pull` | asset id | join (inline when a send needs it) | `POST /api/fab/pull`, the card's Get, or a send of a remote asset | the zip is downloaded (3 at a time), extracted and indexed; the asset is local under the same id. Progress = MB downloaded |

## For agents: watching and waiting

```
GET  /api/jobs?active=1                     # what is running or queued right now
GET  /api/jobs?kind=save&limit=5            # recent jobs of one kind (newest first); also returns the transition table
GET  /api/jobs/<id>                         # one job, with result and log
GET  /api/jobs/<id>?wait=60                 # blocks until the job ends (or 60 s pass; the maximum is 120). No sleep loops.
GET  /api/status                            # also lists the active jobs (the UI polls this every 2 s)
```

Every route that starts work returns the job that will answer it:
- `/api/rescan` and the health `rescan` action return `{"ok": true, "job": "scan-N"}`.
- `/api/health/run` returns `{"ok": true, "job": "health-N"}`.
- `/api/restore` returns `{"ok": true, "rescan": [...], "jobs": ["scan-N"]}`.
- `/api/send` adds `"job"`.
- `/api/fab/pull` returns `{"ok": true, "job": "fab_pull-N", "joined": <bool>}`; `/api/fab/index` returns `{"ok": true, "job": "fab_index-N"}`.

The pattern is: start it, take the job id, then `GET /api/jobs/<id>?wait=...` until `state` is `succeeded`, `failed` or `cancelled`. Jobs live in memory, so a AssetBridge restart clears the history; the files on disk are the lasting result.


## Receivers (`c4d/Bridge2/bridge2.pyp`, `blender/bridge2_blender/__init__.py`)

An import waits on the main thread and moves:

```
queued → running → done        (or queued → abandoned)
```

- If AssetBridge stops waiting (600 s) before the main thread reaches the import, the import is **abandoned** and skipped. It no longer lands in the scene minutes later as a "ghost". The reply says nothing was added.
- If it is already running, the reply says it will still appear.
- `ping` reports `queued`, the imports waiting on a busy main thread.
- AssetBridge waits 620 s (`SEND_TIMEOUT`), longer than the receivers, so their own timeout reply reaches the user.

**Selection ownership** (`bridge2_import.py`): `state["auto_selected"]` and `state["auto_dirty"]` record "the receiver selected this, the user hasn't touched anything since". Geometry, splat, instance and HDRI imports all record it. Before, HDRI did not, and the next material send landed on the sky.

## Fab assets: remote → local (`server/fab.py`)

```
remote (fab://<uid>, thumbnail only) ──pull──> local (files in the library folder + fab.json)
            ^                                              │
            └──────────────── evict (Free up space) ───────┘
```

- The state is on disk: a folder with `fab.json` is local; every other indexed listing is remote. A scan derives it.
- The asset id never changes (`<library>:<listing uid>`), so favourites, labels and send history survive pulls and evictions.
- Evict refuses while a pull of the same asset runs, and only deletes a folder inside the library that has a `fab.json`.


## Web UI (`web/app.js`)

- **Grid paging:** `S.gen` is the generation, and `reload()` always releases `S.loading`. An in-flight page from an old generation can no longer freeze the grid.
- **Status pill:** responses are applied newest-first. It shows "AssetBridge server not responding…" when the server is unreachable, and shows active jobs (previews, promo, edition, SPZ, conversion) when no scan is running. Hovering lists every active job.
- **Sends:** `S.sending` holds `"<id>|<target>"` for each send in flight, and the buttons are derived from it. Re-rendering the panel can't reset them, and the same send can't start twice. The server's `send` job `join` policy backs this up.
- **Detail image:** refreshes when the preview's `tv` changes, for example after a save, not only when the asset changes.

## Still implicit (on purpose)

These are small, local, and either can't run twice or can be recomputed from disk. A full state machine would add weight without making anything safer.

| what | where | why it stays |
|---|---|---|
| thumbnail per-path locks + `thumb_failures` | `bridge2.py` `make_thumb` | the output file is the state; failures clear on restart |
| removed / restore | `catalog.py` `removed` table | a database row is the state; restore queues a `scan` job, which is never dropped now |
| `SCAN_VERSION` / `TAGS_VERSION` | the `meta` table | schema version markers, checked at startup |
| plugin install, the Max batch converters, archive extraction | `tools/` | one-shot CLI runs with their own jsonl reports |

## Tests

- `py -3.13 tools/test_jobs.py`: the job state machine. It covers the legal and illegal transitions, the queue/merge and join policies, scope widening, wait timeouts, deadlines, inline joins, cancelling, and a 200-run double-start race.
