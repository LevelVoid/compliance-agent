"""Chunking: split source into function-sized units with real line numbers.

Python uses the stdlib `ast` module (exact). JavaScript/TypeScript uses a
brace-counting scanner - good enough for the demo, replaced by tree-sitter
later. No parser dependencies, so this runs anywhere Python runs.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

MAX_CHUNK_LINES = 120
MIN_CHUNK_LINES = 3


@dataclass(frozen=True)
class Chunk:
    path: str
    lang: str
    symbol: str
    start: int        # 1-based, inclusive
    end: int          # 1-based, inclusive
    text: str

    @property
    def key(self) -> str:
        return f"{self.path}:{self.start}-{self.end}"


def chunk_file(root: Path, path: str, lang: str) -> list[Chunk]:
    source = (root / path).read_text(errors="replace")
    lines = source.splitlines()
    if lang == "py":
        spans = _python_spans(source)
    else:
        spans = _js_spans(source)
    if not spans:
        spans = [("<module>", 1, len(lines))]

    chunks: list[Chunk] = []
    for symbol, start, end in spans:
        end = min(end, len(lines))
        if end - start + 1 < MIN_CHUNK_LINES:
            continue
        for win_start in range(start, end + 1, MAX_CHUNK_LINES):
            win_end = min(win_start + MAX_CHUNK_LINES - 1, end)
            text = "\n".join(lines[win_start - 1:win_end])
            chunks.append(
                Chunk(path=path, lang=lang, symbol=symbol,
                      start=win_start, end=win_end, text=text)
            )
    return chunks


def _python_spans(source: str) -> list[tuple[str, int, int]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    spans: list[tuple[str, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not hasattr(node, "end_lineno"):
                continue
            spans.append((node.name, node.lineno, node.end_lineno or node.lineno))
    spans.sort(key=lambda s: (s[1], -s[2]))
    return spans


_FUNC_START = re.compile(
    r"""^\s*(?:
        (?:export\s+)?(?:async\s+)?function\s+(?P<fname>\w+)
      | (?:export\s+)?(?:const|let|var)\s+(?P<vname>\w+)\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>)
      | (?P<rname>(?:app|router|server)\.\w+)\s*\(
    )""",
    re.VERBOSE,
)


def _js_spans(source: str) -> list[tuple[str, int, int]]:
    """Find function bodies by matching braces from each declaration line."""
    lines = source.splitlines()
    spans: list[tuple[str, int, int]] = []
    for index, line in enumerate(lines):
        match = _FUNC_START.match(line)
        if not match:
            continue
        symbol = match.group("fname") or match.group("vname") or match.group("rname") or "<anon>"
        depth = 0
        opened = False
        for cursor in range(index, min(len(lines), index + MAX_CHUNK_LINES * 2)):
            for char in lines[cursor]:
                if char == "{":
                    depth += 1
                    opened = True
                elif char == "}":
                    depth -= 1
            if opened and depth <= 0:
                spans.append((symbol, index + 1, cursor + 1))
                break
    # Drop nested spans already covered by a parent function.
    spans.sort(key=lambda s: (s[1], -s[2]))
    kept: list[tuple[str, int, int]] = []
    for span in spans:
        if kept and span[1] >= kept[-1][1] and span[2] <= kept[-1][2]:
            continue
        kept.append(span)
    return kept


def load_chunk(root: Path, path: str, start: int, end: int, lang: str, symbol: str = "") -> Chunk:
    """Re-read a chunk from disk inside an activity (never carried in workflow state)."""
    lines = (root / path).read_text(errors="replace").splitlines()
    end = min(end, len(lines))
    return Chunk(path=path, lang=lang, symbol=symbol, start=start, end=end,
                 text="\n".join(lines[start - 1:end]))
