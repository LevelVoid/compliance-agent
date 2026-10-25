"""The durable audit workflow.

Why this exists: a 400k-file repo takes hours. A worker that dies at file 300k
must not restart from zero. Temporal persists the outcome of every activity in
the event history, so restarting the worker replays that history and re-runs only
the file that was in flight.

Three rules are what make that true rather than aspirational:

1. Workflow state holds file *references*, never file contents. State stays
   O(number of files) while the repo is O(gigabytes).
2. Each activity is idempotent - it writes one artifact per source file, keyed by
   a hash of the path, and never overwrites an existing one. A retry is harmless.
3. `continue_as_new` carries a cursor, not an accumulation. History stays under
   the 51,200-event ceiling on repos of any size.

Activities are the same functions audit.py calls. Temporal is a wrapper here, not
a rewrite.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

# Deliberately no pipeline/ or settings imports at module scope. Temporal runs
# workflow code in a deterministic sandbox that forbids filesystem calls at
# import time, and a workflow that can only start activities is a workflow that
# stays replayable. Everything else is imported inside the activity bodies.

TASK_QUEUE = "compliance-audit"


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


@dataclass
class AuditState:
    """Everything the workflow carries. Serialised between runs."""

    audit_id: str
    root: str
    repo: str
    frameworks: list[str]
    refs: list[dict] = field(default_factory=list)
    cursor: int = 0
    candidates: int = 0
    confirmed: int = 0
    rejected: int = 0
    delay: float = 0.0
    summary: dict = field(default_factory=dict)


def _artifact_dir(audit_id: str) -> Path:
    path = Path(os.environ.get("OUT_DIR", "out")) / "findings" / audit_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _artifact_path(audit_id: str, source_path: str) -> Path:
    digest = hashlib.sha256(source_path.encode()).hexdigest()[:16]
    return _artifact_dir(audit_id) / f"{digest}.json"


def _llm():
    from pipeline.engine import LLMConfig
    from settings import get_settings

    settings = get_settings()
    return LLMConfig(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        use_mock=settings.use_mock,
    )


# --------------------------------------------------------------------------
# Activities
# --------------------------------------------------------------------------


@activity.defn
async def discover_files(state: AuditState) -> list[dict]:
    from pipeline.snapshot import discover

    include = [part.strip() for part in os.environ.get("INCLUDE", "").split(",") if part.strip()]
    refs = discover(
        Path(state.root),
        max_files=int(os.environ.get("MAX_FILES", "12")),
        include=include or None,
    )
    return [asdict(ref) for ref in refs]


@activity.defn
async def evaluate_file(
    audit_id: str,
    root: str,
    frameworks: list[str],
    delay: float,
    ref: dict,
) -> dict:
    """Adjudicate one file and durably record the result.

    Takes primitives rather than the whole workflow state: an activity should
    receive only what it needs, or the history grows with the run.
    Retries are safe because the artifact file is content-keyed and write-once.
    """
    from pipeline.catalog import load_controls, select
    from pipeline.chunk import chunk_file
    from pipeline.detect import detect
    from pipeline.snapshot import FileRef
    from pipeline.validate import Finding, Rejection, validate

    artifact = _artifact_path(audit_id, ref["path"])
    if artifact.exists():
        payload = json.loads(artifact.read_text())
        activity.heartbeat("cached")
        return {"path": ref["path"], "cached": True,
                "confirmed": len(payload["findings"]),
                "rejected": len(payload["rejections"]),
                "candidates": payload["candidates"]}

    stop = asyncio.Event()

    async def beat() -> None:
        while not stop.is_set():
            activity.heartbeat()
            await asyncio.sleep(2)

    beater = asyncio.create_task(beat())
    try:
        file_ref = FileRef(path=ref["path"], lang=ref["lang"], size=ref["size"])
        controls = load_controls()
        llm = _llm()
        findings: list[Finding] = []
        rejections: list[Rejection] = []
        candidates_total = 0

        for chunk in chunk_file(Path(root), file_ref.path, file_ref.lang):
            applicable = select(controls, frameworks, file_ref.lang)
            candidates = detect(chunk, applicable)
            if not candidates:
                continue
            candidates_total += len(candidates)
            from pipeline.adjudicate import adjudicate

            decisions = await asyncio.to_thread(
                adjudicate,
                chunk, candidates, applicable,
                base_url=llm.base_url, api_key=llm.api_key, model=llm.model, use_mock=llm.use_mock,
            )
            result = validate(chunk, candidates, decisions, applicable)
            findings.extend(result.findings)
            rejections.extend(result.rejections)
            if delay:
                await asyncio.sleep(delay)

        artifact.write_text(json.dumps({
            "path": ref["path"],
            "candidates": candidates_total,
            "findings": [asdict(f) for f in findings],
            "rejections": [asdict(r) for r in rejections],
        }, indent=2))
    finally:
        stop.set()
        await beater

    return {"path": ref["path"], "cached": False, "confirmed": len(findings),
            "rejected": len(rejections), "candidates": candidates_total}


@activity.defn
async def finalise(state: AuditState) -> dict:
    """Collapse artifacts into the report. Deterministic, no model calls."""
    from pipeline.engine import head_commit
    from pipeline.score import AuditReport, as_json, catalog_hash, render_markdown, render_terminal
    from settings import ROOT, get_settings

    findings: list[dict] = []
    rejections: list[dict] = []
    candidates = 0
    for artifact in sorted(_artifact_dir(state.audit_id).glob("*.json")):
        payload = json.loads(artifact.read_text())
        findings.extend(payload["findings"])
        rejections.extend(payload["rejections"])
        candidates += payload["candidates"]

    from pipeline.validate import Finding as FindingDC, Rejection as RejectionDC

    report = AuditReport(
        audit_id=state.audit_id,
        repo=state.repo,
        commit=head_commit(Path(state.root)),
        mode="mock" if _llm().use_mock else _llm().model,
        frameworks=state.frameworks,
        catalog_hash=catalog_hash(ROOT / "controls.yaml"),
        findings=[FindingDC(**f) for f in findings],
        rejections=[RejectionDC(**r) for r in rejections],
        candidates=candidates,
        files=len(state.refs),
    )
    report.findings.sort(key=lambda f: (f.path, f.line_start))

    out_dir = get_settings().out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"report-{state.audit_id}.md").write_text(render_markdown(report))
    (out_dir / f"report-{state.audit_id}.json").write_text(json.dumps(as_json(report), indent=2))
    print(render_terminal(report), flush=True)

    payload = as_json(report)
    return {
        "audit_id": payload["audit_id"],
        "score": payload["score"],
        "verdict": payload["verdict"],
        "findings": payload["findings"],
        "defects": payload["defects"],
        "report_md": str(out_dir / f"report-{state.audit_id}.md"),
    }


@activity.defn
async def notify(summary: dict, repo: str) -> dict:
    """Hand findings to the alerting gateway. Adapter chosen by env."""
    from notify import build_notifier

    notifier = build_notifier()
    return await notifier.send(repo=repo, summary=summary)


# --------------------------------------------------------------------------
# Workflow
# --------------------------------------------------------------------------

_PIPELINE = dict(
    start_to_close_timeout=timedelta(minutes=5),
    heartbeat_timeout=timedelta(seconds=8),
    retry_policy=RetryPolicy(maximum_attempts=3),
)


@workflow.defn
class ComplianceAuditWorkflow:
    def __init__(self) -> None:
        self._state: AuditState | None = None
        self._done = 0
        self._total = 0

    @workflow.run
    async def run(self, state: AuditState) -> dict:
        self._state = state

        if not state.refs:
            state.refs = await workflow.execute_activity(
                discover_files, args=[state],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

        self._total = len(state.refs)
        self._done = state.cursor

        while state.cursor < len(state.refs):
            ref = state.refs[state.cursor]
            result = await workflow.execute_activity(
                evaluate_file,
                args=[state.audit_id, state.root, state.frameworks, state.delay, ref],
                **_PIPELINE,
            )
            state.candidates += result["candidates"]
            state.confirmed += result["confirmed"]
            state.rejected += result["rejected"]
            state.cursor += 1
            self._done = state.cursor

            # History ceiling: hand off with a cursor, never with the payload.
            if workflow.info().is_continue_as_new_suggested():
                workflow.logger.info("continuing as new at file %s", state.cursor)
                workflow.continue_as_new(state)

        summary = await workflow.execute_activity(
            finalise, args=[state],
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        state.summary = summary

        await workflow.execute_activity(
            notify, args=[summary, state.repo],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        return summary

    @workflow.query
    def progress(self) -> dict:
        return {
            "done": self._done,
            "total": self._total,
            "confirmed": self._state.confirmed if self._state else 0,
        }
