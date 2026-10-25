"""Append-only local sink. The default, because a demo must never depend on a
third party being reachable."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class OutboxNotifier:
    name = "outbox"

    def __init__(self, out_dir: Path) -> None:
        self._path = Path(out_dir) / "notifications.jsonl"

    async def send(self, *, repo: str, summary: dict) -> dict:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "repo": repo,
            "score": summary.get("score"),
            "verdict": summary.get("verdict"),
            "findings": summary.get("findings", []),
        }
        with self._path.open("a") as handle:
            handle.write(json.dumps(record) + "\n")
        return {"transport": self.name, "delivered": len(record["findings"]),
                "path": str(self._path)}
