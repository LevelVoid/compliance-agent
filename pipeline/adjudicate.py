"""LLM adjudication - the precision pass.

One batched call per chunk. Only suspects produced by detect.py are adjudicated,
so token spend is bounded by the number of candidates, not the size of the repo.

The model is asked for structured output and is explicitly allowed to say "not a
violation" - that rejection is a feature of the product, not a failure.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx

from pipeline.catalog import Control
from pipeline.chunk import Chunk
from pipeline.detect import Candidate

SYSTEM_PROMPT = """You are a compliance auditor reviewing source code for a regulated business.

You are given ONE function and a list of SUSPECTS flagged by pattern matching.
For each suspect decide whether it is a genuine compliance violation.

Rules:
- Only rule on the suspects provided. Never invent new findings.
- Be conservative. A pattern match is not a violation. Judge intent and context.
- If the same construct is used in a way that carries no regulatory risk
  (a cache key, a checksum, a non-sensitive identifier), return is_violation false
  and explain why in one clause.
- evidence_quote must be copied character for character from the numbered source.
- If it is a violation, provide a minimal fix: the replacement lines only, no diff
  markers, no commentary.

Return ONLY this JSON object:
{"decisions": [{"candidate_key": "<echo the suspect key>", "is_violation": true,
  "confidence": 0.0-1.0, "evidence_quote": "<exact line>", "line_start": 0,
  "line_end": 0, "rationale": "<one sentence>", "fix": "<replacement lines or empty>",
  "reject_reason": "<why not a violation, when is_violation is false>"}]}"""


@dataclass
class Decision:
    candidate_key: str
    is_violation: bool
    confidence: float
    evidence_quote: str
    line_start: int
    line_end: int
    rationale: str
    fix: str = ""
    reject_reason: str = ""


def build_prompt(chunk: Chunk, candidates: list[Candidate], controls: list[Control]) -> list[dict]:
    catalog = "\n".join(
        f"- {c.id} | {c.framework} {c.control} | {c.severity} | {c.title}"
        for c in controls
    )
    numbered = "\n".join(
        f"{chunk.start + i:4d}| {line}" for i, line in enumerate(chunk.text.splitlines())
    )
    suspects = "\n".join(
        f"- key={c.key} control={c.control_id} hint={c.hint} at line {c.start}"
        for c in candidates
    )
    user = (
        f"FILE: {chunk.path}\nSYMBOL: {chunk.symbol}\n\n"
        f"SELECTED CONTROLS:\n{catalog}\n\n"
        f"SUSPECTS:\n{suspects}\n\n"
        f"SOURCE ({chunk.start}-{chunk.end}):\n{numbered}"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def adjudicate(
    chunk: Chunk,
    candidates: list[Candidate],
    controls: list[Control],
    *,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    use_mock: bool = False,
    timeout: float = 120.0,
) -> list[Decision]:
    if use_mock:
        return [_mock_decision(c, chunk) for c in candidates]

    if not base_url or not model:
        raise RuntimeError("LLM endpoint not configured - set LLM_BASE_URL and LLM_MODEL, or MOCK_LLM=1")

    payload = {
        "model": model,
        "messages": build_prompt(chunk, candidates, controls),
        "temperature": 0,
        "max_tokens": 2048,
    }
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with httpx.Client(timeout=timeout) as client:
        response = client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
        # Handle reasoning models where content might be in 'reasoning' field
        choice = body["choices"][0]
        message = choice["message"]
        content = message.get("content") or message.get("reasoning") or ""

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = json.loads(_extract_json(content))
    return [_decision_from(raw) for raw in parsed.get("decisions", [])]


def probe(base_url: str, api_key: str, model: str, timeout: float = 45.0) -> str:
    """Cheapest possible round-trip. Run this before building anything on top."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": 'Reply with JSON only: {"ok": true}'}],
        "temperature": 0,
        "max_tokens": 128,
    }
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with httpx.Client(timeout=timeout) as client:
        response = client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
    
    # Handle reasoning models that put content in 'reasoning' field
    choice = body.get("choices", [{}])[0]
    message = choice.get("message", {})
    content = message.get("content") or message.get("reasoning") or ""
    
    return json.dumps({"ok": True, "model": body.get("model", model), "finish_reason": choice.get("finish_reason")})


def _extract_json(content: str) -> str:
    match = re.search(r"\{.*\}", content, re.DOTALL)
    return match.group(0) if match else "{}"


def _decision_from(raw: dict) -> Decision:
    return Decision(
        candidate_key=str(raw.get("candidate_key", "")),
        is_violation=bool(raw.get("is_violation")),
        confidence=float(raw.get("confidence") or 0.0),
        evidence_quote=str(raw.get("evidence_quote") or "").strip(),
        line_start=int(raw.get("line_start") or 0),
        line_end=int(raw.get("line_end") or 0),
        rationale=str(raw.get("rationale") or "").strip(),
        fix=str(raw.get("fix") or "").strip(),
        reject_reason=str(raw.get("reject_reason") or "").strip(),
    )


# --- Mock mode -------------------------------------------------------------
# Deterministic, and deliberately capable of *rejecting* a suspect, so the
# precision story is demoable without a GPU. This is a stand-in for the model,
# not a stub that always agrees.

_BENIGN_HASH_CONTEXT = re.compile(
    r"(etag|cache|checksum|digest of|content[-_ ]?hash|filecontents|filename)", re.IGNORECASE
)
_CREDENTIAL_CONTEXT = re.compile(r"(password|passwd|credential|secret|pin)", re.IGNORECASE)


_MOCK_COPY = {
    "secrets-in-logs": (
        "A live session token is written to the application log, so anyone with log "
        "access — including support staff and log shippers — holds a valid credential.",
        'logger.info("issued session token for user %s", user["id"])',
    ),
    "pii-exfil": (
        "Email address and date of birth are transmitted to a third-party analytics "
        "vendor with no consent gate and no processor agreement in scope.",
        "axios.post(ANALYTICS_ENDPOINT, { event: \"signup\", user_id: user.id, plan: user.plan })",
    ),
    "sql-concat": (
        "A query is assembled from an interpolated value, so the caller controls SQL "
        "syntax and can read or destroy arbitrary records.",
        'cursor.execute("SELECT id, balance FROM accounts WHERE email = ?", (email,))',
    ),
}


def _mock_decision(candidate: Candidate, chunk: Chunk) -> Decision:
    text = chunk.text
    if candidate.detector == "weak-crypto":
        if _CREDENTIAL_CONTEXT.search(text) and not _BENIGN_HASH_CONTEXT.search(text):
            return Decision(
                candidate_key=candidate.key, is_violation=True, confidence=0.93,
                evidence_quote=candidate.evidence, line_start=candidate.start,
                line_end=candidate.end,
                rationale="A credential is hashed with a fast, unsalted primitive, so a leaked "
                          "digest is trivially reversible.",
                fix=('const hashed = await argon2.hash(password, { type: argon2.argon2id });'
                     if chunk.lang in {"js", "ts"} else "hashed = argon2.hash(password)"),
            )
        return Decision(
            candidate_key=candidate.key, is_violation=False, confidence=0.9,
            evidence_quote=candidate.evidence, line_start=candidate.start,
            line_end=candidate.end, rationale="",
            reject_reason="Hashes public file contents for an HTTP cache key. Not a credential "
                          "digest, so a broken hash carries no authentication risk.",
        )

    if candidate.detector == "missing-authz":
        if "/admin/" in candidate.evidence:
            return Decision(
                candidate_key=candidate.key, is_violation=True, confidence=0.88,
                evidence_quote=candidate.evidence, line_start=candidate.start,
                line_end=candidate.end,
                rationale="A privileged administrative route is registered with no authorisation "
                          "middleware, so any authenticated or anonymous caller can read it.",
                fix='router.get("/admin/users", requireRole("admin"), async (req, res) => {',
            )
        return Decision(
            candidate_key=candidate.key, is_violation=False, confidence=0.7,
            evidence_quote=candidate.evidence, line_start=candidate.start,
            line_end=candidate.end, rationale="", reject_reason="Route is protected upstream.",
        )

    rationale, fix = _MOCK_COPY.get(
        candidate.detector, (candidate.hint.capitalize() + ".", "")
    )
    return Decision(
        candidate_key=candidate.key, is_violation=True, confidence=0.86,
        evidence_quote=candidate.evidence, line_start=candidate.start, line_end=candidate.end,
        rationale=rationale, fix=fix,
    )
