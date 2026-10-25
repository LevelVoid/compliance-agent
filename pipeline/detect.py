"""Candidate generation - the cheap, deterministic first pass.

This is the reason the LLM bill is small. Regex finds *suspects* at zero token
cost; the model only adjudicates suspects. Recall comes from here, precision
comes from the model, and `validate.py` throws away anything the model made up.

Every detector is intentionally noisy. Being wrong here is free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pipeline.catalog import Control
from pipeline.chunk import Chunk


@dataclass(frozen=True)
class Candidate:
    control_id: str
    framework: str
    control: str
    detector: str
    path: str
    symbol: str
    start: int
    end: int
    hint: str
    evidence: str

    @property
    def key(self) -> str:
        return f"{self.control_id}|{self.path}|{self.start}"


LOG_CALL = re.compile(r"(console\.(?:log|info|warn|error|debug)|logger\.\w+|print)\s*\(")
SENSITIVE = re.compile(
    r"(token|password|passwd|secret|session|api[_-]?key|creditcard|credit_card|ssn|cookie)",
    re.IGNORECASE,
)
# A sensitive *identifier* must reach the log, not merely appear in the message
# text. `logger.info("token issue failed", user_id)` is safe and must not fire.
INTERPOLATED_SENSITIVE = re.compile(
    r"(\{\s*[^}]*\b" + SENSITIVE.pattern + r"\b[^}]*\}"      # f-string / template literal
    r"|\+\s*[\w\.\[\]'\"]*\b" + SENSITIVE.pattern + r"\b"    # string concatenation
    r"|%[sd][^\"']*[\"']\s*,\s*[\w\.\[\]'\"]*\b" + SENSITIVE.pattern + r"\b)",  # %-style args
    re.IGNORECASE,
)

SQL_VERB = re.compile(r"\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b", re.IGNORECASE)
CONCAT = re.compile(r"(\$\{|\+\s*['\"\w]|%s\s*%|\.format\(|\{[a-z_]\w*\})", re.IGNORECASE)
NOSQL_WHERE = re.compile(r"\$where")

ROUTE = re.compile(
    r"""(?:app|router|server)\.(?:get|post|put|patch|delete)\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)
PRIVILEGED_PATH = re.compile(r"(admin|internal|manage|root|superuser)", re.IGNORECASE)
AUTH_HINT = re.compile(
    r"(auth|permission|role|isAdmin|canAccess|ensureLogged|requireUser|policy|acl)",
    re.IGNORECASE,
)

WEAK_HASH = re.compile(r"(\bmd5\b|\bsha1\b|createHash\(\s*['\"](?:md5|sha1)['\"])", re.IGNORECASE)
SENSITIVE_SUBJECT = re.compile(r"(password|passwd|credential|secret|pin)", re.IGNORECASE)

OUTBOUND = re.compile(r"(axios\.\w+|fetch|request\.\w+|https?\.request|httpx\.\w+)\s*\(", re.IGNORECASE)
PII = re.compile(r"(email|e_mail|ssn|social_security|dob|date_of_birth|phone|address)", re.IGNORECASE)


def detect(chunk: Chunk, controls: list[Control]) -> list[Candidate]:
    """Run every applicable detector over one chunk.

    A detector maps to *many* controls - the same log statement breaches GDPR
    Art. 32 and DPDP S.8(5) at once. One candidate is emitted per (control, line),
    so each obligation is adjudicated and rejected independently.
    """
    by_detector: dict[str, list[Control]] = {}
    for control in controls:
        by_detector.setdefault(control.detector, []).append(control)
    lines = chunk.text.splitlines()
    found: list[Candidate] = []

    def add(detector: str, idx: int, hint: str, span: int = 1) -> None:
        start = chunk.start + idx
        end = min(start + span - 1, chunk.end)
        for control in by_detector.get(detector, []):
            found.append(
                Candidate(
                    control_id=control.id,
                    framework=control.framework,
                    control=control.control,
                    detector=control.detector,
                    path=chunk.path,
                    symbol=chunk.symbol,
                    start=start,
                    end=end,
                    hint=hint,
                    evidence=lines[idx].strip()[:300],
                )
            )

    for idx, line in enumerate(lines):
        window = "\n".join(lines[idx: idx + 3])

        # Single line on purpose: a 3-line window spills into the following
        # statement and produces phantom hits on safe code.
        if "secrets-in-logs" in by_detector and LOG_CALL.search(line) and INTERPOLATED_SENSITIVE.search(line):
            add("secrets-in-logs", idx, "credential or personal data appears inside a log statement", 1)

        if "sql-concat" in by_detector and (NOSQL_WHERE.search(line) or (SQL_VERB.search(line) and CONCAT.search(line))):
            add("sql-concat", idx, "query text is assembled from runtime values", 3)

        route = ROUTE.search(line)
        if "missing-authz" in by_detector and route and PRIVILEGED_PATH.search(route.group(1)):
            body = "\n".join(lines[idx: idx + 15])
            if not AUTH_HINT.search(body):
                add("missing-authz", idx, "privileged route declared without an authorisation check", 15)

        if "weak-crypto" in by_detector and WEAK_HASH.search(line):
            add("weak-crypto", idx, "weak hash algorithm in use - check what is being hashed", 3)

        if "pii-exfil" in by_detector and OUTBOUND.search(line) and PII.search("\n".join(lines[idx: idx + 6])):
            add("pii-exfil", idx, "outbound request carries personal data", 6)

    return found
