#!/usr/bin/env python3
"""FastAPI HTTP server that exposes the compliance-audit pipeline.

Endpoints
---------
POST /api/audit          – submit a GitHub repo URL, get back an audit_id
GET  /api/audit/{id}     – poll status; returns full report once complete
GET  /api/audits         – list recent audit results kept in memory
DELETE /api/audit/{id}   – drop an in-progress or completed audit

Run
---
    source .venv/bin/activate
    uvicorn api:app --reload --port 8000

Environment
-----------
All the same vars as the CLI (.env is loaded automatically by settings.py).
Set CORS_ORIGIN to override the default http://localhost:3000.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

from pipeline.engine import LLMConfig, audit_repo
from pipeline.score import as_json
from pipeline.snapshot import clone
from settings import ROOT, get_settings

# ---------------------------------------------------------------------------
# In-memory job store (good enough for a single-worker dev server)
# ---------------------------------------------------------------------------

_jobs: dict[str, dict[str, Any]] = {}


class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load settings once at startup so errors surface early.
    get_settings()
    yield


app = FastAPI(
    title="Compliance Agent API",
    description="Source-code compliance audit against DPDP, SOC2, GDPR, HIPAA, and CERT-In.",
    version="1.0.0",
    lifespan=lifespan,
)

CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "http://localhost:3000")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[CORS_ORIGIN, "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class AuditRequest(BaseModel):
    repo_url: HttpUrl
    frameworks: list[str] = ["DPDP", "SOC2"]
    max_files: int = 12


class AuditJob(BaseModel):
    audit_id: str
    status: Status
    repo: str
    repo_url: str
    frameworks: list[str]
    progress: dict[str, Any] = {}   # values may be int OR str (current_file)
    result: dict[str, Any] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

def _llm() -> LLMConfig:
    settings = get_settings()
    return LLMConfig(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        use_mock=settings.use_mock,
    )


async def _run_audit(audit_id: str, repo_url: str, frameworks: list[str], max_files: int) -> None:
    """Clone repo and run the audit pipeline; updates _jobs in-place."""
    job = _jobs[audit_id]
    job["status"] = Status.RUNNING

    try:
        settings = get_settings()
        llm = _llm()

        # Extract owner/repo from URL for a tidy clone destination
        # e.g. https://github.com/OWASP/NodeGoat  →  NodeGoat
        url = repo_url.rstrip("/")
        repo_name = url.split("/")[-1]
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]

        clone_dest = ROOT / "repo" / repo_name
        job["progress"] = {"cloning": 1}

        # Clone (blocking; run in thread so we don't block the event loop)
        await asyncio.to_thread(
            clone,
            repo_url if repo_url.endswith(".git") else repo_url + ".git",
            clone_dest,
        )

        job["progress"] = {"cloned": 1, "auditing": 0, "total": 0}

        def _progress(i: int, n: int, path: str) -> None:
            job["progress"] = {"cloned": 1, "auditing": i, "total": n, "current_file": path}

        report = await audit_repo(
            clone_dest,
            repo_name=repo_name,
            frameworks=frameworks,
            llm=llm,
            max_files=max_files,
            progress=_progress,
        )

        # Persist JSON report to out/
        settings.out_dir.mkdir(parents=True, exist_ok=True)
        report_json = as_json(report)
        (settings.out_dir / f"report-{audit_id}.json").write_text(
            json.dumps(report_json, indent=2)
        )

        job["status"] = Status.DONE
        job["result"] = report_json

    except Exception as exc:  # noqa: BLE001
        job["status"] = Status.FAILED
        job["error"] = str(exc)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/audit", response_model=AuditJob, status_code=202)
async def start_audit(body: AuditRequest, background_tasks: BackgroundTasks) -> AuditJob:
    """Submit a GitHub repository for compliance auditing.

    Returns immediately with a job record; poll `GET /api/audit/{audit_id}`
    until status is `done` or `failed`.
    """
    audit_id = uuid.uuid4().hex[:12]
    repo_url_str = str(body.repo_url)
    repo_name = repo_url_str.rstrip("/").split("/")[-1].removesuffix(".git")

    _jobs[audit_id] = {
        "audit_id": audit_id,
        "status": Status.PENDING,
        "repo": repo_name,
        "repo_url": repo_url_str,
        "frameworks": body.frameworks,
        "progress": {},
        "result": None,
        "error": None,
    }

    background_tasks.add_task(
        _run_audit, audit_id, repo_url_str, body.frameworks, body.max_files
    )

    return AuditJob(**_jobs[audit_id])


@app.get("/api/audit/{audit_id}", response_model=AuditJob)
async def get_audit(audit_id: str) -> AuditJob:
    """Poll audit status and retrieve the full report once complete."""
    job = _jobs.get(audit_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    return AuditJob(**job)


@app.get("/api/audits", response_model=list[AuditJob])
async def list_audits() -> list[AuditJob]:
    """Return all audits in the current server session (most recent first)."""
    return [AuditJob(**job) for job in reversed(list(_jobs.values()))]


@app.delete("/api/audit/{audit_id}", status_code=204)
async def delete_audit(audit_id: str) -> None:
    """Drop an audit from the in-memory store."""
    if audit_id not in _jobs:
        raise HTTPException(status_code=404, detail="Audit not found")
    del _jobs[audit_id]


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Simple liveness check."""
    settings = get_settings()
    return {
        "status": "ok",
        "llm_configured": str(settings.llm_configured),
        "mock_mode": str(settings.use_mock),
    }
