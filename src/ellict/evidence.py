"""Append-only evidence of extraction, packaging, validation, and adoption."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def evidence_path(root: Path) -> Path:
    return root / ".ellict" / "evidence.jsonl"


def record(root: Path, phase: str, **payload: Any) -> dict[str, Any]:
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        **payload,
    }
    path = evidence_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    return event


def load(root: Path) -> list[dict[str, Any]]:
    path = evidence_path(root)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def summarize(root: Path) -> dict[str, Any]:
    events = load(root)
    phases: dict[str, int] = {}
    for event in events:
        phases[event["phase"]] = phases.get(event["phase"], 0) + 1
    latest_extract = next((event for event in reversed(events) if event["phase"] == "extract"), None)
    validations = [event for event in events if event["phase"] == "validate"]
    passed = sum(1 for event in validations if event.get("ok"))
    publishes = [event for event in events if event["phase"] == "publish"]
    adopted = [event for event in events if event["phase"] == "adopt" and event.get("installed")]
    return {
        "events": len(events),
        "phases": phases,
        "capabilities": (latest_extract or {}).get("count", 0),
        "sources": (latest_extract or {}).get("sources", []),
        "validation_passed": passed,
        "validation_total": len(validations),
        "published": len({event.get("slug") for event in publishes}),
        "adopted": len({event.get("slug") for event in adopted}),
    }
