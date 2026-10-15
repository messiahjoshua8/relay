"""Resolve a local directory or a shallow clone of a public repository."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def is_remote(target: str) -> bool:
    return target.startswith(("https://", "http://", "git@"))


def _name(target: str) -> str:
    tail = target.rstrip("/").split("/")[-1]
    if tail.endswith(".git"):
        tail = tail[: -len(".git")]
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", tail).strip("-")
    return slug or "repository"


def resolve(target: str, *, workspace: Path | None = None) -> Path:
    if not is_remote(target):
        return Path(target)
    root = workspace or Path.cwd()
    destination = root / ".ellict" / "checkouts" / _name(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if (destination / ".git").exists():
        subprocess.run(
            ["git", "-C", str(destination), "pull", "--ff-only"],
            check=True,
            capture_output=True,
            text=True,
        )
        return destination
    subprocess.run(
        ["git", "clone", "--depth", "1", target, str(destination)],
        check=True,
        capture_output=True,
        text=True,
    )
    return destination
