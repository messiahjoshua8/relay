"""Distribution targets. Skill registries index a public git tree; MCP server registries do not accept SKILL.md."""

from __future__ import annotations

import json
from pathlib import Path

from ellict.models import SkillPackage

# Places that host or route agent skills. Admission is how a contributor gets a skill listed.
REGISTRIES = (
    {"id": "clawhub.ai", "kind": "skill-registry", "admission": "contribute", "detail": "OpenClaw registry. `clawhub skill publish` after login."},
    {"id": "skills.sh", "kind": "skill-registry", "admission": "install", "detail": "Public git repo plus `npx skills add`. Search indexing is a request to the skills repo."},
    {"id": "smithery.ai", "kind": "skill-registry", "admission": "contribute", "detail": "Submit the git URL; they index and host the listing."},
    {"id": "skillsmp.com", "kind": "directory", "admission": "crawl", "detail": "Crawls GitHub repos tagged as agent skills."},
    {"id": "skillhub.club", "kind": "directory", "admission": "contribute", "detail": "Upload a SKILL.md and they host the listing."},
    {"id": "claudeskills.info", "kind": "directory", "admission": "contribute", "detail": "Free submission form."},
    {"id": "mcpmarket.com", "kind": "directory", "admission": "contribute", "detail": "MCP marketplace with an agent-skills section."},
    {"id": "lobehub.com", "kind": "directory", "admission": "contribute", "detail": "Submit the repository."},
    {"id": "mcp.directory", "kind": "directory", "admission": "contribute", "detail": "Submit the repository."},
    {"id": "aiagentsdirectory.com", "kind": "directory", "admission": "contribute", "detail": "Submission form."},
    {"id": "claudemarketplaces.com", "kind": "directory", "admission": "crawl", "detail": "Indexes public GitHub skills."},
    {"id": "claude-plugins.dev", "kind": "directory", "admission": "crawl", "detail": "Indexes public GitHub skills and plugins."},
    {"id": "agentskills.so", "kind": "router", "admission": "crawl", "detail": "Routes to skills already listed on skills.sh."},
    {"id": "skillsplayground.com", "kind": "directory", "admission": "contribute", "detail": "Submit the skill and a README badge."},
    {"id": "tonsofskills.com", "kind": "directory", "admission": "crawl", "detail": "Crawls public GitHub skills."},
    {"id": "registry.modelcontextprotocol.io", "kind": "mcp-server-registry", "admission": "contribute", "detail": "Hosts MCP servers via mcp-publisher, not SKILL.md packages."},
    {"id": "glama.ai", "kind": "mcp-server-registry", "admission": "crawl", "detail": "Indexes MCP servers."},
    {"id": "mcp.so", "kind": "mcp-server-registry", "admission": "crawl", "detail": "Indexes MCP servers."},
    {"id": "pulsemcp.com", "kind": "mcp-server-registry", "admission": "crawl", "detail": "Catalog of MCP servers and clients."},
    {"id": "hub.docker.com/mcp", "kind": "mcp-server-registry", "admission": "contribute", "detail": "Docker-hosted MCP servers."},
)

# Registries that list SKILL.md packages. Both discover skills from a public git repository.
# skills.sh has no upload API: `npx skills add owner/repo`.
# Smithery indexes gitUrl and serves search at registry.smithery.ai; publishing an account listing needs auth.
SKILL_REGISTRIES = ("skills.sh", "smithery")

# Directories that host MCP servers, not skill packages. Publishing a SKILL.md here is not a supported operation.
SERVER_REGISTRIES = (
    {
        "id": "registry.modelcontextprotocol.io",
        "api": "https://registry.modelcontextprotocol.io/v0.1/servers",
        "status": "not-a-skill-registry",
        "detail": "Official MCP registry publishes server manifests, not SKILL.md packages.",
    },
    {
        "id": "glama.ai",
        "api": "https://glama.ai/mcp",
        "status": "not-a-skill-registry",
        "detail": "Glama indexes MCP servers. No public API accepts a skill directory.",
    },
    {
        "id": "mcp.so",
        "api": "https://mcp.so/",
        "status": "not-a-skill-registry",
        "detail": "mcp.so lists MCP servers and has no skill publish API.",
    },
    {
        "id": "pulsemcp.com",
        "api": "https://www.pulsemcp.com/",
        "status": "not-a-skill-registry",
        "detail": "PulseMCP catalogs MCP servers and clients, not skill packages.",
    },
    {
        "id": "hub.docker.com/mcp",
        "api": "https://hub.docker.com/mcp",
        "status": "not-a-skill-registry",
        "detail": "Docker MCP Catalog publishes containerized MCP servers.",
    },
)


def _copy_skill(directory: Path, package: SkillPackage) -> None:
    skill = directory / package.slug
    skill.mkdir(parents=True, exist_ok=True)
    for relative, contents in package.files.items():
        target = skill / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")


def write_registry_bundle(
    root: Path,
    packages: list[SkillPackage],
    *,
    repository: str | None,
) -> list[dict]:
    """Write the git layout skill registries index, plus an explicit skip for server registries."""
    base = root / ".ellict" / "distribution"
    repo = repository or "<owner>/<repo>"
    released = []
    for registry, folder in (("skills.sh", "skills.sh"), ("smithery", "smithery")):
        registry_root = base / folder
        registry_root.mkdir(parents=True, exist_ok=True)
        for package in packages:
            _copy_skill(registry_root, package)
            if registry == "skills.sh":
                install = f"npx skills add {repo} --skill {package.slug}"
            else:
                install = f"https://smithery.ai/skills?q={package.slug}"
            released.append(
                {
                    "slug": package.slug,
                    "fingerprint": package.fingerprint,
                    "destination": registry,
                    "format": "skill-directory",
                    "install": install,
                    "status": "prepared",
                    "path": f".ellict/distribution/{folder}/{package.slug}",
                }
            )
    document = {
        "repository": repo,
        "skillRegistries": [
            {
                "id": "skills.sh",
                "hosts": "skills",
                "status": "prepared",
                "mechanism": "Push this tree to a public GitHub repository. skills.sh has no upload API; installs are `npx skills add`.",
                "path": ".ellict/distribution/skills.sh",
                "install": f"npx skills add {repo}",
                "api": "https://www.skills.sh/docs/api",
            },
            {
                "id": "smithery",
                "hosts": "skills",
                "status": "prepared",
                "mechanism": "Smithery indexes a public git URL. Search is open; creating the account listing requires a Smithery login.",
                "path": ".ellict/distribution/smithery",
                "search": "https://registry.smithery.ai/skills",
            },
        ],
        "registries": list(REGISTRIES),
        "serverRegistries": list(SERVER_REGISTRIES),
        "skills": [
            {
                "slug": package.slug,
                "fingerprint": package.fingerprint,
                "skillsSh": f"npx skills add {repo} --skill {package.slug}",
            }
            for package in packages
        ],
    }
    base.mkdir(parents=True, exist_ok=True)
    (base / "registries.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return released
