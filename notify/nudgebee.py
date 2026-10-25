"""Nudge Bee adapter.

Nudge Bee is a CloudOps gateway whose own runbook server is Temporal-based, so the
integration is a plain JSON POST rather than an SDK. The transport is deliberately
dumb: one HTTP call, no state, retry-safe.

Set NUDGEBEE_MODE=webhook and NUDGEBEE_WEBHOOK_URL to enable. When their endpoint
shape differs from the contract below, only this file changes.

One event per **defect**, not per obligation: a single bad line that breaches GDPR
and DPDP must open one ticket carrying two citations, not two tickets.
"""

from __future__ import annotations

import hashlib

import httpx

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


class NudgeBeeNotifier:
    name = "nudgebee"

    def __init__(self, webhook_url: str, timeout: float = 15.0) -> None:
        self._url = webhook_url
        self._timeout = timeout

    def _event(self, repo: str, summary: dict, defect: dict) -> dict:
        key = f"{defect['defect_id']}:{repo}"
        return {
            "event_id": hashlib.sha256(key.encode()).hexdigest()[:24],
            "source": "geo-compliance-agent",
            "type": "compliance.defect",
            "repo": repo,
            "severity": defect["severity"],
            "location": f"{defect['path']}:{defect['line_start']}",
            "evidence": defect["evidence"],
            "suggested_fix": defect.get("fix", ""),
            "obligations": defect.get("obligations", []),
            "audit": {
                "audit_id": summary.get("audit_id"),
                "score": summary.get("score"),
                "verdict": summary.get("verdict"),
                "report": summary.get("report_md"),
            },
            "dedupe_key": key,
        }

    async def send(self, *, repo: str, summary: dict) -> dict:
        defects = sorted(
            summary.get("defects", []),
            key=lambda d: (SEVERITY_ORDER.get(d["severity"], 9), d["path"]),
        )
        payload = {
            "source": "geo-compliance-agent",
            "repo": repo,
            "score": summary.get("score"),
            "verdict": summary.get("verdict"),
            "events": [self._event(repo, summary, defect) for defect in defects],
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._url, json=payload)
            response.raise_for_status()
        return {"transport": self.name, "delivered": len(payload["events"])}
