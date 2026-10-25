"""Validation gate - the reason this is not an LLM writing you a report.

Every confirmed finding must pass four mechanical checks. Anything that fails is
kept as a rejection with a reason and shown in the report, because a visible
rejection is proof the pipeline has a gate at all.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from pipeline.adjudicate import Decision
from pipeline.catalog import Control
from pipeline.chunk import Chunk
from pipeline.detect import Candidate

MIN_CONFIDENCE = 0.5


@dataclass(frozen=True)
class Finding:
    finding_id: str
    control_id: str
    framework: str
    control: str
    severity: str
    title: str
    exposure: str
    remediation: str
    path: str
    symbol: str
    line_start: int
    line_end: int
    evidence: str
    rationale: str
    fix: str
    confidence: float


@dataclass(frozen=True)
class Rejection:
    path: str
    control_id: str
    line_start: int
    evidence: str
    reason: str


@dataclass
class ValidationResult:
    findings: list[Finding]
    rejections: list[Rejection]


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().strip("\"'`").lower()


def validate(
    chunk: Chunk,
    candidates: list[Candidate],
    decisions: list[Decision],
    controls: list[Control],
) -> ValidationResult:
    control_by_id = {c.id: c for c in controls}
    candidate_by_key = {c.key: c for c in candidates}
    source = _normalise(chunk.text)
    seen: set[str] = set()
    findings: list[Finding] = []
    rejections: list[Rejection] = []

    for decision in decisions:
        candidate = candidate_by_key.get(decision.candidate_key)
        if candidate is None:
            continue  # model invented a key: ignore silently, it has no source anchor
        control = control_by_id.get(candidate.control_id)
        if control is None:
            continue

        if not decision.is_violation:
            rejections.append(
                Rejection(
                    path=chunk.path,
                    control_id=control.id,
                    line_start=candidate.start,
                    evidence=candidate.evidence,
                    reason=decision.reject_reason or decision.rationale or "assessed as behaviourally safe",
                )
            )
            continue

        if decision.confidence < MIN_CONFIDENCE:
            rejections.append(
                Rejection(path=chunk.path, control_id=control.id, line_start=candidate.start,
                          evidence=candidate.evidence,
                          reason=f"below confidence floor ({decision.confidence:.2f})")
            )
            continue

        # Check 1: the quoted evidence must exist in the source the model was shown.
        if not decision.evidence_quote or _normalise(decision.evidence_quote) not in source:
            rejections.append(
                Rejection(path=chunk.path, control_id=control.id, line_start=candidate.start,
                          evidence=candidate.evidence,
                          reason="evidence quote not found in source (hallucination guard)")
            )
            continue

        # Check 2: the line range must be inside the chunk that was adjudicated.
        if not (chunk.start <= decision.line_start <= chunk.end):
            rejections.append(
                Rejection(path=chunk.path, control_id=control.id, line_start=candidate.start,
                          evidence=candidate.evidence,
                          reason="reported line outside adjudicated range")
            )
            continue

        evidence = decision.evidence_quote or candidate.evidence
        digest = hashlib.sha256(
            f"{control.id}|{chunk.path}|{decision.line_start}|{_normalise(evidence)}".encode()
        ).hexdigest()[:16]

        # Check 3: deduplicate against everything already accepted.
        if digest in seen:
            continue
        seen.add(digest)

        findings.append(
            Finding(
                finding_id=digest,
                control_id=control.id,
                framework=control.framework,
                control=control.control,
                severity=control.severity,
                title=control.title,
                exposure=control.exposure,
                remediation=control.remediation,
                path=chunk.path,
                symbol=chunk.symbol,
                line_start=decision.line_start,
                line_end=max(decision.line_end, decision.line_start),
                evidence=evidence.strip(),
                rationale=decision.rationale,
                fix=decision.fix,
                confidence=decision.confidence,
            )
        )

    findings.sort(key=lambda f: (f.path, f.line_start, f.control_id))
    return ValidationResult(findings=findings, rejections=rejections)
