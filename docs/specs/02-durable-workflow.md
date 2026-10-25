# 02 — Durable workflow

## The failure this exists for

A 400k-file repository takes hours. If the worker dies at file 300,000, a naive
pipeline restarts from file 1. Temporal persists each activity's outcome in the
event history, so a restarting worker replays that history and re-executes only what
was in flight.

## Graph

```
ComplianceAuditWorkflow(state)                    id = audit:{repo}:{commit}
│
├─ discover_files(state) → [{path, lang, size}]
│
├─ while cursor < len(refs):
│     evaluate_file(audit_id, root, frameworks, delay, ref)
│        ├─ artifact exists?  → return cached counts, no model call
│        ├─ detect → adjudicate → validate
│        ├─ heartbeat every 2s (async beater task)
│        └─ write out/findings/{audit_id}/{sha256(path)[:16]}.json  (write-once)
│     if info().is_continue_as_new_suggested(): continue_as_new(state)
│
├─ finalise(state)  → reads artifacts, scores, renders, returns summary
└─ notify(summary, repo) → Notifier.send()
```

## Why it survives `kill -9`

- **History is the state.** No workflow variable is the only copy of anything.
- **The unit of work is one file**, so the worst case is re-auditing one file.
- **Artifacts are content-keyed and write-once.** A retried activity finds its
  artifact and returns without calling the model. Retries are free and cannot
  duplicate findings.
- **Heartbeats** (2s interval, 8s timeout) mean a dead worker is detected in seconds
  rather than at the end of a 5-minute `start_to_close` window.

Verified:

```
artifacts before kill: 2      # worker killed with `kill -9` at file 3
Pending Activities: 1         # workflow stayed RUNNING
artifacts after resume: 6     # new worker finished, did not redo files 1-2
```

## Limits and the cursor

Temporal enforces **51,200 history events per run** and **2,000 in-flight activities
(500 recommended)**. A repo of 10,000 files exceeds the first limit, so:

```python
if workflow.info().is_continue_as_new_suggested():
    workflow.continue_as_new(state)
```

`state` carries `cursor` and counters, not findings. The new run continues at
`refs[cursor]`; artifacts remain on disk, so nothing is recomputed. History resets.

## What is never passed through history

| Never in state | Where it lives |
|---|---|
| file contents | read from disk inside the activity (object store in production) |
| full findings array between files | one artifact file per source file |
| credentials | `settings`, read inside the activity only |

Activities take primitives (`audit_id, root, frameworks, delay, ref`) rather than the
whole state object. Passing `state` to every activity makes history grow with the run.

## Retry policy

| Activity | Timeout | Attempts | Notes |
|---|---|---|---|
| `discover_files` | 2 min | 3 | idempotent read |
| `evaluate_file` | 5 min start-to-close, 8s heartbeat | 3 | write-once artifact |
| `finalise` | 2 min | 2 | deterministic |
| `notify` | 30s | 2 | transports are retry-safe |

## Sandbox constraint

Temporal imports workflow modules inside a deterministic sandbox that forbids
filesystem calls at import time. Consequence, learned the hard way:

- `workflows/audit_workflow.py` imports **only** `temporalio` and stdlib at module
  scope.
- Every `pipeline.*` and `settings` import happens **inside** an activity body.

Symptom if violated: `RestrictedWorkflowAccessError: Cannot access
pathlib.Path.resolve.__call__ from inside a workflow` on worker startup, before any
workflow runs.

## Positioning versus `audit.py`

`audit.py` runs the same pipeline in-process with no Temporal. It is the fastest
debug loop and the fallback demo. The workflow and the CLI converge on the same
`pipeline/` functions — that is the property that keeps the durable path honest.
