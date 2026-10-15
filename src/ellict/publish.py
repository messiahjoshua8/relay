"""Release approved packages to a canonical catalog and supported registries."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ellict.models import SkillPackage
from ellict.registries import write_registry_bundle

DESTINATIONS = ("canonical", "cursor", "agents", "claude", "registries")


def _write_tree(directory: Path, package: SkillPackage, extra: dict | None = None) -> None:
    if directory.exists():
        for child in directory.rglob("*"):
            if child.is_file():
                child.unlink()
    for relative, contents in package.files.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
        if relative.startswith("scripts/") and relative.endswith(".sh"):
            os.chmod(target, 0o755)
    if extra is not None:
        (directory / "distribution.json").write_text(
            json.dumps(extra, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def destination_dir(root: Path, destination: str, slug: str) -> Path:
    if destination == "canonical":
        return root / ".ellict" / "catalog" / slug
    if destination == "cursor":
        return root / ".cursor" / "skills" / slug
    if destination == "agents":
        return root / ".agents" / "skills" / slug
    if destination == "claude":
        return root / ".claude" / "skills" / slug
    raise ValueError(f"unsupported destination: {destination}")


def publish(
    root: Path,
    packages: list[SkillPackage],
    destinations: list[str],
    *,
    approved: set[str] | None = None,
    repository: str | None = None,
) -> list[dict]:
    root = root.resolve()
    unknown = [item for item in destinations if item not in DESTINATIONS]
    if unknown:
        raise ValueError(f"unsupported destinations: {', '.join(unknown)}")
    selected = [
        package
        for package in packages
        if approved is None or package.slug in approved
    ]
    released = []
    tree_destinations = [item for item in destinations if item != "registries"]
    for package in selected:
        if approved is not None and package.slug not in approved:
            continue
        for destination in tree_destinations:
            directory = destination_dir(root, destination, package.slug)
            meta = {
                "slug": package.slug,
                "fingerprint": package.fingerprint,
                "destination": destination,
                "format": "skill-directory",
            }
            if destination == "cursor":
                meta["install"] = f".cursor/skills/{package.slug}"
            elif destination == "agents":
                meta["install"] = f".agents/skills/{package.slug}"
            elif destination == "claude":
                meta["install"] = f".claude/skills/{package.slug}"
            else:
                meta["install"] = f".ellict/catalog/{package.slug}"
            _write_tree(directory, package, meta)
            released.append(meta)
    if "registries" in destinations:
        released.extend(write_registry_bundle(root, selected, repository=repository))
    index_path = root / ".ellict" / "catalog" / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index = {
        "destinations": destinations,
        "skills": [
            {
                "slug": package.slug,
                "fingerprint": package.fingerprint,
                "files": sorted(package.files),
            }
            for package in selected
        ],
    }
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return released
