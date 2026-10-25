"""Repository ingestion.

Shallow-clones a public repo and returns a bounded file list. Never executes
repository code - parsing only. The file cap is what keeps a 5 GB monorepo
auditable in minutes instead of days.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "vendor", "coverage",
    "__pycache__", ".venv", "venv", "site-packages", "bower_components",
}
LANG_BY_SUFFIX = {
    ".py": "py", ".js": "js", ".mjs": "js", ".cjs": "js",
    ".jsx": "js", ".ts": "ts", ".tsx": "ts",
}
MAX_FILE_BYTES = 200_000


@dataclass(frozen=True)
class FileRef:
    path: str          # repo-relative
    lang: str
    size: int

    @property
    def key(self) -> str:
        return self.path


def clone(url: str, dest: Path) -> Path:
    """Shallow clone. Blobless filter keeps the transfer small."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", "--filter=blob:none", "--quiet", url, str(dest)],
        check=True,
        capture_output=True,
    )
    return dest


def discover(root: Path, max_files: int, include: list[str] | None = None) -> list[FileRef]:
    """Collect parseable source files, cheapest-to-audit first.

    Ordering is by path depth so entrypoints and core modules are audited before
    deep fixtures - matters when the file cap truncates the list.
    """
    refs: list[FileRef] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        lang = LANG_BY_SUFFIX.get(path.suffix.lower())
        if lang is None:
            continue
        if include and not any(pattern in str(rel) for pattern in include):
            continue
        size = path.stat().st_size
        if size == 0 or size > MAX_FILE_BYTES:
            continue
        refs.append(FileRef(path=str(rel), lang=lang, size=size))

    refs.sort(key=lambda ref: (len(Path(ref.path).parts), ref.path))
    return refs[:max_files]
