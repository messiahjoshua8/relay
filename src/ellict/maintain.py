"""Regenerate skills whose source capabilities changed."""

from __future__ import annotations

import json
from pathlib import Path

from ellict.models import Capability

STATE = ".ellict/state.json"


def load_state(root: Path) -> dict[str, str]:
    path = root / STATE
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    fingerprints = data.get("fingerprints", {})
    return {str(key): str(value) for key, value in fingerprints.items()}


def save_state(root: Path, capabilities: list[Capability]) -> None:
    path = root / STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fingerprints": {item.slug: item.fingerprint for item in capabilities},
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def affected(root: Path, capabilities: list[Capability]) -> list[Capability]:
    previous = load_state(root)
    current = {item.slug: item for item in capabilities}
    changed = []
    for slug, capability in current.items():
        if previous.get(slug) != capability.fingerprint:
            changed.append(capability)
    for slug in previous:
        if slug not in current:
            changed.append(
                Capability(
                    slug=slug,
                    title=slug,
                    summary="removed",
                    kind="removed",
                    fingerprint="",
                )
            )
    return changed
