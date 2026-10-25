# Geo-Isolated Compliance Audit Agent

Compliance review for source code, judged by a locally hosted model instead of a
rules engine. Answers the question a developer actually asks — *"is this going to
cost me a fine?"* — with the statute, the severity, and a patched line.

Supports **DPDP (India)**, **GDPR**, **SOC2** and **HIPAA** out of the box.

```bash
python audit.py run --path fixtures/bad      # 0/100  · 6 defects · 12 obligations
python audit.py run --path fixtures/fixed    # 100/100 · COMPLIANT
```

---

## Quickstart

```bash
# 1. deps
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. config (gitignored - never paste keys into a shell you share)
cp .env.example .env && $EDITOR .env

# 3. verify the model endpoint before anything depends on it
.venv/bin/python audit.py probe
```

`probe` should print `endpoint replied: {"ok": true}`. If it doesn't, fix that first —
everything downstream assumes a working endpoint. See
[Troubleshooting](#troubleshooting) for the exact error messages.

**No endpoint yet?** Everything runs against a deterministic stand-in:

```bash
MOCK_LLM=1 .venv/bin/python audit.py run --path fixtures/bad
```

MOCK_LLM=1 is not a stub that always agrees — it rejects the decoy in `fixtures/bad`
too, so the pipeline's precision story is demoable offline.

---

## Run it

### Against the bundled fixtures

```bash
# vulnerable tree
MOCK_LLM=1 .venv/bin/python audit.py run --path fixtures/bad --frameworks DPDP,SOC2

# the same tree, remediated
MOCK_LLM=1 .venv/bin/python audit.py run --path fixtures/fixed --frameworks DPDP,SOC2
```

Reports land in `out/report-<commit>.md` and `.json`, and the terminal prints the
scorecard.

### Against your own or a client's code

Point `--path` at any local directory. It does **not** need to be a git repository.

```bash
.venv/bin/python audit.py run \
  --path ~/work/client-acme/backend \
  --frameworks DPDP,SOC2 \
  --include src,api \
  --max-files 40 \
  --name acme-backend
```

| Flag | Why you'd use it |
|---|---|
| `--path` | any local directory; `~` is expanded |
| `--include` | only paths containing these substrings. **Use this on a client repo** — auditing 40 files beats skimming 4,000 |
| `--max-files` | hard cap. Default 12. Raise deliberately, not casually |
| `--name` | display name in the report (defaults to the folder name) |
| `--frameworks` | `DPDP,SOC2,GDPR,HIPAA`. Default `DPDP,SOC2` |

**Cost and time scale with files that contain suspects, not with repository size.**
A file with no regex hits costs zero tokens. Rough guide on a hosted model:

| Files | Wall clock | Notes |
|---|---|---|
| 12 | 1–3 min | default; good first pass |
| 40 | 5–10 min | `--include` on the directory that matters |
| 200+ | 30 min+ | use the Temporal path so a crash doesn't restart it |

For a client audit, run `--include src --max-files 12` first, read the report, then
widen. Never open with the whole tree.

### Against a public GitHub repository

```bash
.venv/bin/python audit.py run --clone OWASP/NodeGoat --frameworks DPDP,SOC2 --max-files 8
```

Shallow clone (`--depth 1`), so it's cheap. Verified output: two real NoSQL-injection
defects at `app/data/allocations-dao.js:73` and `:78`.

---

## Configure the model

Any **OpenAI-compatible** `/v1/chat/completions` endpoint works.

```ini
LLM_BASE_URL=https://your-endpoint/v1
LLM_API_KEY=...
LLM_MODEL=your-model-id
```

### JarvisLabs specifically

JarvisLabs gives you a base URL and a model id, **and an API key** — their Token
Factory is authenticated with `JARVISLABS_API_KEY`, so an endpoint alone is not
enough. Set it as `LLM_API_KEY`.

- **Token Factory** (hosted models): needs a key. `probe` returns 401 without one.
- **Self-deployed vLLM / serverless endpoint**: auth depends on how you launched it.
  If no key is required, leave `LLM_API_KEY` empty — the client omits the
  `Authorization` header entirely rather than sending a blank one.

Requirements the client assumes:

| Requirement | Fallback if missing |
|---|---|
| `response_format: {"type":"json_object"}` | set `MOCK_LLM=1`, or expect parse retries |
| model id must match the catalogue exactly | 404 from `probe` |
| base URL should end in `/v1` | 404, or a redirect that drops the body |

---

## Reading the output

```
COMPLIANCE POSTURE — acme-backend @ be8b45a4
Score: 0/100   Verdict: CRITICAL EXPOSURE
Frameworks: SOC2, HIPAA, GDPR, DPDP   Mode: mock
6 defects · 12 obligations

  src/auth.py:9   HIGH
    Reasonable security safeguards - personal data must not be exposed in logs
      DPDP S.8(5)              Up to INR 250 crore (Schedule, breach of security safeguards)
      GDPR Art. 32             Up to EUR 20M or 4% of global annual turnover (Art. 83(5))
    > logger.info(f"issued session token={token} for user {user['id']}")
```

**Defects vs obligations.** One line of code can breach DPDP and GDPR at once. That
is **one defect** with **two obligations** — one thing to fix, two laws broken.
Scoring counts defects, so a second framework never inflates the penalty on its own.
(The score can still move if that framework selects a detector no other framework
did — that's a genuinely new defect, not a double count.)

| Score | Verdict |
|---|---|
| 90–100 | COMPLIANT |
| 70–89 | AT RISK |
| 40–69 | NOT COMPLIANT |
| 0–39 | CRITICAL EXPOSURE |

Weights: high 25, medium 12, low 5, floored at 0. Deterministic — same commit, same
score, no model sampling involved.

Beneath every run:

```
14 suspects → 12 confirmed, 1 rejected by adjudication
  rejected  src/cache.ts:6 — Hashes public file contents for an HTTP cache key.
            Not a credential digest, so a broken hash carries no authentication risk.
```

That rejection is the product. A rules scanner fails that file.

---

## Frameworks

| Framework | Controls | Statutory exposure carried in the report |
|---|---|---|
| **DPDP** (India, 2023) | S.6, S.8(5) | up to INR 250 crore (security safeguards), INR 50 crore (residual) |
| **GDPR** | Art. 6, Art. 32 | up to EUR 20M or 4% of global annual turnover |
| **SOC2** | CC6.1, CC6.6 | no statutory fine; contractual, insurance, audit-failure exposure |
| **HIPAA** | 164.312(a)(2)(iv) | tiered OCR civil penalties, inflation-adjusted annually |

All of it lives in `controls.yaml` — one YAML file, keyed by `(framework, id)`.
Adding a framework is a YAML edit. Adding a *kind* of defect is one detector in
`pipeline/detect.py`.

Framework selection scopes **evaluation**, never storage: it filters which controls
reach the prompt, so switching frameworks never forces a re-index.

---

## Durability (Temporal)

A large repository takes hours. If the worker dies at file 300,000 it must not
restart at file 1.

```bash
./bin/temporal server start-dev            # UI on :8233   (make temporal)
.venv/bin/python -m workflows.worker       # terminal 2
.venv/bin/python -m workflows.start --path fixtures/bad --frameworks DPDP,SOC2 --slow 2
```

Verified: `kill -9` the worker mid-run, restart it, and the workflow resumes at the
next file — 2 artifacts before the kill, 6 after, files 1–2 never recomputed.

Why it holds: workflow state carries file *references* (never contents), each
activity writes a content-keyed write-once artifact so retries are free, and
`continue_as_new` carries a cursor to stay under the 51,200-event history ceiling.

Details in [`docs/specs/02-durable-workflow.md`](docs/specs/02-durable-workflow.md).

### Docker

```bash
docker compose up --build        # temporal + worker, UI on :8233
docker compose run --rm worker python audit.py run --path fixtures/bad
```

---

## Layout

```
audit.py              CLI (no Temporal required)
settings.py           env-only config
pipeline/             pure functions — no workflow, no notifier imports
workflows/            Temporal wrappers over pipeline/
notify/               Notifier protocol: outbox | slack | nudgebee
controls.yaml         the catalog (4 frameworks, 10 controls)
fixtures/bad|fixed/   the 6-defect demo tree and its remediation
tests/smoke.py        9 tests, no GPU, no network
docs/specs/           architecture, durability, notification + GitHub contracts
```

The import rule that keeps this integrable: **`pipeline/` never imports
`workflows/` or `notify/`.** Temporal and the alerting gateway are additive layers,
not dependencies of the analysis.

### Alerting

`NUDGEBEE_MODE=outbox` (default, cannot fail) · `slack` · `webhook`.
One event per defect, carrying every obligation, deduped by `defect_id` so a retry
cannot open duplicate tickets. Contract in
[`docs/specs/03-nudgebee-contract.md`](docs/specs/03-nudgebee-contract.md).

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `probe failed: HTTP 401` | endpoint needs a key | set `LLM_API_KEY` (JarvisLabs Token Factory always does) |
| `probe failed: HTTP 404` | wrong base URL or model id | URL usually ends `/v1`; model id must match the catalogue exactly |
| `probe failed: HTTP 400` | model rejected the request | likely no `json_object` support, or wrong model id |
| `LLM endpoint not configured` | `.env` not found or empty | `cp .env.example .env`, run from the repo root |
| `Failed validating workflow` | a `pipeline.*` import at module scope in a workflow file | imports go inside activity bodies (sandbox forbids import-time I/O) |
| `0 suspects` on a real repo | file cap picked the wrong files | `--include src,api,app` and raise `--max-files` |
| Report says `Mode: mock` | `MOCK_LLM=1`, or the endpoint is unconfigured | check `.env`; `use_mock` is true whenever config is incomplete |
| Score is 0 | four+ high-severity defects | intentional: 25 each, floored |

Run the suite before blaming the model:

```bash
.venv/bin/python tests/smoke.py     # 9 tests, ~10s, no network
```

---

## Status

| Real | Stubbed |
|---|---|
| 5 detectors, 10 controls across 4 frameworks | JS/TS chunker is a brace counter (tree-sitter next) |
| Defect/obligation grouping and scoring | Supermemory indexing (not wired) |
| Temporal durability, verified under `kill -9` | GitHub App (Action/SARIF first — see spec 04) |
| Evidence-anchored validation, 9-test suite | Next.js dashboard, geo cells |
| Slack + webhook + outbox delivery | auto-fix PRs |

Indicative only. Not legal advice.
