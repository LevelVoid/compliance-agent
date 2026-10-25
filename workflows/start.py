#!/usr/bin/env python3
"""Start (or resume) a durable audit.

    python -m workflows.start --path fixtures/bad --frameworks SOC2,HIPAA,GDPR --slow 2

Workflow ID is derived from repo + commit, so starting the same audit twice
attaches to the running one instead of duplicating work.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import timedelta
from pathlib import Path

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy

from pipeline.engine import head_commit
from settings import ROOT
from workflows.audit_workflow import TASK_QUEUE, AuditState, ComplianceAuditWorkflow

ADDRESS = "localhost:7233"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="fixtures/bad")
    parser.add_argument("--frameworks", default="SOC2,HIPAA,GDPR")
    parser.add_argument("--slow", type=float, default=0.0, help="seconds per file (for demos)")
    parser.add_argument("--max-files", type=int, default=0)
    args = parser.parse_args()

    root = Path(args.path).resolve()
    commit = head_commit(root)
    workflow_id = f"audit:{root.name}:{commit[:8]}"

    client = await Client.connect(ADDRESS)
    handle = await client.start_workflow(
        ComplianceAuditWorkflow.run,
        AuditState(
            audit_id=uuid.uuid4().hex[:12],
            root=str(root),
            repo=root.name,
            frameworks=[f.strip() for f in args.frameworks.split(",") if f.strip()],
            delay=args.slow,
        ),
        id=workflow_id,
        task_queue=TASK_QUEUE,
        execution_timeout=timedelta(hours=6),
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
    )
    print(f"started {workflow_id}", flush=True)
    print(f"ui: http://localhost:8233/namespaces/default/workflows/{workflow_id}", flush=True)

    result = await handle.result()
    print(f"\nworkflow complete — score {result['score']}/100, {result['verdict']}")
    print(f"report: {result['report_md']}")


if __name__ == "__main__":
    asyncio.run(main())
