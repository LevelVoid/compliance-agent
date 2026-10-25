# Geo-Isolated Compliance Audit Agent

Continuous compliance review for source code, judged by a locally hosted model
instead of a rules engine. Answers the question a developer actually asks — *"is
this going to cost me a fine?"* — with a control citation, a severity, and a patched
line.

```bash
python audit.py run --path fixtures/bad        # 0/100  CRITICAL EXPOSURE
python audit.py run --path fixtures/fixed      # 100/100 COMPLIANT
```

---

## What it does

Pattern matching raises **suspects** for free. The model **adjudicates** each one and
is allowed to reject it. A deterministic gate then throws out anything the model
cannot anchor to real source.

The point is not more findings. It is fewer, correct ones:

```
7 suspects → 6 confirmed, 1 rejected
  rejected  src/cache.ts:6 — Hashes public file contents for an HTTP cache key.
            Not a credential digest, so a broken hash carries no authentication risk.
```

A rules scanner fails that file. This does not. That single rejection is the whole
product argument.

## Pipeline

| Stage | Module | Cost | What it guarantees |
|---|---|---|---|
| Ingest | `pipeline/snapshot.py` | free | shallow clone, file cap, never executes repo code |
| Chunk | `pipeline/chunk.py` | free | function-sized units with real line numbers |
| Detect | `pipeline/detect.py` | free | recall. Intentionally noisy |
| Adjudicate | `pipeline/adjudicate.py` | 1 call per chunk | precision, structured JSON |
| Validate | `pipeline/validate.py` | free | evidence must exist verbatim; line must be in range |
| Score | `pipeline/score.py` | free | 0–100, verdict, statutory exposure per control |
| Alert | `notify/` | free | one seam, three transports |

## Durability

The audit is a Temporal workflow, because a large repo takes hours and a worker
that dies at file 300,000 must not restart at zero.

```
ComplianceAuditWorkflow            WorkflowId = audit:{repo}:{commit}
├─ discover_files()
├─ evaluate_file() per file       ← one checkpoint each, write-once artifact
│    heartbeats every 2s, retried on worker death
│    continue_as_new() when history approaches the ceiling
├─ finalise()                     ← deterministic scoring, no model calls
└─ notify()                       ← outbox | slack | webhook
```

Three rules make it real: workflow state holds file *references* (never contents),
every activity is idempotent (content-keyed artifacts), and `continue_as_new` carries
a cursor rather than an accumulation.

Verified behaviour — kill the worker mid-run and the workflow resumes at the next
file:

```
artifacts before kill: 2
worker killed at 17:06:52
Pending Activities: 1
artifacts after resume: 6
```

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # fill in, or leave empty and use MOCK_LLM=1
```

**Without Temporal** (fastest loop, real files, no server):

```bash
MOCK_LLM=1 .venv/bin/python audit.py run --path fixtures/bad
```

**With Temporal** (three terminals):

```bash
./bin/temporal server start-dev              # UI on :8233
.venv/bin/python -m workflows.worker
.venv/bin/python -m workflows.start --path fixtures/bad --slow 2
```

**Against a real repository:**

```bash
.venv/bin/python audit.py run --clone OWASP/NodeGoat --frameworks SOC2,GDPR --max-files 12
```

**Before pointing it at a model endpoint**, verify the endpoint — do this first, not
last:

```bash
.venv/bin/python audit.py probe
```

## Controls

`controls.yaml` is the catalog: five controls across SOC2, HIPAA and GDPR, each with
a detector, a severity and an exposure string. It is loaded once and keyed by
`(framework, id)`.

Framework selection scopes **evaluation**, never **storage**. Selecting SOC2 only
changes which control cards reach the prompt — it does not partition the index, so
switching frameworks never forces a re-index.

Adding a framework is a YAML edit. Adding a detector is one function in
`pipeline/detect.py`.

## Layout

```
audit.py              CLI (no Temporal required)
settings.py           env-only config
pipeline/             pure async functions — no workflow, no notifier imports
workflows/            Temporal wrappers over pipeline/
notify/               Notifier protocol: outbox | slack | nudgebee
controls.yaml         the catalog
fixtures/bad|fixed/   the 6-violation demo tree and its remediation
docs/specs/           architecture, durability, integration contracts
```

The import rule that keeps this integrable: **`pipeline/` never imports
`workflows/` or `notify/`.** Temporal and Nudge Bee are additive layers, not
dependencies of the analysis.

## Status

| Real | Stubbed |
|---|---|
| 4 detectors, 5 controls, scoring | JS/TS chunker is a brace counter (tree-sitter next) |
| Temporal durability, verified under `kill -9` | Supermemory indexing (not wired) |
| Slack + webhook + outbox delivery | GitHub App (Action/SARIF first — see spec 04) |
| Evidence-anchored validation | Geo cells, Next.js dashboard |

Indicative only. Not legal advice.
