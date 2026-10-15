"""One-command pipeline: extract, build, validate, publish, and record evidence."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ellict import agentic, headless
from ellict.build import build
from ellict.evidence import record, summarize
from ellict.extract import extract
from ellict.llm import Model, ModelError, from_env
from ellict.maintain import affected, load_state, save_state
from ellict.models import Capability, SkillPackage
from ellict.publish import destination_dir, publish
from ellict.validate import validate

DEFAULT_DESTINATIONS = ["canonical", "cursor", "registries"]


def load_config(root: Path) -> dict:
    path = root / "ellict.json"
    if not path.exists():
        return {"destinations": DEFAULT_DESTINATIONS}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("destinations", DEFAULT_DESTINATIONS)
    return data


def stage_owned(root: Path, owned: Path) -> list[str]:
    """Copy approved skills into a repo the user owns. Do not commit or push."""
    source = root / ".ellict" / "catalog"
    owned.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        for skill in source.iterdir():
            if not skill.is_dir():
                continue
            target = owned / skill.name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(skill, target)
    commands = [
        f"git -C {owned} add .",
        f'git -C {owned} commit -m "Add skills extracted by ellict"',
        f"git -C {owned} push",
    ]
    (owned / "PUSH.txt").write_text("\n".join(commands) + "\n", encoding="utf-8")
    return commands


class SelectionError(RuntimeError):
    pass


def _choose(root: Path, config: dict, select: str, model: Model | None) -> tuple[str, Model | None, str]:
    if select == "rules":
        return "rules", None, "requested"
    if model is None:
        try:
            model = from_env(config)
        except ModelError as exc:
            raise SelectionError(str(exc)) from exc
    if model is None:
        if select == "agent":
            raise SelectionError(
                "no model configured: set ANTHROPIC_API_KEY, OPENAI_API_KEY, or ELLICT_MODEL"
            )
        return "rules", None, "no model configured"
    return "agent", model, model.spec


def _clear_outputs(root: Path) -> None:
    for name in ("build", "catalog", "distribution"):
        target = root / ".ellict" / name
        if target.exists():
            shutil.rmtree(target)


def run(
    root: Path,
    *,
    maintain_only: bool = False,
    publish_to: Path | None = None,
    select: str = "auto",
    model: Model | None = None,
) -> dict:
    root = root.resolve()
    config = load_config(root)
    selector, model, selector_reason = _choose(root, config, select, model)
    selection: dict | None = None
    if selector == "agent":
        try:
            capabilities, selection = agentic.select(root, model)
        except ModelError as exc:
            record(root, "select", selector="agent", model=model.spec, ok=False, error=str(exc))
            raise SelectionError(str(exc)) from exc
        record(
            root,
            "select",
            selector="agent",
            model=model.spec,
            ok=True,
            cached=selection["cached"],
            kept=selection["kept"],
            grounding_dropped=selection["grounding_dropped"],
            model_rejected=selection["model_rejected"],
        )
    else:
        capabilities = extract(root)
        record(root, "select", selector="rules", reason=selector_reason, kept=[c.slug for c in capabilities])
    sources = sorted({source.split(":", 1)[0] for item in capabilities for source in item.sources})
    record(
        root,
        "extract",
        count=len(capabilities),
        slugs=[item.slug for item in capabilities],
        sources=sources,
    )
    if not maintain_only:
        _clear_outputs(root)
    selected = capabilities
    removed: list[str] = []
    if maintain_only:
        delta = affected(root, capabilities)
        removed = [item.slug for item in delta if item.kind == "removed"]
        selected = [item for item in delta if item.kind != "removed"]
        record(
            root,
            "maintain",
            changed=[item.slug for item in selected],
            removed=removed,
            unchanged=len(capabilities) - len(selected),
        )
        if not selected and not removed:
            save_state(root, capabilities)
            return {
                "selector": selector,
                "selector_reason": selector_reason,
                "changed": [],
                "removed": [],
                "published": [],
                "push": [],
                "summary": summarize(root),
            }

    runs = headless.check(root, selected)
    for slug, results in runs.items():
        record(root, "headless", slug=slug, runs=results)
    packages = build(selected, model=model.spec if model else None)
    build_root = root / ".ellict" / "build"
    for package in packages:
        skill_dir = build_root / package.slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        for relative, contents in package.files.items():
            target = skill_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding="utf-8")
    for package in packages:
        record(root, "build", slug=package.slug, files=sorted(package.files), fingerprint=package.fingerprint)
    reports = validate(root, packages, selected, runs)
    approved = set()
    for report in reports:
        record(
            root,
            "validate",
            slug=report.slug,
            ok=report.ok,
            checks=[check.to_dict() for check in report.checks],
        )
        if report.ok:
            approved.add(report.slug)
    released = publish(
        root,
        packages,
        list(config["destinations"]),
        approved=approved,
        repository=config.get("repository"),
    )
    for item in released:
        record(root, "publish", **item)
    for slug in removed:
        for destination in config["destinations"]:
            directory = destination_dir(root, destination, slug)
            if directory.exists():
                for child in sorted(directory.rglob("*"), reverse=True):
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
                directory.rmdir()
    save_state(root, capabilities)
    observe(root)
    owned = publish_to or config.get("publishTo")
    push: list[str] = []
    if owned and approved:
        push = stage_owned(root, Path(owned).expanduser().resolve())
    return {
        "selector": selector,
        "selector_reason": selector_reason,
        "selection": {
            "grounding_dropped": selection["grounding_dropped"],
            "model_rejected": selection["model_rejected"],
            "cached": selection["cached"],
        }
        if selection
        else None,
        "capabilities": [item.slug for item in capabilities],
        "changed": [item.slug for item in selected] if maintain_only else [item.slug for item in capabilities],
        "removed": removed,
        "approved": sorted(approved),
        "rejected": sorted({package.slug for package in packages} - approved),
        "published": released,
        "push": push,
        "summary": summarize(root),
    }


def observe(root: Path) -> list[dict]:
    """Record which catalogued skills are installed in a supported channel."""
    root = root.resolve()
    index_path = root / ".ellict" / "catalog" / "index.json"
    if not index_path.exists():
        return []
    index = json.loads(index_path.read_text(encoding="utf-8"))
    observations = []
    for skill in index.get("skills", []):
        slug = skill["slug"]
        installed = any(
            (destination_dir(root, destination, slug) / "SKILL.md").exists()
            for destination in ("cursor", "agents")
        )
        event = record(
            root,
            "adopt",
            slug=slug,
            installed=installed,
            fingerprint=skill.get("fingerprint"),
        )
        observations.append(event)
    return observations


def packages_for(capabilities: list[Capability]) -> list[SkillPackage]:
    return build(capabilities)


def previous_fingerprints(root: Path) -> dict[str, str]:
    return load_state(root)
