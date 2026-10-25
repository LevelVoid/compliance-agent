# RegionGuard — Geo-Isolated Compliance Audit Agent

> **Source-code compliance auditing for India's DPDP Act, GDPR, SOC2, and HIPAA — judged by a locally hosted model, not a rules engine.**

[![CI](https://github.com/2005Mohammadali/compliance-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/2005Mohammadali/compliance-agent/actions)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

---

## What it does

RegionGuard scans a GitHub repository for privacy, security, and data-governance violations and maps each one to a specific statutory obligation — the regulation, the clause, and the fine range. It answers the question a developer actually asks: **"Is this going to cost me a fine?"** with a patched line, not just a warning.

```
Score: 0/100   Verdict: CRITICAL EXPOSURE
6 defects · 12 obligations

  src/auth.py:9   HIGH
    Reasonable security safeguards — personal data must not be exposed in logs
      DPDP S.8(5)   Up to INR 250 crore
      GDPR Art. 32  Up to EUR 20M or 4% of global annual turnover
    > logger.info(f"issued session token={token} for user {user['id']}")
```

---

## Demo

```bash
# Run against bundled fixtures (no LLM needed)
MOCK_LLM=1 python audit.py run --path fixtures/bad --frameworks DPDP,SOC2
# → 0/100  CRITICAL EXPOSURE  (6 defects · 12 obligations)

MOCK_LLM=1 python audit.py run --path fixtures/fixed --frameworks DPDP,SOC2
# → 100/100  COMPLIANT

# Or use the web UI
uvicorn api:app --reload --port 8000   # backend
npm run dev                             # frontend at http://localhost:3000
```

---

## Architecture

RegionGuard is built as a strict layered system. Each layer is additive — Temporal can be removed without touching the analysis engine; the LLM can be replaced without touching the workflow.

```
┌───────────────────────────────────────────────────────────────────────┐
│  Web UI  (Next.js 16 · RegionGuard dashboard)                         │
│  POST /api/audit  ·  GET /api/audit/{id}  (poll with progress bar)    │
└───────────────────────────┬───────────────────────────────────────────┘
                            │ HTTP / REST
┌───────────────────────────▼───────────────────────────────────────────┐
│  FastAPI HTTP Server  (api.py)                                        │
│  BackgroundTasks · CORS · Pydantic v2 validation                      │
└──────┬──────────────────────────────────────────────────────┬─────────┘
       │ direct (dev)                                          │ durable (prod)
┌──────▼──────────┐                               ┌───────────▼──────────────┐
│  audit.py (CLI) │                               │  Temporal Workflow       │
│  audit_repo()   │                               │  ComplianceAuditWorkflow │
└──────┬──────────┘                               └───────────┬──────────────┘
       │                                                       │
┌──────▼───────────────────────────────────────────────────────▼──────────────┐
│                         pipeline/  (pure functions)                          │
│                                                                               │
│  snapshot.py   →  chunk.py   →  detect.py   →  adjudicate.py  →  validate.py │
│  (clone/discover)  (split)     (regex)         (LLM call)       (gate)       │
│                                                                               │
│                         score.py  (deterministic scoring)                    │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │
            ┌──────────────────────┼─────────────────────────┐
            ▼                      ▼                          ▼
    notify/outbox.py       notify/slack.py          notify/nudgebee.py
    (JSONL · default)      (Webhook)                (CloudOps gateway)
```

### Two-stage detection

| Stage | Job | Mechanism | Cost |
|---|---|---|---|
| **Detect** | Recall | Regex over parsed functions | Zero tokens |
| **Adjudicate** | Precision | One LLM call per suspect | Bounded by suspects, not file count |

Token spend is proportional to **suspects found**, not repository size. A file with no regex hits costs zero tokens. A 400k-file repo with 50 suspects costs roughly the same as a 1k-file repo with 50 suspects.

### Validation gate (hallucination guard)

Every finding must pass four mechanical checks before it enters the report:

1. `confidence ≥ 0.5`
2. The quoted evidence exists **verbatim** in the source the model was shown
3. The reported line is inside the adjudicated chunk
4. The finding ID (content hash) has not already been accepted

Rejections are shown in the report rather than silently dropped — a visible rejection is proof the gate exists.

### Scoring

```
WEIGHTS = { high: 25, medium: 12, low: 5 }   score = max(0, 100 − Σ weights)
```

| Score | Verdict |
|---|---|
| 90–100 | ✅ COMPLIANT |
| 70–89 | 🟡 AT RISK |
| 40–69 | 🟠 NOT COMPLIANT |
| 0–39 | 🔴 CRITICAL EXPOSURE |

Scoring is **model-free and deterministic** — the same commit always produces the same score across runs, making it usable in an audit trail.

---

## Components

### `pipeline/` — Pure analysis engine

| File | Responsibility |
|---|---|
| `snapshot.py` | Shallow-clone repos, discover source files |
| `chunk.py` | Split large files into function-level chunks |
| `detect.py` | Regex-based suspect detection (5 detectors) |
| `adjudicate.py` | LLM call per suspect, structured JSON output |
| `validate.py` | 4-check validation gate, deduplication |
| `score.py` | Deterministic scoring, markdown/JSON/terminal render |
| `catalog.py` | Load `controls.yaml` into memory |
| `engine.py` | Orchestrate per-file audit, compose `AuditReport` |

**Import rule:** `pipeline/` never imports `workflows/` or `notify/`. The analysis core runs with Temporal uninstalled.

### `workflows/` — Durable execution via Temporal

Temporal makes hours-long audits crash-safe. If a worker dies at file 300,000, it resumes at file 300,001 — not from scratch.

**Three rules that make durability real:**
1. Workflow state carries file *references*, never file contents
2. Each activity writes one **content-keyed, write-once** artifact — retries are free
3. `continue_as_new` carries a cursor to stay under Temporal's 51,200-event history ceiling

```
ComplianceAuditWorkflow
 ├─ discover_files()         → list of {path, lang, size}
 ├─ evaluate_file() × N      → artifact per file (cached on retry)
 │    ├─ heartbeat every 2s
 │    └─ detect → adjudicate → validate → write JSON
 ├─ finalise()               → collapse artifacts, score, render report
 └─ notify()                 → send to outbox / Slack / NudgeBee
```

**Verified:** `kill -9` the worker at file 3 → restart → 6 artifacts total, files 1–2 never recomputed.

### `api.py` — FastAPI HTTP bridge

REST API wrapping `audit_repo()` for the web frontend:

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/audit` | Submit GitHub repo → `audit_id` (immediate) |
| `GET` | `/api/audit/{id}` | Poll status + per-file progress |
| `GET` | `/api/audits` | List session audits |
| `DELETE` | `/api/audit/{id}` | Remove an audit |
| `GET` | `/api/health` | Liveness + LLM config check |

### `notify/` — Alerting adapters

Three adapters behind one `Notifier` protocol — switching transport is an env-var change:

| `NUDGEBEE_MODE` | Transport |
|---|---|
| `outbox` (default) | Appends to `out/notifications.jsonl` — cannot fail |
| `slack` | Posts top 5 defects to `SLACK_WEBHOOK_URL` |
| `webhook` | Posts full payload to `NUDGEBEE_WEBHOOK_URL` |

Each event carries a **deterministic `event_id`** (`sha256(defect_id:repo)`) so retries cannot open duplicate tickets. One event per defect, not per obligation — a line breaching both GDPR and DPDP sends one alert, not two.

### Web UI — Next.js 16 (RegionGuard)

- Framework chip picker (DPDP / SOC2 / GDPR / HIPAA)
- Real-time progress bar (clone → per-file audit)
- Score ring animated SVG, colour-coded to verdict
- Expandable defect cards: evidence snippet, suggested fix, per-obligation table
- Country → framework auto-suggestion

### `controls.yaml` — The compliance catalog

10 controls across 4 frameworks. Adding a framework is a YAML edit. Adding a detector type is one function in `pipeline/detect.py`.

| Framework | Controls | Max exposure |
|---|---|---|
| **DPDP** (India 2023) | S.6, S.8(5) | INR 250 crore |
| **GDPR** | Art. 6, Art. 32 | EUR 20M or 4% global revenue |
| **SOC2** | CC6.1, CC6.6 | Contractual / audit failure |
| **HIPAA** | 164.312(a)(2)(iv) | Tiered OCR civil penalties |

---

## Supermemory Integration (planned)

Supermemory provides **persistent semantic memory** across audit runs — a vector store of past findings indexed by `(repo, control, evidence_hash)`.

Planned capabilities:
- **Cross-run deduplication**: surface when the same defect was found and fixed in a prior audit, closing the loop on remediation
- **Trend analysis**: track compliance score over time per repo and per framework
- **Knowledge distillation**: when a repo has been audited multiple times, surface patterns ("this codebase consistently fails CC6.6 — likely architectural")
- **RAG over past reports**: let developers ask "has this class of issue appeared before in our codebase?" without re-running the full pipeline

Integration point: `finalise()` activity in `workflows/audit_workflow.py` — after scoring, index defects into Supermemory keyed by `defect_id`. A new audit queries for overlap before calling the LLM, returning cached rationale for identical evidence.

---

## Geo-isolation (target architecture)

```
Request → geo-DNS → regional cell → local model endpoint → attestation

Cell contains:
  ├─ Temporal namespace
  ├─ PostgreSQL (workflow history)
  ├─ Object store (audit artifacts)
  ├─ Vector store (Supermemory node)
  ├─ Model endpoint (vLLM / JarvisLabs)
  └─ Notifier → NudgeBee / Slack
```

No component is shared across cells. An India-region audit never sends data outside India. The attestation (Ed25519-signed JSON committed to `.compliance/`) is checkable by a third party without repo access.

---

## Quickstart

```bash
# 1. Python dependencies
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. Configure LLM (gitignored — never paste keys into a shared terminal)
cp .env.example .env && $EDITOR .env

# 3. Verify the model endpoint
python audit.py probe

# 4a. CLI run
python audit.py run --clone OWASP/NodeGoat --frameworks DPDP,SOC2 --max-files 8

# 4b. Web UI
uvicorn api:app --reload --port 8000    # terminal 1
cd client && npm run dev                 # terminal 2 → http://localhost:3000

# 4c. Durable run (requires Temporal)
./bin/temporal server start-dev          # terminal 1
python -m workflows.worker               # terminal 2
python -m workflows.start --path fixtures/bad --frameworks DPDP,SOC2
```

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_BASE_URL` | Yes | — | OpenAI-compatible endpoint ending in `/v1` |
| `LLM_API_KEY` | If auth required | — | JarvisLabs Token Factory key |
| `LLM_MODEL` | Yes | — | Model ID matching the endpoint catalogue |
| `MOCK_LLM` | No | `0` | `1` = deterministic mock, no network |
| `NUDGEBEE_MODE` | No | `outbox` | `outbox` \| `slack` \| `webhook` |
| `SLACK_WEBHOOK_URL` | If slack mode | — | Slack incoming webhook |
| `NUDGEBEE_WEBHOOK_URL` | If webhook mode | — | NudgeBee POST endpoint |
| `MAX_FILES` | No | `12` | Hard cap on files audited per run |
| `CORS_ORIGIN` | No | `http://localhost:3000` | Allowed origin for the API server |

---

## GitHub integration roadmap

| Stage | What ships | Status |
|---|---|---|
| **1 — Action + SARIF** | Findings in Security → Code Scanning on any repo | Spec complete |
| **2 — GitHub App** | Check Runs, PR annotations, sticky comment, fix PRs | Spec complete |
| **3 — Attestation** | Ed25519-signed audit JSON committed to `.compliance/` | Spec complete |
| **4 — Marketplace** | Verified publisher, pricing plans, one-click install | Planned |

---

## Repository layout

```
audit.py              CLI entrypoint (no Temporal required)
api.py                FastAPI HTTP server (REST bridge for the web UI)
settings.py           Env-only config, bottom of the dependency graph
controls.yaml         Compliance catalog (4 frameworks, 10 controls)
pipeline/             Pure analysis functions
  snapshot.py         Clone + file discovery
  chunk.py            Function-level chunking
  detect.py           Regex suspect detection (5 detectors)
  adjudicate.py       LLM call per suspect
  validate.py         4-check validation gate
  score.py            Deterministic scoring + report rendering
  engine.py           audit_repo() — orchestrates per-file pipeline
workflows/            Temporal wrappers (additive layer)
  audit_workflow.py   ComplianceAuditWorkflow + activities
  worker.py           Temporal worker entrypoint
  start.py            CLI to submit a durable workflow run
notify/               Alerting adapters (outbox / Slack / NudgeBee)
client/               Next.js 16 web dashboard (RegionGuard UI)
fixtures/bad|fixed/   6-defect demo tree and its remediation
tests/smoke.py        9 tests, no GPU, no network
docs/specs/           Architecture, durability, notify, and GitHub specs
bin/temporal          Bundled Temporal dev server binary
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `probe failed: HTTP 401` | Set `LLM_API_KEY` (JarvisLabs always requires one) |
| `probe failed: HTTP 404` | URL must end in `/v1`; model ID must match catalogue exactly |
| `TypeError: Load failed` in browser | Backend not running — `uvicorn api:app --port 8000` |
| Audit stuck on `running` | Check uvicorn terminal for traceback; set `MOCK_LLM=1` to bypass LLM |
| `0 suspects` on a real repo | Add `--include src,api,app` and raise `--max-files` |
| Score says `Mode: mock` | `MOCK_LLM=1` is set or LLM config is incomplete |
| `RestrictedWorkflowAccessError` | A `pipeline.*` import is at module scope in a workflow file — move it inside the activity body |

---

## Status

| ✅ Shipped | 🔲 Planned |
|---|---|
| 5 detectors, 10 controls, 4 frameworks | Supermemory cross-run indexing |
| Two-stage detect → adjudicate pipeline | JS/TS tree-sitter chunker |
| 4-check validation gate (hallucination guard) | GitHub App + SARIF upload |
| Temporal durable workflow, verified under `kill -9` | Auto-fix PRs |
| `continue_as_new` cursor for unlimited repo size | Geo-cell deployment |
| FastAPI REST server + Next.js 16 web UI | Marketplace listing |
| Slack + NudgeBee + outbox alerting | Supermemory trend dashboard |
| 9-test smoke suite (no GPU, no network) | |

---

*Indicative only. Not legal advice.*
