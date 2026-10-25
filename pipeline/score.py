"""Deterministic scoring and reporting. No model calls in this module.

The score exists to answer the question a developer actually asks: "am I going to
get fined?" A count of findings does not answer that. Severity weights, a verdict
band, and the statutory exposure attached to each control do.

Findings are grouped into **defects**. One line of code can breach GDPR Art. 32 and
DPDP S.8(5) at the same time - that is one defect with two obligations, not two
defects. Scoring counts defects, so adding a framework never changes the score.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pipeline.validate import Finding, Rejection

WEIGHTS = {"high": 25, "medium": 12, "low": 5}


@dataclass(frozen=True)
class Defect:
    """One piece of bad code, plus every statute it breaches."""

    defect_id: str
    path: str
    line_start: int
    evidence: str
    severity: str
    findings: tuple[Finding, ...]

    @property
    def frameworks(self) -> list[str]:
        return sorted({f.framework for f in self.findings})

    @property
    def title(self) -> str:
        return self.findings[0].title

    @property
    def fix(self) -> str:
        for finding in self.findings:
            if finding.fix:
                return finding.fix
        return self.findings[0].remediation

    @property
    def obligations(self) -> list[dict]:
        seen: set[tuple[str, str]] = set()
        out: list[dict] = []
        for finding in sorted(self.findings, key=lambda f: f.framework):
            key = (finding.framework, finding.control)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "framework": finding.framework,
                "control": finding.control,
                "control_id": finding.control_id,
                "title": finding.title,
                "exposure": finding.exposure,
            })
        return out


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

    @property
    def defects(self) -> list[Defect]:
        return group_defects(self.findings)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def group_defects(findings: list[Finding]) -> list[Defect]:
    """Collapse obligations that point at the same line into one defect."""
    buckets: dict[tuple[str, int, str], list[Finding]] = {}
    for finding in findings:
        key = (finding.path, finding.line_start, _normalise(finding.evidence))
        buckets.setdefault(key, []).append(finding)

    defects: list[Defect] = []
    for (path, line, _evidence), group in buckets.items():
        severity = max(group, key=lambda f: WEIGHTS.get(f.severity, 5)).severity
        digest = hashlib.sha256(f"{path}|{line}|{_evidence}".encode()).hexdigest()[:16]
        defects.append(
            Defect(
                defect_id=digest,
                path=path,
                line_start=line,
                evidence=group[0].evidence,
                severity=severity,
                findings=tuple(sorted(group, key=lambda f: f.framework)),
            )
        )

    defects.sort(key=lambda d: (WEIGHTS.get(d.severity, 5) * -1, d.path, d.line_start))
    return defects


def score(findings: list[Finding]) -> tuple[int, str]:
    """Weighted by defect, not by obligation. Extra citations on the same line do not move it."""
    penalty = sum(WEIGHTS.get(defect.severity, 5) for defect in group_defects(findings))
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


def dedupe_rejections(rejections: list[Rejection]) -> list[Rejection]:
    """One line can be rejected under several controls. Show it once."""
    merged: dict[tuple[str, int, str], list[str]] = {}
    order: list[tuple[str, int, str]] = []
    for rejection in rejections:
        key = (rejection.path, rejection.line_start, rejection.reason)
        if key not in merged:
            merged[key] = []
            order.append(key)
        if rejection.control_id not in merged[key]:
            merged[key].append(rejection.control_id)

    return [
        Rejection(
            path=key[0], line_start=key[1], reason=key[2],
            control_id=", ".join(merged[key]), evidence="",
        )
        for key in order
    ]


def _obligation_line(obligation: dict) -> str:
    return f"{obligation['framework']} {obligation['control']}"


def render_terminal(report: AuditReport) -> str:
    value, verdict = score(report.findings)
    defects = report.defects
    obligations = sum(len(d.obligations) for d in defects)
    lines = [
        f"\nCOMPLIANCE POSTURE — {report.repo} @ {report.commit[:8]}",
        f"Score: {value}/100   Verdict: {verdict}",
        f"Frameworks: {', '.join(report.frameworks) or 'all'}   Mode: {report.mode}",
        f"{len(defects)} defect{'s' if len(defects) != 1 else ''} · "
        f"{obligations} obligation{'s' if obligations != 1 else ''}",
        "",
    ]
    if not defects:
        lines.append("  No confirmed violations.")
    for defect in defects:
        lines.append(
            f"  {defect.path}:{defect.line_start}   {defect.severity.upper()}"
        )
        lines.append(f"    {defect.title}")
        for obligation in defect.obligations:
            lines.append(
                f"      {_obligation_line(obligation):<24} {obligation['exposure']}"
            )
        lines.append(f"    > {defect.evidence[:100]}")
        lines.append("")

    lines.append(
        f"{report.candidates} suspect{'s' if report.candidates != 1 else ''} → "
        f"{len(report.findings)} confirmed, "
        f"{len(dedupe_rejections(report.rejections))} rejected by adjudication."
    )
    for rejection in dedupe_rejections(report.rejections):
        lines.append(f"  rejected  {rejection.path}:{rejection.line_start} — {rejection.reason}")
    lines.append("\nIndicative only. Not legal advice.\n")
    return "\n".join(lines)


def render_markdown(report: AuditReport) -> str:
    value, verdict = score(report.findings)
    defects = report.defects
    obligations = sum(len(d.obligations) for d in defects)
    badge = {"COMPLIANT": "🟢", "AT RISK": "🟡", "NOT COMPLIANT": "🟠", "CRITICAL EXPOSURE": "🔴"}[verdict]
    out = [
        f"# Compliance audit — `{report.repo}`",
        "",
        f"**{badge} {value}/100 — {verdict}**",
        "",
        "| | |",
        "|---|---|",
        f"| Audit ID | `{report.audit_id}` |",
        f"| Commit | `{report.commit}` |",
        f"| Frameworks | {', '.join(report.frameworks) or 'all'} |",
        f"| Files analysed | {report.files} |",
        f"| Suspects raised | {report.candidates} |",
        f"| Defects confirmed | {len(defects)} |",
        f"| Obligations breached | {obligations} |",
        f"| Rejected | {len(dedupe_rejections(report.rejections))} |",
        f"| Catalog | `{report.catalog_hash}` |",
        f"| Engine | {report.mode} |",
        "",
        "## Confirmed defects",
        "",
    ]

    if not defects:
        out += ["No confirmed violations in the analysed files.", ""]

    for index, defect in enumerate(defects, start=1):
        out += [
            f"### {index}. `{defect.path}:{defect.line_start}` — {defect.severity.upper()}",
            "",
            f"**{defect.title}**",
            "",
            "| Framework | Control | Exposure |",
            "|---|---|---|",
        ]
        for obligation in defect.obligations:
            out.append(
                f"| {obligation['framework']} | {obligation['control']} | {obligation['exposure']} |"
            )
        out += ["", "```", defect.evidence, "```", ""]
        if defect.fix:
            out += ["**Suggested fix**", "", "```diff", f"- {defect.evidence}", f"+ {defect.fix}", "```", ""]
        out += [f"**Why:** {defect.findings[0].rationale}", ""]

    out += ["## Rejected by adjudication", ""]
    if not report.rejections:
        out += ["None.", ""]
    for rejection in dedupe_rejections(report.rejections):
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
        "The score counts **defects**, not obligations: one bad line that breaches both GDPR "
        "and DPDP is one defect with two obligations, so enabling a framework never lowers the "
        "score on its own.",
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
        "defects": [
            {
                "defect_id": defect.defect_id,
                "path": defect.path,
                "line_start": defect.line_start,
                "severity": defect.severity,
                "evidence": defect.evidence,
                "fix": defect.fix,
                "obligations": defect.obligations,
            }
            for defect in report.defects
        ],
        "rejections": [asdict(r) for r in dedupe_rejections(report.rejections)],
    }
