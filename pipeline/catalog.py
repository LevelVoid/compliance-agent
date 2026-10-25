"""Control catalog loader.

The catalog is reference data, keyed by (framework, id) and indexed by detector.
It is read once per process and filtered - never embedded, never vectorised.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from settings import ROOT


@dataclass(frozen=True)
class Control:
    id: str
    framework: str
    control: str
    title: str
    severity: str
    detector: str
    exposure: str
    remediation: str
    languages: tuple[str, ...] = ()


def load_controls(path: Path | None = None) -> list[Control]:
    raw = yaml.safe_load((path or ROOT / "controls.yaml").read_text()) or []
    return [
        Control(
            id=item["id"],
            framework=item["framework"],
            control=str(item["control"]),
            title=item["title"],
            severity=item["severity"],
            detector=item["detector"],
            exposure=item.get("exposure", ""),
            remediation=item.get("remediation", ""),
            languages=tuple(item.get("languages", ())),
        )
        for item in raw
    ]


LANG_FAMILY = {"ts": "js", "tsx": "js", "jsx": "js", "mjs": "js"}


def select(controls: list[Control], frameworks: list[str], lang: str) -> list[Control]:
    """Framework selection scopes *evaluation*, never storage.

    An empty framework list means "everything applicable to this language".
    """
    wanted = {f.lower() for f in frameworks}
    family = LANG_FAMILY.get(lang, lang)
    return [
        control
        for control in controls
        if (not wanted or control.framework.lower() in wanted)
        and (not control.languages or family in control.languages)
    ]
