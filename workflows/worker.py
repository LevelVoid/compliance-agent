#!/usr/bin/env python3
"""Temporal worker. Kill this process mid-audit; that is the point.

    python -m workflows.worker
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from workflows.audit_workflow import (
    TASK_QUEUE,
    ComplianceAuditWorkflow,
    discover_files,
    evaluate_file,
    finalise,
    notify,
)

ADDRESS = "localhost:7233"


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    client = await Client.connect(ADDRESS)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ComplianceAuditWorkflow],
        activities=[discover_files, evaluate_file, finalise, notify],
    )
    print(f"worker polling {TASK_QUEUE} on {ADDRESS}  (ctrl-c to stop)", flush=True)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
