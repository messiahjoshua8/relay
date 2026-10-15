"""Shared records for extraction, packaging, validation, and distribution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Invocation:
    """One way an agent can exercise a capability."""

    method: str
    command: str | None = None
    http_method: str | None = None
    path: str | None = None
    example: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class Capability:
    """A unit of application behavior an agent can be taught to use."""

    slug: str
    title: str
    summary: str
    kind: str
    invocations: list[Invocation] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    fingerprint: str = ""
    guidance: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["invocations"] = [item.to_dict() for item in self.invocations]
        if self.guidance is None:
            data.pop("guidance")
        return data


@dataclass
class SkillPackage:
    """Files that make up one installable skill."""

    slug: str
    files: dict[str, str]
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "fingerprint": self.fingerprint,
            "files": sorted(self.files),
        }


@dataclass
class Check:
    name: str
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationReport:
    slug: str
    ok: bool
    checks: list[Check] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
        }
