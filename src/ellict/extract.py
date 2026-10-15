"""Inspect a repository for capabilities an agent can actually invoke."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ellict.models import Capability, Invocation

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".ellict",
    "dist",
    "build",
    ".cursor",
    ".agents",
    "target",
    "vendor",
}
DOC_SUFFIXES = {".md", ".markdown", ".mkd", ".rst"}
PROMPT_PREFIX = re.compile(r"^[\$>▶%]\s*")

CLI_PARSER = re.compile(
    r"""add_parser\(\s*['"]([a-zA-Z0-9][\w-]*)['"](?:[^)]*?help\s*=\s*['"]([^'"]*)['"])?""",
    re.DOTALL,
)
PROG = re.compile(r"""ArgumentParser\(\s*[^)]*prog\s*=\s*['"]([^'"]+)['"]""")
TYPER_CMD = re.compile(
    r"""@\w+\.command\(\)\s*(?:@\w+\.\w+\([^)]*\)\s*)*def\s+([a-zA-Z_][\w]*)\s*\([^)]*\)\s*(?::\s*[^:]+?)?:\s*(?:\"\"\"(.*?)\"\"\"|'''(.*?)''')?""",
    re.DOTALL,
)
FENCE = re.compile(r"```(?:bash|sh|shell|console)?\n(.*?)```", re.DOTALL)
HEADING = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
MAKE_TARGET = re.compile(r"^([a-zA-Z][\w-]*)\s*:.*?(?:##\s*(.+))?$", re.MULTILINE)


def slugify(value: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", value.strip())
    text = text.lower().replace("_", "-")
    text = re.sub(r"[^a-z0-9-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:64] or "capability"


def _fingerprint(parts: list[str]) -> str:
    digest = hashlib.sha256("\n".join(parts).encode()).hexdigest()
    return digest[:16]


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _walk(root: Path):
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            yield path


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def _app_name(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        match = re.search(r'(?m)^name\s*=\s*"([^"]+)"', _read(pyproject))
        if match:
            return slugify(match.group(1))
    package = root / "package.json"
    if package.exists():
        try:
            data = json.loads(_read(package))
            if isinstance(data.get("name"), str):
                return slugify(data["name"])
        except json.JSONDecodeError:
            pass
    return slugify(root.name)


SCRIPT_ENTRY = re.compile(r'(?m)^([a-zA-Z0-9][\w-]*)\s*=\s*"([^"]+)"')
CLICK_OPTION = re.compile(
    r"""@click\.option\(\s*["'](--[a-zA-Z0-9][\w-]*)["'][^)]*help\s*=\s*["']([^"']+)["']""",
    re.DOTALL,
)


def _scripts(root: Path) -> list[Capability]:
    pyproject = root / "pyproject.toml"
    text = _read(pyproject) if pyproject.exists() else ""
    headers = (
        r"\[project\.scripts\]",
        r"\[project\.entry-points\.console_scripts\]",
        r"\[tool\.poetry\.scripts\]",
    )
    found = []
    for header in headers:
        section = re.search(rf"(?ms)^{header}\s*(.*?)(?=^\[|\Z)", text)
        if not section:
            continue
        found.extend(_script_entries(section.group(1)))
    setup = root / "setup.py"
    if setup.exists():
        block = re.search(r"(?s)\[console_scripts\](.*?)(?:\"\"\"|''')", _read(setup))
        if block:
            for name, target in re.findall(r"(?m)^\s*([a-zA-Z0-9][\w-]*)\s*=\s*([\w.]+:[\w.]+)", block.group(1)):
                found.extend(_script_entries(f'{name} = "{target}"\n'))
    return found


def _binary(name: str, manifest: str, summary: str, target: str) -> Capability:
    return Capability(
        slug=slugify(name),
        title=f"Run {name}",
        summary=summary,
        kind="cli",
        invocations=[Invocation(method="cli", command=name, example=f"{name} --help")],
        prerequisites=[f"`{name}` is available on PATH"],
        examples=[f"`{name} --help`"],
        sources=[f"cli:{manifest}"],
        fingerprint=_fingerprint([manifest, name, target]),
    )


def _script_entries(section: str) -> list[Capability]:
    return [
        _binary(name, "pyproject.toml", f"Console script {name} ({target})", target)
        for name, target in SCRIPT_ENTRY.findall(section)
    ]


def node_bins(root: Path) -> dict[str, str]:
    """Map each command in package.json ``bin`` to the file it runs."""
    package = root / "package.json"
    if not package.is_file():
        return {}
    try:
        data = json.loads(_read(package))
    except json.JSONDecodeError:
        return {}
    bin_field = data.get("bin")
    if isinstance(bin_field, str) and isinstance(data.get("name"), str):
        return {data["name"].split("/")[-1]: bin_field}
    if isinstance(bin_field, dict):
        return {k: v for k, v in bin_field.items() if isinstance(v, str)}
    return {}


def _node(root: Path) -> list[Capability]:
    return [
        _binary(name, "package.json", f"Node command {name} ({target})", target)
        for name, target in node_bins(root).items()
    ]


PACKAGE_MAIN = re.compile(r"(?m)^package\s+main\b")


def _go_main(directory: Path) -> bool:
    return any(
        PACKAGE_MAIN.search(_read(path))
        for path in directory.glob("*.go")
        if not path.name.endswith("_test.go")
    )


def _go(root: Path) -> list[Capability]:
    gomod = root / "go.mod"
    if not gomod.is_file():
        return []
    module = re.search(r"(?m)^module\s+(\S+)", _read(gomod))
    if not module:
        return []
    found = []
    parts = module.group(1).split("/")
    if len(parts) > 1 and re.fullmatch(r"v\d+", parts[-1]):
        parts = parts[:-1]
    if _go_main(root):
        found.append(_binary(parts[-1], "go.mod", f"Go command {parts[-1]} ({module.group(1)})", module.group(1)))
    cmd = root / "cmd"
    if cmd.is_dir():
        for directory in sorted(p for p in cmd.iterdir() if p.is_dir() and _go_main(p)):
            target = f"{module.group(1)}/cmd/{directory.name}"
            found.append(_binary(directory.name, _rel(root, directory), f"Go command {directory.name} ({target})", target))
    return found


def _toml_section(text: str, header: str) -> list[str]:
    return [m.group(1) for m in re.finditer(rf"(?ms)^{header}[ \t]*\n(.*?)(?=^\[|\Z)", text)]


def _toml_name(section: str) -> str | None:
    match = re.search(r'(?m)^name\s*=\s*"([^"]+)"', section)
    return match.group(1) if match else None


def _cargo(root: Path) -> list[Capability]:
    found = []
    for manifest in sorted(_walk(root)):
        if manifest.name != "Cargo.toml":
            continue
        text = _read(manifest)
        crate = manifest.parent
        sections = _toml_section(text, r"\[package\]")
        package = next((_toml_name(s) for s in sections), None)
        if package == "xtask" or any(re.search(r"(?m)^publish\s*=\s*false", s) for s in sections):
            continue
        names = [n for n in (_toml_name(s) for s in _toml_section(text, r"\[\[bin\]\]")) if n]
        if not names and package and (crate / "src" / "main.rs").is_file():
            names.append(package)
        names.extend(p.stem for p in sorted((crate / "src" / "bin").glob("*.rs")))
        source = _rel(root, manifest)
        for name in dict.fromkeys(names):
            found.append(_binary(name, source, f"Rust command {name} ({package or crate.name})", f"{source}:{name}"))
    return found


def _click_options(root: Path, existing: dict[str, Capability]) -> None:
    for path in _walk(root):
        if path.suffix != ".py" or "click.option" not in _read(path):
            continue
        text = _read(path)
        source = _rel(root, path)
        options = CLICK_OPTION.findall(text)
        if not options:
            continue
        for capability in existing.values():
            if capability.kind != "cli":
                continue
            for flag, help_text in options:
                example = f"`{capability.invocations[0].command} {flag}`"
                if example not in capability.examples and len(capability.examples) < 8:
                    capability.examples.append(example)
            ref = f"cli:{source}"
            if ref not in capability.sources:
                capability.sources.append(ref)


def _cli_from_python(root: Path, app: str) -> list[Capability]:
    found: list[Capability] = []
    for path in _walk(root):
        if path.suffix != ".py":
            continue
        text = _read(path)
        if "add_parser" not in text and ".command(" not in text:
            continue
        prog = PROG.search(text)
        binary = prog.group(1) if prog else app
        source = _rel(root, path)
        for match in CLI_PARSER.finditer(text):
            command, help_text = match.group(1), match.group(2) or f"Run {binary} {match.group(1)}"
            slug = slugify(f"{binary}-{command}")
            invocation = f"{binary} {command}"
            found.append(
                Capability(
                    slug=slug,
                    title=help_text.strip().rstrip("."),
                    summary=help_text.strip(),
                    kind="cli",
                    invocations=[Invocation(method="cli", command=invocation, example=invocation)],
                    prerequisites=[f"`{binary}` is available on PATH"],
                    examples=[f"`{invocation}`"],
                    sources=[f"cli:{source}"],
                    fingerprint=_fingerprint([source, invocation, help_text]),
                )
            )
        for match in TYPER_CMD.finditer(text):
            name = match.group(1).replace("_", "-")
            doc = (match.group(2) or match.group(3) or f"Run {binary} {name}").strip().split("\n")[0]
            slug = slugify(f"{binary}-{name}")
            invocation = f"{binary} {name}"
            if any(item.slug == slug for item in found):
                continue
            found.append(
                Capability(
                    slug=slug,
                    title=doc.rstrip("."),
                    summary=doc,
                    kind="cli",
                    invocations=[Invocation(method="cli", command=invocation, example=invocation)],
                    prerequisites=[f"`{binary}` is available on PATH"],
                    examples=[f"`{invocation}`"],
                    sources=[f"cli:{source}"],
                    fingerprint=_fingerprint([source, invocation, doc]),
                )
            )
    return found


def _commands_in_fence(body: str) -> list[str]:
    commands = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        stripped = PROMPT_PREFIX.sub("", stripped)
        if stripped:
            commands.append(stripped)
    return commands


def _docs(root: Path, existing: dict[str, Capability]) -> list[Capability]:
    extra: list[Capability] = []
    for path in _walk(root):
        if path.suffix.lower() not in DOC_SUFFIXES:
            continue
        text = _read(path)
        if "```" not in text:
            continue
        source = _rel(root, path)
        headings = [(m.start(), m.group(1).strip()) for m in HEADING.finditer(text)]
        for fence in FENCE.finditer(text):
            commands = _commands_in_fence(fence.group(1))
            if not commands:
                continue
            title = "Documented command"
            for pos, heading in headings:
                if pos < fence.start():
                    title = heading
            for command in commands:
                binary = command.split()[0]
                slug = slugify(f"{binary}-{title}") if len(command.split()) == 1 else slugify(command)
                matched = _match_existing(existing, command)
                if matched:
                    if f"`{command}`" not in matched.examples and len(matched.examples) < 8:
                        matched.examples.append(f"`{command}`")
                    ref = f"docs:{source}"
                    if ref not in matched.sources:
                        matched.sources.append(ref)
                    matched.fingerprint = _fingerprint(
                        [matched.fingerprint, command, source]
                    )
                    continue
    return []


def _match_existing(existing: dict[str, Capability], command: str) -> Capability | None:
    for capability in existing.values():
        for invocation in capability.invocations:
            if invocation.command and command.startswith(invocation.command):
                return capability
    return None


def _openapi_json(root: Path, path: Path) -> list[Capability]:
    try:
        spec = json.loads(_read(path))
    except json.JSONDecodeError:
        return []
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return []
    source = _rel(root, path)
    found = []
    for route, operations in paths.items():
        if not isinstance(operations, dict):
            continue
        for method, operation in operations.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "head"}:
                continue
            if not isinstance(operation, dict):
                continue
            op_id = str(operation.get("operationId") or f"{method}-{route}")
            summary = str(operation.get("summary") or op_id)
            slug = slugify(op_id)
            example = f"{method.upper()} {route}"
            found.append(
                Capability(
                    slug=slug,
                    title=summary.rstrip("."),
                    summary=summary,
                    kind="api",
                    invocations=[
                        Invocation(
                            method="http",
                            http_method=method.upper(),
                            path=str(route),
                            example=example,
                        )
                    ],
                    prerequisites=["API base URL is configured"],
                    examples=[f"`{example}`"],
                    sources=[f"openapi:{source}"],
                    fingerprint=_fingerprint([source, method, str(route), summary]),
                )
            )
    return found


def _openapi_yaml(root: Path, path: Path) -> list[Capability]:
    text = _read(path)
    if "paths:" not in text:
        return []
    source = _rel(root, path)
    section = text.split("paths:", 1)[1]
    found: list[Capability] = []
    current_path = None
    current_method = None
    summary = None
    op_id = None

    def flush() -> None:
        nonlocal summary, op_id, current_method
        if not current_path or not current_method:
            return
        title = (summary or op_id or f"{current_method} {current_path}").strip().strip("'\"")
        slug = slugify(op_id or f"{current_method}-{current_path}")
        example = f"{current_method.upper()} {current_path}"
        found.append(
            Capability(
                slug=slug,
                title=title.rstrip("."),
                summary=title,
                kind="api",
                invocations=[
                    Invocation(
                        method="http",
                        http_method=current_method.upper(),
                        path=current_path,
                        example=example,
                    )
                ],
                prerequisites=["API base URL is configured"],
                examples=[f"`{example}`"],
                sources=[f"openapi:{source}"],
                fingerprint=_fingerprint([source, current_method, current_path, title]),
            )
        )
        summary = None
        op_id = None

    for line in section.splitlines():
        path_match = re.match(r"^\s{2}(/[^:]*):\s*$", line)
        method_match = re.match(r"^\s{4}(get|post|put|patch|delete|head):\s*$", line, re.I)
        if path_match:
            flush()
            current_path = path_match.group(1)
            current_method = None
            continue
        if method_match:
            flush()
            current_method = method_match.group(1)
            continue
        summary_match = re.match(r"^\s+summary:\s*(.+)$", line)
        op_match = re.match(r"^\s+operationId:\s*(.+)$", line)
        if summary_match:
            summary = summary_match.group(1).strip().strip("'\"")
        elif op_match:
            op_id = op_match.group(1).strip().strip("'\"")
    flush()
    return found


def _openapi(root: Path) -> list[Capability]:
    found: list[Capability] = []
    names = {"openapi.json", "openapi.yaml", "openapi.yml", "swagger.json", "swagger.yaml"}
    for path in _walk(root):
        if path.name not in names and "openapi" not in path.name.lower():
            continue
        if path.suffix == ".json":
            found.extend(_openapi_json(root, path))
        elif path.suffix in {".yaml", ".yml"}:
            found.extend(_openapi_yaml(root, path))
    return found


def _make(root: Path) -> list[Capability]:
    makefile = root / "Makefile"
    if not makefile.exists():
        return []
    found = []
    for match in MAKE_TARGET.finditer(_read(makefile)):
        target, help_text = match.group(1), (match.group(2) or f"Run make {match.group(1)}").strip()
        if target in {".PHONY", "all", "install", "test", "lint", "format", "fmt", "clean", "build", "ci", "check"}:
            continue
        command = f"make {target}"
        found.append(
            Capability(
                slug=slugify(command),
                title=help_text.rstrip("."),
                summary=help_text,
                kind="make",
                invocations=[Invocation(method="cli", command=command, example=command)],
                prerequisites=["GNU make is available"],
                examples=[f"`{command}`"],
                sources=["make:Makefile"],
                fingerprint=_fingerprint(["Makefile", command, help_text]),
            )
        )
    return found


def _dedupe(items: list[Capability]) -> list[Capability]:
    ordered: dict[str, Capability] = {}
    for item in items:
        current = ordered.get(item.slug)
        if current is None:
            ordered[item.slug] = item
            continue
        for source in item.sources:
            if source not in current.sources:
                current.sources.append(source)
        for example in item.examples:
            if example not in current.examples:
                current.examples.append(example)
    return list(ordered.values())


def extract(root: Path) -> list[Capability]:
    """Return capabilities discovered under ``root``."""
    root = root.resolve()
    app = _app_name(root)
    cli = _dedupe([*_scripts(root), *_node(root), *_go(root), *_cargo(root), *_cli_from_python(root, app)])
    by_slug = {item.slug: item for item in cli}
    _click_options(root, by_slug)
    docs = _docs(root, by_slug)
    capabilities = _dedupe([*cli, *docs, *_openapi(root), *_make(root)])
    capabilities.sort(key=lambda item: item.slug)
    return capabilities
