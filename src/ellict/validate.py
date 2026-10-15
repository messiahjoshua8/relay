"""Check that a skill package is well formed and that safe commands run."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ellict.extract import node_bins
from ellict.models import Capability, Check, SkillPackage, ValidationReport

NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
FRONTMATTER = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def _field(frontmatter: str, key: str) -> str | None:
    match = re.search(rf"(?m)^{key}:\s*(.+)$", frontmatter)
    if not match:
        return None
    return match.group(1).strip().strip("\"'")


def _structural(package: SkillPackage) -> list[Check]:
    checks: list[Check] = []
    skill = package.files.get("SKILL.md", "")
    match = FRONTMATTER.match(skill)
    if not match:
        checks.append(Check("frontmatter", False, "SKILL.md is missing YAML frontmatter"))
        return checks
    body = match.group(1)
    name = _field(body, "name")
    description = _field(body, "description")
    checks.append(
        Check(
            "name",
            bool(name and NAME_RE.match(name) and name == package.slug),
            f"name={name!r} slug={package.slug!r}",
        )
    )
    checks.append(
        Check(
            "description",
            bool(description) and len(description or "") <= 1024,
            f"length={len(description or '')}",
        )
    )
    checks.append(
        Check(
            "instructions",
            "## Instructions" in skill and "## Invocation" in skill,
            "required sections present",
        )
    )
    for relative in package.files:
        if relative != "SKILL.md" and relative.startswith("scripts/"):
            checks.append(Check("script", relative.endswith(".sh"), relative))
    return checks


def _entry_module(root: Path, binary: str) -> str | None:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return None
    text = pyproject.read_text(encoding="utf-8")
    section = re.search(r"(?ms)^\[project\.scripts\]\s*(.*?)(?=^\[|\Z)", text)
    if not section:
        return None
    match = re.search(rf'(?m)^{re.escape(binary)}\s*=\s*"([^":]+)', section.group(1))
    if not match:
        return None
    return match.group(1)


def resolve_command(root: Path, command: str) -> tuple[list[str], dict[str, str]]:
    """Return the argv that runs ``command`` from this checkout, and any environment it needs."""
    parts = command.split()
    binary = parts[0]
    if shutil.which(binary):
        return parts, {}
    module = _entry_module(root, binary)
    src = root / "src"
    if module and (src / module.split(".", 1)[0]).exists():
        pythonpath = str(src)
        if os.environ.get("PYTHONPATH"):
            pythonpath = pythonpath + os.pathsep + os.environ["PYTHONPATH"]
        return [sys.executable, "-m", module, *parts[1:]], {"PYTHONPATH": pythonpath}
    script = root / f"{binary}.py"
    if script.exists():
        return [sys.executable, str(script), *parts[1:]], {}
    local = root / binary
    if local.exists() and os_access(local):
        return [str(local), *parts[1:]], {}
    node = node_bins(root).get(binary)
    if node and shutil.which("node") and (root / node).is_file():
        return ["node", str(root / node), *parts[1:]], {}
    return parts, {}


def _resolve(root: Path, command: str) -> tuple[list[str], dict[str, str]]:
    argv, env = resolve_command(root, command)
    return [*argv, "--help"], env


def os_access(path: Path) -> bool:
    return path.is_file() and path.stat().st_mode & 0o111


def _help_command(root: Path, command: str) -> tuple[list[str], dict[str, str]]:
    return _resolve(root, command)


def help_text(root: Path, command: str) -> str | None:
    """Run ``command --help`` and return its output, or None when it does not run."""
    argv, extra_env = _resolve(root, command)
    try:
        completed = subprocess.run(
            argv,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={**os.environ, **extra_env},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return (completed.stdout or "") + (completed.stderr or "")


def _execute(root: Path, capability: Capability) -> list[Check]:
    checks: list[Check] = []
    for invocation in capability.invocations:
        if invocation.method != "cli" or not invocation.command:
            checks.append(
                Check(
                    "execute",
                    True,
                    f"skipped non-cli invocation {invocation.example or invocation.path}",
                )
            )
            continue
        if invocation.command.startswith("make "):
            checks.append(Check("execute", True, f"skipped mutating target {invocation.command}"))
            continue
        argv, extra_env = _help_command(root, invocation.command)
        try:
            completed = subprocess.run(
                argv,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env={**os.environ, **extra_env},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            checks.append(Check("execute", False, f"{' '.join(argv)} failed: {exc}"))
            continue
        detail = (completed.stdout or completed.stderr).strip().splitlines()
        snippet = detail[0] if detail else f"exit {completed.returncode}"
        checks.append(
            Check(
                "execute",
                completed.returncode == 0,
                f"{' '.join(argv)} -> {snippet}",
            )
        )
    return checks


def _headless(runs: list[dict]) -> Check:
    passed = [run for run in runs if run["ok"]]
    if passed:
        return Check("headless", True, f"{len(passed)} of {len(runs)} examples ran with no terminal attached")
    if not runs:
        return Check("headless", False, "no examples to run; an agent has nothing to copy")
    return Check("headless", False, f"no example ran with no terminal attached: {runs[0]['command']} -> {runs[0]['detail']}")


def validate(
    root: Path,
    packages: list[SkillPackage],
    capabilities: list[Capability],
    headless: dict[str, list[dict]] | None = None,
) -> list[ValidationReport]:
    by_slug = {item.slug: item for item in capabilities}
    reports = []
    for package in packages:
        checks = _structural(package)
        capability = by_slug.get(package.slug)
        if capability:
            checks.extend(_execute(root, capability))
        if headless is not None and package.slug in headless:
            checks.append(_headless(headless[package.slug]))
        reports.append(
            ValidationReport(
                slug=package.slug,
                ok=all(check.ok for check in checks),
                checks=checks,
            )
        )
    return reports
