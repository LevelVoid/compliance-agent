#!/usr/bin/env python3
"""CLI entrypoint.

    python audit.py probe
    python audit.py run --path fixtures/bad --frameworks SOC2,HIPAA,GDPR
    python audit.py run --clone OWASP/NodeGoat --frameworks SOC2,GDPR
    python audit.py run --path fixtures/bad --slow 3      # camera-friendly pacing
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

from pipeline.adjudicate import probe as llm_probe
from pipeline.engine import LLMConfig, audit_repo
from pipeline.score import as_json, render_markdown, render_terminal
from pipeline.snapshot import clone
from settings import ROOT, get_settings


def _llm() -> LLMConfig:
    settings = get_settings()
    return LLMConfig(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        use_mock=settings.use_mock,
    )


def _frameworks(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    llm = _llm()

    if args.clone:
        repo_name = args.clone.split("/")[-1]
        target = ROOT / "repo" / repo_name
        print(f"cloning {args.clone} …", flush=True)
        clone(f"https://github.com/{args.clone}.git", target)
    else:
        target = Path(args.path).resolve()
        repo_name = target.name

    report = await audit_repo(
        target,
        repo_name=repo_name,
        frameworks=_frameworks(args.frameworks),
        llm=llm,
        max_files=args.max_files or settings.max_files,
        delay=args.slow,
        progress=lambda i, n, path: print(f"  [{i}/{n}] {path}", flush=True),
    )

    settings.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.commit[:8]
    (settings.out_dir / f"report-{stamp}.json").write_text(json.dumps(as_json(report), indent=2))
    (settings.out_dir / f"report-{stamp}.md").write_text(render_markdown(report))
    print(render_terminal(report))
    print(f"wrote {settings.out_dir}/report-{stamp}.md and .json")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="audit")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("probe", help="verify the LLM endpoint before anything depends on it")

    run = sub.add_parser("run", help="audit a repository")
    run.add_argument("--path", default="fixtures/bad")
    run.add_argument("--clone", default="", help="owner/repo on GitHub (shallow clone)")
    run.add_argument("--frameworks", default="", help="comma separated, e.g. SOC2,HIPAA")
    run.add_argument("--max-files", type=int, default=0)
    run.add_argument("--slow", type=float, default=0.0, help="seconds to pause between files")

    args = parser.parse_args()
    if args.command == "probe":
        settings = get_settings()
        if not settings.llm_configured:
            print("LLM not configured. Set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL in .env")
            print("or run with MOCK_LLM=1 to use the deterministic stand-in.")
            return 1
        try:
            print("endpoint replied:", llm_probe(settings.llm_base_url, settings.llm_api_key, settings.llm_model))
            return 0
        except Exception as error:  # noqa: BLE001 - probe is allowed to be blunt
            print(f"probe failed: {type(error).__name__}: {error}")
            return 1
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
