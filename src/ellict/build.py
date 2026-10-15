"""Generate skill directories from extracted capabilities."""

from __future__ import annotations

from ellict.models import Capability, SkillPackage


def _invocation_block(capability: Capability) -> str:
    lines = []
    for invocation in capability.invocations:
        if invocation.method == "http":
            lines.append(f"`{invocation.http_method} {invocation.path}`")
        elif invocation.command:
            lines.append(f"```bash\n{invocation.command}\n```")
    return "\n\n".join(lines) if lines else "_No invocation recorded._"


def _description(capability: Capability) -> str:
    triggers = capability.summary.rstrip(".")
    command = next((item.command for item in capability.invocations if item.command), None)
    if command:
        return (
            f"{triggers}. Use when the user asks to {capability.title.lower()} "
            f"or run `{command}`."
        )[:1024]
    http = next((item for item in capability.invocations if item.method == "http"), None)
    if http:
        return (
            f"{triggers}. Use when the user needs `{http.http_method} {http.path}`."
        )[:1024]
    return f"{triggers}. Use when the user asks to {capability.title.lower()}."[:1024]


def _yaml(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_skill(capability: Capability) -> str:
    prereq = "\n".join(f"- {item}" for item in capability.prerequisites) or "- None"
    examples = "\n".join(f"- {item}" for item in capability.examples) or "- None"
    sources = "\n".join(f"- {item}" for item in capability.sources)
    return f"""---
name: {capability.slug}
description: {_yaml(_description(capability))}
---

# {capability.title}

## Prerequisites
{prereq}

## Invocation
{_invocation_block(capability)}

## Instructions
1. Confirm the prerequisite tools are available.
2. Run the invocation from the application root.
3. Treat a non-zero exit or an HTTP error as a failed attempt and report the output.
4. On success, summarize what the command or request changed.

## Examples
{examples}

## Sources
{sources}
"""


def _bullets(items: object) -> str:
    values = [str(item).strip() for item in (items or []) if str(item).strip()] if isinstance(items, list) else []
    return "\n".join(f"- {value}" for value in values) or "- None stated"


def render_guided_skill(capability: Capability) -> str:
    g = capability.guidance or {}
    steps = [str(s).strip() for s in g.get("steps") or [] if str(s).strip()]
    instructions = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)) or "1. Run the invocation from the application root."
    examples = "\n".join(
        (f"- {e['task']}: `{e['command']}`" if e.get("task") else f"- `{e['command']}`")
        + (f" (exits {e['exit']})" if e.get("exit") else "")
        for e in g.get("examples") or []
        if e.get("command")
    ) or "- None"
    sources = "\n".join(f"- {item}" for item in capability.sources)
    return f"""---
name: {capability.slug}
description: {_yaml(str(g.get("description") or capability.summary)[:1024])}
---

# {capability.title}

{str(g.get("helps") or "").strip()}

## When to use
{_bullets(g.get("when_to_use"))}

## When not to use
{_bullets(g.get("when_not_to_use"))}

## Prerequisites
{_bullets(capability.prerequisites)}

## Invocation
{_invocation_block(capability)}

## Instructions
{instructions}

## Examples
{examples}

## Verify
{str(g.get("verify") or "Check the command exits 0 and its output matches the request.").strip()}

## Pitfalls
{_bullets(g.get("pitfalls"))}

## Sources
{sources}
"""


def render_selection(capability: Capability, model: str | None) -> str:
    g = capability.guidance or {}
    removed = "\n".join(f"- {item}" for item in g.get("removed_examples") or []) or "- None"
    headless = "\n".join(f"- {item}" for item in g.get("headless_removed") or []) or "- None"
    return f"""# Why {capability.slug} is a skill

Selected by: {model or "unknown model"}

## What is worthy of becoming a skill
{g.get("worthy")}

## When an agent would use it
{g.get("would_use")}

## How an agent would use it
{g.get("how")}

## What it helps an agent do
{g.get("helps")}

## Demand
{g.get("demand") or "Not stated"}

## Agent limitation addressed
{g.get("agent_gap") or "Not stated"}

## Examples removed during grounding
{removed}

## Examples removed when run with no terminal attached
{headless}
"""


def render_reference(capability: Capability) -> str:
    invocations = "\n".join(
        f"- {invocation.to_dict()}" for invocation in capability.invocations
    )
    return f"""# {capability.slug}

Kind: {capability.kind}

Fingerprint: {capability.fingerprint}

## Invocations
{invocations or "- None"}

## Sources
{chr(10).join(f"- {source}" for source in capability.sources)}
"""


def render_script(capability: Capability) -> str | None:
    command = next((item.command for item in capability.invocations if item.command), None)
    if not command:
        return None
    return f"""#!/bin/sh
# Generated wrapper for {capability.slug}. Review before use.
set -eu
exec {command} "$@"
"""


def build(capabilities: list[Capability], *, model: str | None = None) -> list[SkillPackage]:
    packages = []
    for capability in capabilities:
        if capability.guidance is not None:
            files = {
                "SKILL.md": render_guided_skill(capability),
                "references/source.md": render_reference(capability),
                "references/selection.md": render_selection(capability, model),
            }
        else:
            files = {
                "SKILL.md": render_skill(capability),
                "references/source.md": render_reference(capability),
            }
        script = render_script(capability)
        if script:
            files["scripts/run.sh"] = script
        packages.append(
            SkillPackage(
                slug=capability.slug,
                files=files,
                fingerprint=capability.fingerprint,
            )
        )
    return packages
