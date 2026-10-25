"""The pipeline, composed. Plain async functions, no workflow imports.

Everything here is callable outside Temporal, which is what makes the durable
version a wrapper instead of a rewrite. Each step takes plain data and returns
plain data so it can become an activity without modification.
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from pipeline.adjudicate import adjudicate
from pipeline.catalog import load_controls, select
from pipeline.chunk import chunk_file
from pipeline.detect import detect
from pipeline.score import AuditReport, catalog_hash
from pipeline.snapshot import FileRef, discover
from pipeline.validate import validate
from settings import ROOT


@dataclass(frozen=True)
class LLMConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    use_mock: bool = True


def head_commit(root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "0" * 40


async def audit_file(
    root: Path,
    ref: FileRef,
    frameworks: list[str],
    llm: LLMConfig,
) -> tuple[list, list, int]:
    """One file in, (findings, rejections, candidate_count) out.

    This is the unit of work Temporal checkpoints. It is idempotent: the same file
    and commit always produce the same findings, and finding IDs are content
    addressed, so a retry cannot duplicate anything.
    """
    controls = select(load_controls(), frameworks, ref.lang)
    if not controls:
        return [], [], 0

    findings, rejections = [], []
    candidates_total = 0
    for chunk in chunk_file(root, ref.path, ref.lang):
        applicable = select(controls, frameworks, ref.lang)
        candidates = detect(chunk, applicable)
        if not candidates:
            continue
        candidates_total += len(candidates)
        decisions = await asyncio.to_thread(
            adjudicate, chunk, candidates, applicable,
            base_url=llm.base_url, api_key=llm.api_key, model=llm.model,
            use_mock=llm.use_mock,
        )
        result = validate(chunk, candidates, decisions, applicable)
        findings.extend(result.findings)
        rejections.extend(result.rejections)
    return findings, rejections, candidates_total


async def audit_repo(
    root: Path,
    *,
    repo_name: str,
    frameworks: list[str],
    llm: LLMConfig,
    max_files: int = 12,
    delay: float = 0.0,
    progress=None,
) -> AuditReport:
    refs = discover(root, max_files=max_files)
    findings, rejections = [], []
    candidates_total = 0

    for index, ref in enumerate(refs, start=1):
        if progress:
            progress(index, len(refs), ref.path)
        found, rejected, count = await audit_file(root, ref, frameworks, llm)
        findings.extend(found)
        rejections.extend(rejected)
        candidates_total += count
        if delay:
            await asyncio.sleep(delay)

    findings.sort(key=lambda f: (f.path, f.line_start))
    return AuditReport(
        audit_id=uuid.uuid4().hex[:12],
        repo=repo_name,
        commit=head_commit(root),
        mode="mock" if llm.use_mock else llm.model,
        frameworks=frameworks,
        catalog_hash=catalog_hash(ROOT / "controls.yaml"),
        findings=findings,
        rejections=rejections,
        candidates=candidates_total,
        files=len(refs),
    )
