"""The seam. Every transport implements one method."""

from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    name: str

    async def send(self, *, repo: str, summary: dict) -> dict:
        """Deliver an audit summary. Must be safe to retry."""
        ...


def build_notifier() -> Notifier:
    from notify.nudgebee import NudgeBeeNotifier
    from notify.outbox import OutboxNotifier
    from notify.slack import SlackNotifier
    from settings import get_settings

    settings = get_settings()
    mode = settings.nudgebee_mode

    if mode == "slack" and settings.slack_webhook_url:
        return SlackNotifier(settings.slack_webhook_url)
    if mode == "webhook" and settings.nudgebee_webhook_url:
        return NudgeBeeNotifier(settings.nudgebee_webhook_url)
    return OutboxNotifier(settings.out_dir)
