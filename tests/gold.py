"""Score published skills against the jobs in bench/gold.json."""

import json
import re
from collections import Counter
from pathlib import Path

from helpers import ROOT

GOLD = json.loads((ROOT / "bench" / "gold.json").read_text(encoding="utf-8"))
EXAMPLE = re.compile(r"`([^`]+)`")


def skill_examples(skill_md: str) -> list[str]:
    section = skill_md.split("## Examples", 1)[-1].split("\n## ", 1)[0]
    return EXAMPLE.findall(section)


def job_of(command: str, project: dict) -> str | None:
    if project.get("ignore"):
        command = re.sub(project["ignore"], " ", command)
    for job in project["jobs"]:
        if re.search(job["match"], command):
            return job["job"]
    return None


def score(project: dict, skills: dict[str, list[str]]) -> dict:
    """``skills`` maps each published slug to its example commands."""
    order = [job["job"] for job in project["jobs"]]
    required = [job["job"] for job in project["jobs"] if job.get("required", True)]
    jobs: dict[str, str | None] = {}
    for slug, commands in skills.items():
        counts = Counter(job for job in (job_of(c, project) for c in commands) if job)
        rank = lambda job: (counts[job], job in required, -order.index(job))  # noqa: E731
        jobs[slug] = max(counts, key=rank) if counts else None
    by_job: dict[str, list[str]] = {}
    for slug, job in jobs.items():
        if job:
            by_job.setdefault(job, []).append(slug)
    return {
        "jobs": jobs,
        "missing_jobs": [job for job in required if job not in by_job],
        "split_jobs": {job: slugs for job, slugs in by_job.items() if len(slugs) > 1},
        "extra_skills": [slug for slug, job in jobs.items() if job is None],
    }
