# 01 — Architecture

## Layers

```
settings.py          env only, no imports from below
      ↓
pipeline/            pure functions. deterministic core + one model call
      ↓
workflows/           Temporal wrappers (additive)
notify/              delivery adapters (additive)
audit.py             CLI that composes pipeline/ without Temporal
```

Two rules hold the shape:

1. **`pipeline/` imports nothing from `workflows/` or `notify/`.** The analysis core
   runs with Temporal uninstalled. This is what makes durability an option rather
   than a rewrite.
2. **`pipeline/` never holds configuration.** Every entry point takes explicit
   parameters, so an activity can be given different settings without global state.

## Why a two-stage detection model

Single-pass LLM review of a 500k-LOC repo costs roughly 60M input tokens and produces
inconsistent verdicts, because the model is asked to both find *and* judge.

Two stages split those jobs:

| Stage | Job | Mechanism | Failure mode |
|---|---|---|---|
| Detect | recall | regex over functions | noisy, cheap, forgivable |
| Adjudicate | precision | model per candidate | expensive per call, but bounded |

Consequence: token spend is proportional to the number of *suspects*, not the size of
the repository. A repo with no suspects costs zero tokens.

## The validation gate

Every confirmed finding passes four mechanical checks in `pipeline/validate.py`:

1. `confidence >= 0.5`
2. the quoted evidence exists verbatim in the source the model was shown
3. the reported line is inside the adjudicated chunk
4. the finding ID (a content hash) has not already been accepted

Check 2 is the important one. It converts "the model said so" into "the model said so
*and* the text is really there", which is the difference between a report and a
liability. Rejections are rendered in the report rather than dropped — a visible
rejection is evidence the gate exists.

## Scoring

`WEIGHTS = {high: 25, medium: 12, low: 5}`, score floored at 0.

| Score | Verdict |
|---|---|
| 90–100 | COMPLIANT |
| 70–89 | AT RISK |
| 40–69 | NOT COMPLIANT |
| 0–39 | CRITICAL EXPOSURE |

Scoring is deliberately deterministic and model-free, so two runs of the same commit
always produce the same number. A score that moves because the model was sampled
differently is unusable in an audit trail.

## Isolation (target)

A cell is one region containing: Temporal namespace, Postgres, object store,
vector store, model endpoint, and notifier. No component is shared across cells.

```
request → geo-DNS → cell (all components in-region) → attestation
```

Geo-DNS is routing, not isolation. Isolation comes from an egress allowlist scoped to
the cell plus a signed per-audit attestation. "Mathematically provable" is not a
claim worth making; *auditable and enforced* is.

## Deliberate exclusions

- **No vector search over the control catalog.** Five to fifty controls need exact
  IDs, not fuzzy recall.
- **No execution of repository code.** Parsing only. Docker is not a security
  boundary for untrusted code; never running it is.
- **No model call in scoring or validation.** Anything in the audit trail must be
  reproducible.
