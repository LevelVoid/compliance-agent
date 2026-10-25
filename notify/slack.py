"""Slack incoming webhook. The visual beat: a real alert with the file, the
controls, and the patch, in the channel the team already reads."""

from __future__ import annotations

import httpx


class SlackNotifier:
    name = "slack"

    def __init__(self, webhook_url: str, timeout: float = 15.0) -> None:
        self._url = webhook_url
        self._timeout = timeout

    async def send(self, *, repo: str, summary: dict) -> dict:
        defects = summary.get("defects", [])
        head = defects[:5]
        lines = [
            f"*{repo}* — {summary.get('score')}/100 · {summary.get('verdict')}",
            f"{len(defects)} confirmed defect(s)",
            "",
        ]
        for defect in head:
            citations = ", ".join(
                f"{o['framework']} {o['control']}" for o in defect.get("obligations", [])
            )
            lines += [
                f"*{defect['severity'].upper()}* · `{defect['path']}:{defect['line_start']}`",
                f"_{citations}_",
                f"```{defect['evidence']}```",
            ]
            if defect.get("fix"):
                lines.append(f"Proposed: `{defect['fix'][:180]}`")
            lines.append("")

        payload = {
            "text": f"{repo}: {summary.get('verdict')} ({summary.get('score')}/100)",
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}],
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._url, json=payload)
            response.raise_for_status()
        return {"transport": self.name, "delivered": len(head)}
