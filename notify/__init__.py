"""Notification gateway adapters.

One interface, three transports, selected by NUDGEBEE_MODE. Findings leave the
cell through exactly one seam, which is what makes the alerting integration
swappable without touching the workflow.
"""

from __future__ import annotations

from notify.base import Notifier, build_notifier

__all__ = ["Notifier", "build_notifier"]
