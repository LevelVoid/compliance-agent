#!/usr/bin/env python3
"""Regression tests. No network, no GPU, no Temporal server.

Runs the real pipeline against the fixtures and asserts on behaviour that matters:
the decoy is rejected, the remediated tree scores 100, and the validation gate
actually drops fabricated evidence.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.adjudicate import Decision
from pipeline.catalog import load_controls, select
from pipeline.chunk import chunk_file
from pipeline.engine import LLMConfig, audit_repo
from pipeline.score import score
from pipeline.snapshot import discover
from pipeline.validate import validate

ROOT = Path(__file__).resolve().parents[1]
MOCK = LLMConfig(use_mock=True)
FRAMEWORKS = ["SOC2", "HIPAA", "GDPR"]


def test_discovery_is_bounded() -> None:
    refs = discover(ROOT / "fixtures" / "bad", max_files=3)
    assert len(refs) == 3, refs
    assert all(ref.lang in {"py", "js", "ts"} for ref in refs)


def test_chunker_finds_python_functions() -> None:
    chunks = chunk_file(ROOT / "fixtures" / "bad", "src/auth.py", "py")
    symbols = {chunk.symbol for chunk in chunks}
    assert {"issue_session", "revoke_session"} <= symbols, symbols


def test_decoy_is_rejected() -> None:
    report = asyncio.run(
        audit_repo(ROOT / "fixtures" / "bad", repo_name="bad",
                   frameworks=FRAMEWORKS, llm=MOCK)
    )
    rejected_paths = {r.path for r in report.rejections}
    assert "src/cache.ts" in rejected_paths, rejected_paths
    assert all(f.path != "src/cache.ts" for f in report.findings)

    confirmed = {f.path for f in report.findings}
    assert confirmed == {
        "src/analytics.ts", "src/auth.py", "src/crypto.ts",
        "src/db.py", "src/routes/admin.ts",
    }, confirmed

    value, verdict = score(report.findings)
    assert value == 0 and verdict == "CRITICAL EXPOSURE", (value, verdict)


def test_remediated_tree_is_clean() -> None:
    report = asyncio.run(
        audit_repo(ROOT / "fixtures" / "fixed", repo_name="fixed",
                   frameworks=FRAMEWORKS, llm=MOCK)
    )
    value, verdict = score(report.findings)
    assert report.findings == [], [f.control_id for f in report.findings]
    assert value == 100 and verdict == "COMPLIANT", (value, verdict)


def test_hallucinated_evidence_is_dropped() -> None:
    """The model claims a violation but quotes text that is not in the source."""
    controls = select(load_controls(), FRAMEWORKS, "py")
    chunk = chunk_file(ROOT / "fixtures" / "fixed", "src/auth.py", "py")[0]
    candidates = [type("C", (), {
        "key": "k1", "control_id": controls[0].id, "path": chunk.path,
        "start": chunk.start, "end": chunk.end, "evidence": "x",
    })()]
    decision = Decision(
        candidate_key="k1", is_violation=True, confidence=0.99,
        evidence_quote="logger.info(f'token={token}')",  # not in the fixed tree
        line_start=chunk.start, line_end=chunk.start,
        rationale="invented", fix="",
    )
    result = validate(chunk, candidates, [decision], controls)
    assert result.findings == []
    assert "hallucination" in result.rejections[0].reason


def test_framework_selection_scopes_evaluation() -> None:
    controls = load_controls()
    soc2 = select(controls, ["SOC2"], "js")
    assert soc2 and all(control.framework == "SOC2" for control in soc2)
    assert select(controls, ["SOC2"], "js") == select(controls, ["SOC2"], "ts")
    dpdp = select(controls, ["DPDP"], "py")
    assert dpdp and all(control.framework == "DPDP" for control in dpdp)
    ids = {c.id for c in load_controls()}
    assert "DPDP-S8(5)-secrets-in-logs" in ids


def test_one_defect_carries_every_obligation() -> None:
    """A single bad line breaches GDPR and DPDP at once: one defect, two citations."""
    report = asyncio.run(
        audit_repo(ROOT / "fixtures" / "bad", repo_name="bad",
                   frameworks=["GDPR", "DPDP"], llm=MOCK)
    )
    log_defects = [d for d in report.defects if d.path == "src/auth.py"]
    assert len(log_defects) == 1, log_defects
    assert log_defects[0].frameworks == ["DPDP", "GDPR"], log_defects[0].frameworks


def test_obligations_do_not_double_count() -> None:
    """One defective line cited by GDPR and DPDP penalises the score once."""
    from pipeline.score import WEIGHTS, group_defects

    report = asyncio.run(
        audit_repo(ROOT / "fixtures" / "bad", repo_name="bad",
                   frameworks=["SOC2", "GDPR", "HIPAA", "DPDP"], llm=MOCK)
    )
    defects = group_defects(report.findings)
    expected = max(0, 100 - sum(WEIGHTS[d.severity] for d in defects))
    assert score(report.findings)[0] == expected
    # More obligations than defects is the proof the grouping works.
    assert len(report.findings) > len(defects), (len(report.findings), len(defects))


def test_client_path_and_include_filter() -> None:
    """Auditing an arbitrary local directory with a path filter (client code)."""
    report = asyncio.run(
        audit_repo(ROOT / "fixtures" / "bad", repo_name="client-repo",
                   frameworks=["DPDP"], llm=MOCK, include=["routes"])
    )
    paths = {finding.path for finding in report.findings}
    assert paths == {"src/routes/admin.ts"}, paths
    assert report.repo == "client-repo"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  pass  {name}")
            except AssertionError as error:
                failures += 1
                print(f"  FAIL  {name}: {error}")
    print(f"\n{'all tests passed' if not failures else f'{failures} failed'}")
    sys.exit(1 if failures else 0)
