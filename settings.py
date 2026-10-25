"""Single source of configuration. Environment variables only, loaded from .env.

Nothing here imports the pipeline, the workflow layer, or the notifiers. This is
the bottom of the dependency graph so every layer can read it safely.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader so we don't take a dependency for six variables."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    mock_llm: bool

    nudgebee_mode: str
    slack_webhook_url: str
    nudgebee_webhook_url: str

    max_files: int
    repo_dir: Path
    out_dir: Path

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)

    @property
    def use_mock(self) -> bool:
        return self.mock_llm or not self.llm_configured


def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        llm_base_url=os.environ.get("LLM_BASE_URL", "").rstrip("/"),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        llm_model=os.environ.get("LLM_MODEL", ""),
        mock_llm=_bool("MOCK_LLM", False),
        nudgebee_mode=os.environ.get("NUDGEBEE_MODE", "outbox").strip().lower(),
        slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL", ""),
        nudgebee_webhook_url=os.environ.get("NUDGEBEE_WEBHOOK_URL", ""),
        max_files=int(os.environ.get("MAX_FILES", "12")),
        repo_dir=ROOT / "repo",
        out_dir=ROOT / "out",
    )
