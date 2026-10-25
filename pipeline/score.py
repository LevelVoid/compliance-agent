"""Deterministic scoring and reporting. No model calls in this module.

The score exists to answer the question a developer actually asks: "am I going to
get fined?" A count of findings does not answer that. Severity weights, a verdict
band, and the statutory exposure attached to each control do.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pipeline.validate import Finding, Rejection

WEIGHTS = {"high": 25, "medium": 12, "low": 5}


@dataclass
class AuditReport:
    audit_id: str
    repo: str
    commit: str
    mode: str
    frameworks: list[str]
    catalog_hash: str
    findings: list[Finding] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    candidates: int = 0
    files: int = 0


def score(findings: list[Finding]) -> tuple[int, str]:
    penalty = sum(WEIGHTS.get(f.severity, 5) for f in findings)
    value = max(0, 100 - penalty)
    if value >= 90:
        verdict = "COMPLIANT"
    elif value >= 70:
        verdict = "AT RISK"
    elif value >= 40:
        verdict = "NOT COMPLIANT"
    else:
        verdict = "CRITICAL EXPOSURE"
    return value, verdict


def catalog_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def render_terminal(report: AuditReport) -> str:
    value, verdict = score(report.findings)
    lines = [
        f"\nCOMPLIANCE POSTURE — {report.repo} @ {report.commit[:8]}",
        f"Score: {value}/100   Verdict: {verdict}",
        f"Frameworks: {', '.join(report.frameworks) or 'all'}   Mode: {report.mode}",
        "",
    ]
    if not report.findings:
        lines.append("  No confirmed violations.")
    for finding in report.findings:
        lines += [
            f"  {finding.framework:<6} {finding.control:<16} {finding.severity.upper():<6} "
            f"{finding.path}:{finding.line_start}",
            f"    {finding.title}",
            f"    → {finding.exposure}",
            "",
        ]
    lines.append(
        f"{report.candidates} suspects → {len(report.findings)} confirmed, "
        f"{len(report.rejections)} rejected by adjudication."
    )
    for rejection in report.rejections:
        lines.append(f"  rejected  {rejection.path}:{rejection.line_start} — {rejection.reason}")
    lines.append("\nIndicative only. Not legal advice.\n")
    return "\n".join(lines)


def render_markdown(report: AuditReport) -> str:
    value, verdict = score(report.findings)
    badge = {"COMPLIANT": "🟢", "AT RISK": "🟡", "NOT COMPLIANT": "🟠", "CRITICAL EXPOSURE": "🔴"}[verdict]
    out = [
        f"# Compliance audit — `{report.repo}`",
        "",
        f"**{badge} {value}/100 — {verdict}**",
        "",
        f"| | |",
        f"|---|---|",
        f"| Audit ID | `{report.audit_id}` |",
        f"| Commit | `{report.commit}` |",
        f"| Frameworks | {', '.join(report.frameworks) or 'all'} |",
        f"| Files analysed | {report.files} |",
        f"| Suspects raised | {report.candidates} |",
        f"| Confirmed | {len(report.findings)} |",
        f"| Rejected | {len(report.rejections)} |",
        f"| Catalog | `{report.catalog_hash}` |",
        f"| Engine | {report.mode} |",
        "",
        "## Confirmed violations",
        "",
    ]

    if not report.findings:
        out += ["No confirmed violations in the analysed files.", ""]
    for finding in report.findings:
        out += [
            f"### {finding.framework} {finding.control} — {finding.severity.upper()}",
            "",
            f"**{finding.title}**",
            "",
            f"- Location: `{finding.path}:{finding.line_start}` (function `{finding.symbol}`)",
            f"- Confidence: {finding.confidence:.2f}",
            f"- Why: {finding.rationale}",
            f"- Exposure: {finding.exposure}",
            "",
            "```",
            finding.evidence,
            "```",
            "",
        ]
        if finding.fix:
            out += ["**Suggested fix**", "", "```diff", f"- {finding.evidence}", f"+ {finding.fix}", "```", ""]
        else:
            out += [f"**Suggested fix:** {finding.remediation}", ""]

    out += ["## Rejected by adjudication", ""]
    if not report.rejections:
        out += ["None.", ""]
    for rejection in report.rejections:
        out += [
            f"- `{rejection.path}:{rejection.line_start}` ({rejection.control_id}) — {rejection.reason}",
        ]

    out += [
        "",
        "## How to read this",
        "",
        "Pattern matching raises suspects at zero token cost. Each suspect is adjudicated "
        "individually against the selected controls, then mechanically validated: the quoted "
        "evidence must exist verbatim in the source and the line range must be inside the "
        "adjudicated chunk. Anything failing that gate is listed under *Rejected* rather than "
        "silently dropped.",
        "",
        "Indicative only. Not legal advice.",
        "",
    ]
    return "\n".join(out)


def as_json(report: AuditReport) -> dict:
    value, verdict = score(report.findings)
    return {
        "audit_id": report.audit_id,
        "repo": report.repo,
        "commit": report.commit,
        "mode": report.mode,
        "frameworks": report.frameworks,
        "catalog_hash": report.catalog_hash,
        "score": value,
        "verdict": verdict,
        "files": report.files,
        "candidates": report.candidates,
        "findings": [asdict(f) for f in report.findings],
        "rejections": [asdict(r) for r in report.rejections],
    }
