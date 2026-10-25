"""Slack incoming webhook. The visual beat: a real alert with the file, the
control, and the patch, in the channel the team already reads."""

from __future__ import annotations

import httpx

from pipeline.score import WEIGHTS  # noqa: F401  (kept for weight-aware headers)


class SlackNotifier:
    name = "slack"

    def __init__(self, webhook_url: str, timeout: float = 15.0) -> None:
        self._url = webhook_url
        self._timeout = timeout

    async def send(self, *, repo: str, summary: dict) -> dict:
        findings = summary.get("findings", [])
        head = findings[:5]
        lines = [
            f"*{repo}* — {summary.get('score')}/100 · {summary.get('verdict')}",
            f"{len(findings)} confirmed violation(s)",
            "",
        ]
        for finding in head:
            lines += [
                f"*{finding['framework']} {finding['control']}* · {finding['severity'].upper()}"
                f" · `{finding['path']}:{finding['line_start']}`",
                f"_{finding['title']}_",
                f"```{finding['evidence']}```",
            ]
            if finding.get("fix"):
                lines.append(f"Proposed: `{finding['fix'][:180]}`")
            lines.append("")

        payload = {
            "text": f"{repo}: {summary.get('verdict')} ({summary.get('score')}/100)",
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}],
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._url, json=payload)
            response.raise_for_status()
        return {"transport": self.name, "delivered": len(head)}
