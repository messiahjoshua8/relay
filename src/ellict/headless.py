"""Run each skill's examples the way an agent would: no terminal, no keyboard, a timeout."""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
from pathlib import Path

from ellict.models import Capability
from ellict.validate import resolve_command

DEFAULT_TIMEOUT = 15
PIPE_TOOLS = {"cat", "echo", "printf", "head", "tail", "grep", "fgrep", "egrep", "sort", "uniq", "wc", "tr", "cut"}
CONTROL = {";", ";;", "&", "&&", "||", "(", ")", "|&"}
REDIRECTS = {"<", ">", ">>", "<<", "<<<", ">|", "<>"}


def segments(command: str) -> list[list[str]] | None:
    """Split a command line into pipeline stages of tokens, or None if it cannot be parsed."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    stages: list[list[str]] = [[]]
    for token in tokens:
        if token == "|":
            stages.append([])
        else:
            stages[-1].append(token)
    return stages


def problem(command: str, binary: str) -> str | None:
    """Why ellict will not run ``command`` unattended, or None when it is safe to run."""
    stages = segments(command)
    if not stages or not all(stages):
        return "cannot be parsed as a simple pipeline"
    for stage in stages:
        for index, token in enumerate(stage):
            if token in CONTROL or "$(" in token or "`" in token or token.startswith(("<(", ">(")):
                return "uses shell control syntax"
            if token in REDIRECTS:
                target = stage[index + 1] if index + 1 < len(stage) else ""
                if not target or target.startswith(("/", "~")) or ".." in target.split("/"):
                    return "redirects outside the working directory"
    heads = [stage[0] for stage in stages]
    if binary not in heads:
        return f"does not run {binary}"
    other = [head for head in heads if head != binary and head not in PIPE_TOOLS]
    if other:
        return f"pipes through {other[0]}, which ellict does not run"
    return None


def example_commands(capability: Capability) -> list[tuple[str, int]]:
    """Each example command with the exit code that means it worked."""
    if capability.guidance is not None:
        return [(e["command"], e.get("exit", 0)) for e in capability.guidance.get("examples") or [] if e.get("command")]
    return [(e.strip("`"), 0) for e in capability.examples]


def _shims(root: Path, binaries: set[str], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for binary in binaries:
        if shutil.which(binary):
            continue
        argv, env = resolve_command(root, binary)
        if argv == [binary]:
            continue
        exports = "".join(f"{key}={shlex.quote(value)}\nexport {key}\n" for key, value in env.items())
        shim = directory / binary
        shim.write_text(f"#!/bin/sh\n{exports}exec {shlex.join(argv)} \"$@\"\n", encoding="utf-8")
        shim.chmod(0o755)


def _write_fixtures(work: Path, fixtures: list[dict]) -> None:
    for fixture in fixtures:
        target = work / fixture["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(fixture["contents"], encoding="utf-8")


def _first_line(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[0][:200] if lines else ""


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    errors = [line for line in lines if "error" in line.lower()]
    return (errors or lines or [""])[-1][:200]


def run_one(command: str, *, cwd: Path, env: dict[str, str], timeout: float, expected: int = 0) -> dict:
    process = subprocess.Popen(
        ["/bin/sh", "-c", command],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        return {
            "command": command,
            "ok": False,
            "detail": f"no exit after {timeout:g}s with no terminal attached; it is waiting for a person or input",
        }
    if process.returncode != expected:
        want = f", expected {expected}" if expected else ""
        detail = f"exit {process.returncode}{want}: {_last_line(err) or _last_line(out)}".rstrip(": ")
    else:
        detail = _first_line(out) or _first_line(err) or f"exit {expected}, no output"
    return {"command": command, "ok": process.returncode == expected, "detail": detail}


def check(root: Path, capabilities: list[Capability], *, timeout: float | None = None) -> dict[str, list[dict]]:
    """Run every CLI example and drop the ones an agent could not run. Returns results per slug."""
    root = root.resolve()
    if timeout is None:
        timeout = float(os.environ.get("ELLICT_EXAMPLE_TIMEOUT") or DEFAULT_TIMEOUT)
    base = root / ".ellict" / "headless"
    if base.exists():
        shutil.rmtree(base)
    commands = {
        capability.slug: capability.invocations[0].command
        for capability in capabilities
        if capability.invocations
        and capability.invocations[0].method == "cli"
        and capability.invocations[0].command
        and not capability.invocations[0].command.startswith("make ")
    }
    shims = base / ".bin"
    _shims(root, {command.split()[0] for command in commands.values()}, shims)
    env = {**os.environ, "PATH": os.pathsep.join([str(shims), os.environ.get("PATH", "")])}
    results: dict[str, list[dict]] = {}
    for capability in capabilities:
        if capability.slug not in commands:
            continue
        binary = commands[capability.slug].split()[0]
        fixtures = (capability.guidance or {}).get("fixtures") or []
        runs = []
        for index, (command, expected) in enumerate(example_commands(capability)):
            reason = problem(command, binary)
            if reason:
                runs.append({"command": command, "ok": False, "detail": f"not run: {reason}"})
                continue
            work = base / capability.slug / str(index)
            work.mkdir(parents=True)
            _write_fixtures(work, fixtures)
            runs.append(run_one(command, cwd=work, env=env, timeout=timeout, expected=expected))
        results[capability.slug] = runs
        _keep_passing(capability, runs)
    return results


def _keep_passing(capability: Capability, runs: list[dict]) -> None:
    failed = {run["command"]: run["detail"] for run in runs if not run["ok"]}
    if not failed:
        return
    capability.examples = [e for e in capability.examples if e.strip("`") not in failed]
    if capability.guidance is not None:
        capability.guidance["examples"] = [
            e for e in capability.guidance.get("examples") or [] if e.get("command") not in failed
        ]
        capability.guidance["headless_removed"] = [f"{command} ({detail})" for command, detail in failed.items()]
