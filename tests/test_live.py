"""Public applications in Python, Node, Go, and Rust, installed for real, run through the whole flow.

The rules baseline always runs. The model run happens when a model is configured
(ANTHROPIC_API_KEY, OPENAI_API_KEY, or ELLICT_MODEL), is scored against the jobs in
bench/gold.json, and records results in .ellict/bench/.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from ellict.evidence import load
from ellict.llm import from_env, load_dotenv
from ellict.pipeline import run
from ellict.source import resolve

from gold import GOLD, score, skill_examples
from helpers import ROOT

load_dotenv(ROOT / ".env", ROOT / "agent" / ".env")

VENVS = ROOT / ".ellict" / "venvs"
TOOLCHAINS = ROOT / ".ellict" / "toolchains"
RESULTS = ROOT / ".ellict" / "bench"
NEEDS = {"python": None, "node": "npm", "go": "go", "rust": "cargo"}

if (TOOLCHAINS / "cargo" / "bin" / "cargo").exists():
    os.environ["PATH"] = os.pathsep.join([str(TOOLCHAINS / "cargo" / "bin"), os.environ.get("PATH", "")])
    os.environ.setdefault("RUSTUP_HOME", str(TOOLCHAINS / "rustup"))


def _run(argv: list[str], *, cwd: Path | None = None, env: dict | None = None) -> None:
    subprocess.run(argv, cwd=cwd, env={**os.environ, **(env or {})}, check=True, capture_output=True, text=True)


def _install(name: str, project: dict, checkout: Path) -> Path:
    prefix = VENVS / name
    marker = prefix / ".installed"
    if marker.exists():
        return prefix / "bin"
    if prefix.exists():
        shutil.rmtree(prefix)
    source = checkout / project.get("path", ".")
    kind = project["install"]
    if kind == "python":
        _run([sys.executable, "-m", "venv", str(prefix)])
        _run([str(prefix / "bin" / "pip"), "install", "--quiet", str(source)])
    elif kind == "node":
        prefix.mkdir(parents=True)
        packed = subprocess.run(
            ["npm", "pack", "--silent", "--pack-destination", str(prefix), str(source)],
            check=True, capture_output=True, text=True,
        ).stdout.strip().splitlines()[-1]
        _run(["npm", "install", "--global", "--silent", "--prefix", str(prefix), str(prefix / packed)])
    elif kind == "go":
        _run(
            ["go", "install", "."],
            cwd=source,
            env={
                "GOBIN": str(prefix / "bin"),
                "GOPATH": str(TOOLCHAINS / "gopath"),
                "GOCACHE": str(TOOLCHAINS / "gocache"),
                "GOFLAGS": "-modcacherw",
            },
        )
    elif kind == "rust":
        _run(
            ["cargo", "install", "--quiet", "--locked", "--path", str(source), "--root", str(prefix)],
            env={"CARGO_HOME": str(TOOLCHAINS / "cargo"), "CARGO_TARGET_DIR": str(TOOLCHAINS / "target")},
        )
    else:
        raise ValueError(f"unknown install kind {kind!r}")
    marker.write_text("ok\n")
    return prefix / "bin"


class LiveBench(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.roots: dict[str, Path] = {}
        cls.install_errors: dict[str, str] = {}
        paths = []
        for name, project in GOLD["projects"].items():
            tool = NEEDS[project["install"]]
            if tool and not shutil.which(tool):
                cls.install_errors[name] = f"{tool} is not installed"
                continue
            root = resolve(project["url"], workspace=ROOT)
            cls.roots[name] = root
            try:
                paths.append(str(_install(name, project, root)))
            except subprocess.CalledProcessError as exc:
                cls.install_errors[name] = (exc.stderr or exc.stdout or str(exc))[-800:]
        cls._path = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join([*paths, cls._path])
        RESULTS.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls) -> None:
        os.environ["PATH"] = cls._path

    def _skills(self, root: Path, approved: list[str]) -> dict[str, str]:
        return {
            slug: (root / ".ellict" / "catalog" / slug / "SKILL.md").read_text(encoding="utf-8")
            for slug in approved
        }

    def _check(self, name: str, result: dict) -> dict:
        project = GOLD["projects"][name]
        root = self.roots[name]
        skills = self._skills(root, result["approved"])
        commands = {}
        for slug, text in skills.items():
            block = text.split("## Invocation", 1)[1].split("```bash\n", 1)
            if len(block) == 2:
                commands[slug] = block[1].split("\n", 1)[0].strip()
        binaries = {command.split()[0] for command in commands.values()}
        per_command: dict[str, int] = {}
        for command in commands.values():
            per_command[command] = per_command.get(command, 0) + 1
        headless = {}
        for event in load(root):
            if event["phase"] == "headless" and event["slug"] in skills:
                headless[event["slug"]] = sum(1 for r in event["runs"] if r["ok"])
        return {
            "project": name,
            "selector": result["selector"],
            "selector_reason": result["selector_reason"],
            "approved": result["approved"],
            "rejected_by_validation": result["rejected"],
            "commands": commands,
            "skills_per_command": per_command,
            "crowded_commands": {c: n for c, n in per_command.items() if n > GOLD["max_skills_per_command"]},
            "examples_that_ran": headless,
            "skills_without_a_running_example": [slug for slug in skills if not headless.get(slug)],
            "gold_keep": project["keep"],
            "gold_hit": [keep for keep in project["keep"] if keep in binaries],
            "never_published": sorted(b for b in binaries if b in GOLD["never"]),
            "score": score(project, {slug: skill_examples(text) for slug, text in skills.items()}),
            "selection": result.get("selection"),
        }

    def test_1_rules_baseline(self) -> None:
        self.assertEqual(self.install_errors, {}, self.install_errors)
        rows = []
        for name, root in self.roots.items():
            with self.subTest(project=name):
                row = self._check(name, run(root, select="rules"))
                rows.append(row)
                self.assertEqual(row["gold_hit"], row["gold_keep"], row)
                self.assertEqual(row["never_published"], [], row)
                self.assertEqual(row["skills_without_a_running_example"], [], row)
        (RESULTS / "rules.json").write_text(json.dumps(rows, indent=2) + "\n")

    @unittest.skipUnless(from_env(), "no model configured: set ANTHROPIC_API_KEY, OPENAI_API_KEY, or ELLICT_MODEL")
    def test_2_model_selection(self) -> None:
        self.assertEqual(self.install_errors, {}, self.install_errors)
        rows = []
        for name, root in self.roots.items():
            with self.subTest(project=name):
                result = run(root, select="agent")
                row = self._check(name, result)
                rows.append(row)
                self.assertEqual(row["never_published"], [], row)
                self.assertEqual(row["crowded_commands"], {}, row)
                self.assertEqual(row["skills_without_a_running_example"], [], row)
                self.assertEqual(row["score"]["missing_jobs"], [], row)
                self.assertEqual(row["score"]["split_jobs"], {}, row)
                if not GOLD["projects"][name]["jobs"]:
                    self.assertEqual(row["approved"], [], row)
                for slug in result["approved"]:
                    why = root / ".ellict" / "catalog" / slug / "references" / "selection.md"
                    self.assertTrue(why.exists(), slug)
        (RESULTS / "agent.json").write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    unittest.main()
