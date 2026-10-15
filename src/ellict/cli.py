"""Command line entry for the extract → publish pipeline."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from ellict import __version__
from ellict.agentic import write_prompt
from ellict.evidence import summarize
from ellict.extract import extract
from ellict.llm import Model, load_dotenv
from ellict.pipeline import SelectionError, observe, run
from ellict.source import is_remote, resolve


def _print(payload: object) -> None:
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ellict",
        description="Turn an application into installable agent skills.",
    )
    parser.add_argument("--version", action="version", version=f"ellict {__version__}")
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=("run", "prompt", "extract", "maintain", "report", "observe"),
        help="pipeline step (default: run); prompt writes the selection questions for an agent to answer itself",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="application directory or git URL",
    )
    parser.add_argument(
        "--publish-to",
        default=None,
        help="directory of a git repo you own; ellict writes skills there and prints the push",
    )
    parser.add_argument(
        "--select",
        choices=("auto", "agent", "rules"),
        default="auto",
        help="agent: a model reads the docs and chooses skills; rules: static analysis only; auto: agent when a model is configured",
    )
    parser.add_argument(
        "--answer",
        default=None,
        help="JSON reply to the questions from `ellict prompt`, written by the agent running ellict; no API key needed",
    )
    args = parser.parse_args(argv)
    if is_remote(args.path):
        try:
            root = resolve(args.path)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            parser.error(detail)
    else:
        root = Path(args.path)
    if not root.is_dir():
        parser.error(f"not a directory: {root}")
    load_dotenv(Path.cwd() / ".env", root / ".env")
    if args.command == "extract":
        _print([item.to_dict() for item in extract(root)])
        return 0
    if args.command == "report":
        _print(summarize(root))
        return 0
    if args.command == "observe":
        _print(observe(root))
        return 0
    if args.command == "prompt":
        path = write_prompt(root)
        answer = path.parent / "answer.json"
        print(f"Answer the questions in {path} as one JSON object, save it to {answer}, then run:")
        print(f"ellict run {root} --answer {answer}")
        return 0
    publish_to = Path(args.publish_to) if args.publish_to else None
    model = None
    select = args.select
    if args.answer:
        model = Model("answer", Path(args.answer).name, command=str(Path(args.answer).resolve()))
        select = "agent"
    try:
        result = run(
            root,
            maintain_only=args.command == "maintain",
            publish_to=publish_to,
            select=select,
            model=model,
        )
    except SelectionError as exc:
        print(f"ellict: selection failed: {exc}", file=sys.stderr)
        return 2
    if result["selector"] == "rules" and result["selector_reason"] != "requested":
        print(f"ellict: using static rules ({result['selector_reason']})", file=sys.stderr)
    for line in result.get("push") or []:
        print(line, file=sys.stderr)
    _print(result)
    rejected = result.get("rejected") or []
    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
