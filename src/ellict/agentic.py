"""Model-driven selection: read the docs and interface, choose skills, ground every claim."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from pathlib import Path

from ellict.extract import DOC_SUFFIXES, PROMPT_PREFIX, SKIP_DIRS, _rel, extract, slugify
from ellict.headless import problem, segments
from ellict.llm import Model, ModelError, ask, decide
from ellict.models import Capability, Invocation
from ellict.validate import help_text

DOC_BUDGET = 60_000
DOC_LIMIT = 20_000
MANIFEST_LIMIT = 4_000
HELP_LIMIT = 6_000
FIXTURE_LIMIT = 20_000
MANIFESTS = ("pyproject.toml", "setup.py", "setup.cfg", "package.json", "Cargo.toml", "go.mod", "Makefile")
SKIP_DOCS = ("changelog", "license", "code_of_conduct", "security", "history", "contributing")
SKIP_DOC_DIRS = {"test", "tests", "test_data", "testdata", "fixtures", "__tests__"}
FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)
FLAG = re.compile(r"^--?[a-zA-Z][\w-]*")

SYSTEM = """You are ellict's skill selector. You read one application's documentation and interface and decide which of its capabilities deserve to become agent skills.

Use what you know from training about how coding agents behave today: what they already do reliably without help, and where they still fail or produce poor results. A skill is worth publishing only when an agent would choose it for a real task and using it changes the outcome of that task.

These are not skills on their own: installation steps, a single flag or styling option, a sample shell pipeline, test, lint, format, or build targets, and anything an agent already does well unaided. They can appear as examples inside a skill.

One skill per agent job, not per mode. When several uses run the same command and differ only by flags, input type, or output format, they are one skill, with each mode as an example. Propose a second skill on the same command only when it is a different job: a different input the agent starts from, a different result the agent needs, and different reasons to reach for it. Say why in distinct_job; ellict merges a second skill on the same command into the first when distinct_job is empty. A flag that changes what kind of result comes back, such as a measurement versus rewritten content, a pass or fail verdict versus a rewritten file, or the whole document versus one extracted part, is usually a different job. A flag that only changes where input comes from (a file, stdin, a directory), how text is matched, or how the same result is cleaned up, filtered, or formatted is a mode.

An agent runs commands without a person at the keyboard and reads what comes back. A capability that only works in an interactive screen, pager, prompt, or terminal UI is not a skill, even if piping into it is documented, and neither is an option whose output is produced from inside such a screen. Set interactive to true for anything that needs a person to operate it, and prefer rejecting it. If nothing in the application can run unattended, return no skills; that is a correct answer.

Colour, styling, and layout in a terminal are for a person's eyes; an agent reads plain text. When a command can also write a file such as HTML, SVG, or a report, that file is often the result an agent actually hands back.

Ellict runs every example for real, with no terminal attached, no network, and a timeout, in an empty directory that contains only the fixtures you declare for that skill. An example that hangs, fails, or needs a file you did not declare is removed, and a skill with no example that runs is not published. So write examples an agent would actually run, declare small sample input files as fixtures, and start each example with the command exactly as the invocation names it (never python -m, npx, go run, or cargo run), optionally piped with cat, echo, printf, head, tail, grep, sort, uniq, wc, tr, or cut.

Ground everything in the evidence. Use only commands, subcommands, flags, HTTP routes, and file paths that appear in it. Respond with one JSON object and nothing else."""

SCHEMA = {
    "skills": [
        {
            "slug": "lowercase-hyphenated name, at most 64 characters",
            "title": "short human title",
            "description": "third person; what the skill does and when an agent should use it; at most 1024 characters",
            "worthy": "what here is worthy of becoming a skill, and why",
            "would_use": "when you, as an agent, would actually reach for it",
            "how": "how you would use it",
            "helps": "what it helps you do that you do not already do well",
            "demand": "who needs this and how often, from the evidence and what you know of the ecosystem",
            "agent_gap": "the specific agent limitation this addresses",
            "interactive": "true if a person must operate it in a terminal UI, pager, or prompt; otherwise false",
            "distinct_job": "if another skill uses the same command: why this is a different job, not a mode of it; otherwise empty",
            "invocation": {"method": "cli or http", "command": "base command and subcommand only, no arguments (cli)", "http_method": "GET/POST/... (http)", "path": "/route (http)"},
            "prerequisites": ["what must be true before running it"],
            "when_to_use": ["concrete triggers"],
            "when_not_to_use": ["cases where the agent should not use it"],
            "steps": ["ordered instructions for the agent"],
            "examples": [{"task": "what the user asked", "command": "exact command line using only documented flags", "exit": "expected exit code when it is not 0, such as a check mode that reports a problem; otherwise omit"}],
            "fixtures": [{"path": "relative path of a sample input file the examples read", "contents": "small file contents"}],
            "verify": "how the agent can tell it worked",
            "pitfalls": ["mistakes to avoid"],
            "sources": ["evidence file paths this skill is based on"],
        }
    ],
    "rejected": [{"name": "candidate considered", "reason": "why it is not a skill"}],
}


def _read(path: Path, limit: int) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"


def _docs(root: Path) -> list[dict]:
    paths = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS or part.lower() in SKIP_DOC_DIRS for part in rel.parts) or not path.is_file():
            continue
        if path.suffix.lower() not in DOC_SUFFIXES:
            continue
        if any(word in path.stem.lower() for word in SKIP_DOCS):
            continue
        paths.append(path)
    paths.sort(key=lambda p: (not p.stem.lower().startswith("readme"), len(p.relative_to(root).parts), p.stat().st_size))
    docs, used = [], 0
    for path in paths:
        if used >= DOC_BUDGET:
            break
        text = _read(path, min(DOC_LIMIT, DOC_BUDGET - used))
        if text.strip():
            docs.append({"path": _rel(root, path), "text": text})
            used += len(text)
    return docs


def gather(root: Path) -> dict:
    """Collect what the model reads: docs, manifests, the interface inventory, and real help output."""
    root = root.resolve()
    inventory = extract(root)
    names = [name for name in MANIFESTS if (root / name).is_file()]
    for capability in inventory:
        for source in capability.sources:
            kind, _, path = source.partition(":")
            if kind == "cli" and path not in names and Path(path).name in MANIFESTS and (root / path).is_file():
                names.append(path)
    manifests = [{"path": name, "text": _read(root / name, MANIFEST_LIMIT)} for name in names]
    helps: dict[str, str] = {}
    for capability in inventory:
        for invocation in capability.invocations:
            command = invocation.command
            if invocation.method != "cli" or not command or command.startswith("make ") or command in helps:
                continue
            output = help_text(root, command)
            if output:
                helps[command] = output[:HELP_LIMIT]
    return {
        "application": root.name,
        "docs": _docs(root),
        "manifests": manifests,
        "inventory": [
            {
                "slug": item.slug,
                "kind": item.kind,
                "summary": item.summary,
                "invocations": [inv.to_dict() for inv in item.invocations],
            }
            for item in inventory
        ],
        "help": helps,
    }


def digest(evidence: dict) -> str:
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()[:16]


def prompt(evidence: dict) -> str:
    return "\n\n".join(
        [
            f"Application: {evidence['application']}",
            "Answer these for every candidate you consider:\n"
            "1. What can you see here that is worthy of becoming a skill?\n"
            "2. What would you actually use?\n"
            "3. How would you use it?\n"
            "4. What do you think it would help you do?",
            "Interface inventory (found by static analysis):\n" + json.dumps(evidence["inventory"], indent=1),
            "Help output from running the commands:\n" + json.dumps(evidence["help"], indent=1),
            "Manifests:\n" + "\n\n".join(f"--- {m['path']}\n{m['text']}" for m in evidence["manifests"]),
            "Documentation:\n" + "\n\n".join(f"--- {d['path']}\n{d['text']}" for d in evidence["docs"]),
            "Reply with JSON in exactly this shape:\n" + json.dumps(SCHEMA, indent=1),
        ]
    )


def _known(evidence: dict) -> dict:
    binaries: set[str] = set()
    routes: set[tuple[str, str]] = set()
    for item in evidence["inventory"]:
        for inv in item["invocations"]:
            if inv.get("method") == "cli" and inv.get("command"):
                binaries.add(inv["command"].split()[0])
            if inv.get("method") == "http":
                routes.add((str(inv.get("http_method", "")).upper(), str(inv.get("path", ""))))
    corpus = "\n".join(d["text"] for d in evidence["docs"])
    for fence in FENCE.finditer(corpus):
        for line in fence.group(1).splitlines():
            line = PROMPT_PREFIX.sub("", line.strip())
            if line and not line.startswith("#"):
                binaries.add(line.split()[0])
    helps = "\n".join(evidence["help"].values())
    return {
        "binaries": binaries,
        "routes": routes,
        "text": corpus + "\n" + helps + "\n" + "\n".join(m["text"] for m in evidence["manifests"]),
        "docs": corpus,
        "help": evidence["help"],
    }


def _ground_command(command: str, known: dict) -> tuple[str | None, str]:
    tokens = command.split()
    if not tokens or tokens[0] not in known["binaries"]:
        return None, f"command not found in the repository: {command!r}"
    kept = [tokens[0]]
    for token in tokens[1:]:
        if not re.fullmatch(r"[a-z][\w-]*", token):
            break
        if not re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", known["text"]):
            return None, f"subcommand not found in docs or help: {token!r}"
        kept.append(token)
    return " ".join(kept), ""


def _unknown_flags(command: str, base: str, known: dict) -> list[str]:
    help_text = known["help"].get(base) or known["help"].get(base.split()[0]) or ""
    reference = help_text + "\n" + known["docs"]
    binary = base.split()[0]
    flags = []
    for stage in segments(command) or []:
        if stage and stage[0] == binary:
            for token in stage[1:]:
                match = FLAG.match(token.split("=", 1)[0])
                if match and match.group(0) not in reference:
                    flags.append(match.group(0))
    return flags


def _fixtures(raw: dict) -> list[dict]:
    fixtures = []
    for item in raw.get("fixtures") or []:
        if not isinstance(item, dict):
            continue
        path, contents = str(item.get("path") or "").strip(), item.get("contents")
        parts = Path(path).parts
        if not path or Path(path).is_absolute() or ".." in parts or not isinstance(contents, str):
            continue
        if len(contents) <= FIXTURE_LIMIT and len(fixtures) < 10:
            fixtures.append({"path": path, "contents": contents})
    return fixtures


MAX_PER_COMMAND = 3


def _truthy(value: object) -> bool:
    return value is True or str(value).strip().lower() in {"true", "yes", "1"}


REVIEW_RULE = """Two skills are the same job when an agent would reach for them for the same reason and gets the same kind of result. Differences in where input comes from, output format, previewing versus applying, or matching options are modes of one job. They are different jobs when the kind of result differs: a measurement versus rewritten content, a pass or fail verdict versus a rewritten file, or a whole document versus one extracted part."""

REVIEW_SYSTEM = f"""You decide whether skills that run the same command are separate agent jobs.

{REVIEW_RULE}

Respond with one JSON object and nothing else."""
SAME_JOB_THRESHOLD = 0.5

Review = Callable[[str, list[Capability]], "list[dict] | None"]


def _review_skills(capabilities: list[Capability]) -> list[dict]:
    return [
        {
            "slug": c.slug,
            "description": c.summary,
            "examples": [e["command"] for e in c.guidance["examples"] if e.get("command")],
        }
        for c in capabilities
    ]


def decisions_review(cache_dir: Path, fallback: Review | None) -> Review:
    """Ask the Decisions API, for each pair of skills on a command, whether they are one job."""

    def review(command: str, capabilities: list[Capability]) -> list[dict] | None:
        pairs = [(a, b) for i, a in enumerate(capabilities) for b in capabilities[i + 1 :]]
        text = f"{REVIEW_RULE}\n\nThese skills all run `{command}`:\n{json.dumps(_review_skills(capabilities), indent=1)}"
        questions = [
            {
                "type": "predicate",
                "name": f"same_job_{index}",
                "instructions": f"Do `{a.slug}` and `{b.slug}` give the agent the same kind of result (for example, both rewritten content, or both a measurement), even if format or input differ?",
            }
            for index, (a, b) in enumerate(pairs)
        ]
        try:
            answers = decide(text, questions, cache_dir=cache_dir)
        except ModelError:
            return fallback(command, capabilities) if fallback else None
        probability = {
            a.get("name"): float(a.get("probability") or 0) for a in answers if isinstance(a, dict)
        }
        threshold = float(os.environ.get("ELLICT_SAME_JOB_THRESHOLD") or SAME_JOB_THRESHOLD)
        parent = {c.slug: c.slug for c in capabilities}

        def find(slug: str) -> str:
            while parent[slug] != slug:
                slug = parent[slug]
            return slug

        links = []
        for index, (a, b) in enumerate(pairs):
            p = probability.get(f"same_job_{index}", 0.0)
            if p >= threshold:
                links.append(f"{a.slug} and {b.slug}: p={p:.2f}")
                roots = sorted([find(a.slug), find(b.slug)], key=[c.slug for c in capabilities].index)
                parent[roots[1]] = roots[0]
        groups: dict[str, list[str]] = {}
        for capability in capabilities:
            groups.setdefault(find(capability.slug), []).append(capability.slug)
        why = "Decisions API, same job " + "; ".join(links) if links else ""
        return [{"skills": slugs, "why": why} for slugs in groups.values()]

    return review


def review_prompt(command: str, capabilities: list[Capability]) -> str:
    skills = _review_skills(capabilities)
    shape = {"jobs": [{"skills": ["slugs that are one job; the first is the one to keep"], "why": "the job, in one sentence"}]}
    return (
        f"These skills all run `{command}`:\n{json.dumps(skills, indent=1)}\n\n"
        "Group them into jobs. Every slug appears in exactly one group.\n"
        f"Reply with JSON in exactly this shape:\n{json.dumps(shape, indent=1)}"
    )


def model_review(model: Model, cache_dir: Path) -> Review:
    def review(command: str, capabilities: list[Capability]) -> list[dict] | None:
        try:
            answer, _, _ = ask(model, REVIEW_SYSTEM, review_prompt(command, capabilities), cache_dir=cache_dir)
        except ModelError:
            return None
        jobs = answer.get("jobs")
        return jobs if isinstance(jobs, list) else None

    return review


def _fold(first: Capability, capability: Capability) -> None:
    for example in capability.guidance["examples"]:
        if example not in first.guidance["examples"]:
            first.guidance["examples"].append(example)
            if example["command"]:
                first.examples.append(f"`{example['command']}`")
    paths = {f["path"] for f in first.guidance["fixtures"]}
    first.guidance["fixtures"].extend(f for f in capability.guidance["fixtures"] if f["path"] not in paths)


def _reviewed(group: list[Capability], jobs: list[dict], dropped: list[dict]) -> list[Capability]:
    by_slug = {c.slug: c for c in group}
    heads: list[Capability] = []
    placed: set[str] = set()
    for job in jobs:
        slugs = [s for s in (job.get("skills") or []) if isinstance(s, str) and s in by_slug and s not in placed]
        if not slugs:
            continue
        placed.update(slugs)
        first = by_slug[slugs[0]]
        heads.append(first)
        for slug in slugs[1:]:
            _fold(first, by_slug[slug])
            why = str(job.get("why") or "").strip()
            dropped.append({"slug": slug, "reason": f"same job as {first.slug}; folded in as examples ({why})"})
    heads.extend(c for c in group if c.slug not in placed)
    return sorted(heads, key=group.index)


def _one_skill_per_job(
    kept: list[Capability], raws: dict[str, dict], dropped: list[dict], review: Review | None = None
) -> list[Capability]:
    """Merge same-command skills that are one job, using the model's review when there is one."""
    groups: dict[str, list[Capability]] = {}
    for capability in kept:
        groups.setdefault(capability.invocations[0].command or f"\0{capability.slug}", []).append(capability)
    result: list[Capability] = []
    for command, group in groups.items():
        jobs = review(command, group) if review and len(group) > 1 else None
        if jobs is not None:
            heads = _reviewed(group, jobs, dropped)
        else:
            heads = [group[0]]
            for capability in group[1:]:
                if str(raws[capability.slug].get("distinct_job") or "").strip():
                    heads.append(capability)
                else:
                    _fold(group[0], capability)
                    dropped.append({"slug": capability.slug, "reason": f"a mode of {group[0].slug}; folded in as examples"})
        for capability in heads[MAX_PER_COMMAND:]:
            dropped.append({"slug": capability.slug, "reason": f"more than {MAX_PER_COMMAND} skills on `{command}`"})
        result.extend(heads[:MAX_PER_COMMAND])
    return sorted(result, key=kept.index)


def ground(
    answer: dict, evidence: dict, root: Path, review: Review | None = None
) -> tuple[list[Capability], list[dict]]:
    """Keep model-proposed skills whose invocation, flags, and sources exist in this repository."""
    known = _known(evidence)
    fingerprint = digest(evidence)
    kept: list[Capability] = []
    dropped: list[dict] = []
    seen: set[str] = set()
    raws: dict[str, dict] = {}
    for raw in answer.get("skills") or []:
        if not isinstance(raw, dict):
            continue
        slug = slugify(str(raw.get("slug") or raw.get("title") or ""))
        missing = [k for k in ("description", "worthy", "would_use", "how", "helps") if not str(raw.get(k) or "").strip()]
        if missing:
            dropped.append({"slug": slug, "reason": f"unanswered: {', '.join(missing)}"})
            continue
        if slug in seen:
            dropped.append({"slug": slug, "reason": "duplicate slug"})
            continue
        if _truthy(raw.get("interactive")):
            dropped.append({"slug": slug, "reason": "needs a person at a terminal; an agent cannot operate it"})
            continue
        invocation = raw.get("invocation") or {}
        method = str(invocation.get("method") or "cli").lower()
        if method == "http":
            route = (str(invocation.get("http_method") or "").upper(), str(invocation.get("path") or ""))
            if route not in known["routes"]:
                dropped.append({"slug": slug, "reason": f"route not in the API spec: {route[0]} {route[1]}"})
                continue
            inv = Invocation(method="http", http_method=route[0], path=route[1], example=f"{route[0]} {route[1]}")
            base = None
        else:
            base, error = _ground_command(str(invocation.get("command") or ""), known)
            if not base:
                dropped.append({"slug": slug, "reason": error})
                continue
            inv = Invocation(method="cli", command=base, example=base)
        examples, removed = [], []
        for example in raw.get("examples") or []:
            if not isinstance(example, dict):
                continue
            command = str(example.get("command") or "").strip()
            task = str(example.get("task") or "").strip()
            if base and command:
                reason = problem(command, base.split()[0])
                if reason:
                    removed.append(f"{command} ({reason})")
                    continue
                unknown = _unknown_flags(command, base, known)
                if unknown:
                    removed.append(f"{command} (undocumented {', '.join(unknown)})")
                    continue
            entry = {"task": task, "command": command}
            if str(example.get("exit") or "0").strip().isdigit() and int(example.get("exit") or 0):
                entry["exit"] = int(example["exit"])
            examples.append(entry)
        sources = [s for s in (raw.get("sources") or []) if isinstance(s, str) and (root / s).is_file()]
        guidance = {
            key: raw.get(key)
            for key in (
                "description", "worthy", "would_use", "how", "helps", "demand", "agent_gap",
                "prerequisites", "when_to_use", "when_not_to_use", "steps", "verify", "pitfalls",
            )
        }
        guidance["examples"] = examples
        guidance["fixtures"] = _fixtures(raw)
        guidance["removed_examples"] = removed
        seen.add(slug)
        raws[slug] = raw
        kept.append(
            Capability(
                slug=slug,
                title=str(raw.get("title") or slug),
                summary=str(raw.get("description")),
                kind="agent",
                invocations=[inv],
                prerequisites=[str(p) for p in raw.get("prerequisites") or []],
                examples=[f"`{e['command']}`" for e in examples if e["command"]],
                sources=[f"source:{s}" for s in sources] or ["agent:evidence"],
                fingerprint=hashlib.sha256(f"{fingerprint}:{slug}".encode()).hexdigest()[:16],
                guidance=guidance,
            )
        )
    return _one_skill_per_job(kept, raws, dropped, review), dropped


def write_prompt(root: Path) -> Path:
    """Write the selection prompt for an agent to answer itself, instead of calling a model API."""
    root = root.resolve()
    evidence = gather(root)
    out = root / ".ellict" / "prompt.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"{SYSTEM}\n\n{prompt(evidence)}\n", encoding="utf-8")
    return out


def select(root: Path, model: Model) -> tuple[list[Capability], dict]:
    """Ask the model, ground its answer, and return capabilities plus the full selection record."""
    root = root.resolve()
    evidence = gather(root)
    if not evidence["docs"] and not evidence["inventory"]:
        raise ModelError("no documentation or interface found to read")
    cache_dir = root / ".ellict" / "model"
    answer, key, cached = ask(model, SYSTEM, prompt(evidence), cache_dir=cache_dir)
    review = None
    if model.provider != "answer":
        review = model_review(model, cache_dir)
        if os.environ.get("OPENAI_API_KEY") and os.environ.get("ELLICT_DECISIONS") == "1":
            review = decisions_review(cache_dir, fallback=review)
    kept, dropped = ground(answer, evidence, root, review)
    record = {
        "model": model.spec,
        "cache_key": key,
        "cached": cached,
        "evidence": {
            "digest": digest(evidence),
            "docs": [d["path"] for d in evidence["docs"]],
            "manifests": [m["path"] for m in evidence["manifests"]],
            "help": sorted(evidence["help"]),
            "inventory": [i["slug"] for i in evidence["inventory"]],
        },
        "kept": [c.slug for c in kept],
        "grounding_dropped": dropped,
        "model_rejected": [r for r in answer.get("rejected") or [] if isinstance(r, dict)],
        "answers": {c.slug: c.guidance for c in kept},
    }
    out = root / ".ellict" / "selection.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return kept, record
