"""Static-rules selector, used when no model is configured."""

import unittest

from ellict.extract import extract
from ellict.pipeline import run

from helpers import fresh


class RulesTest(unittest.TestCase):
    def test_finds_commands_routes_and_named_tasks(self) -> None:
        app = fresh("rules-extract")
        slugs = {item.slug for item in extract(app)}
        self.assertTrue({"ledger-add", "ledger-list", "create-entry", "list-entries", "make-report"} <= slugs)

    def test_install_lines_and_chores_are_not_skills(self) -> None:
        app = fresh("rules-noise")
        (app / "README.md").write_text(
            "# Ledger\n\n```bash\nbrew install ledger\npip install ledger\nledger add milk\n```\n"
        )
        (app / "Makefile").write_text("test: ## Run tests\n\tpytest\nreport: ## Summarize\n\techo ok\n")
        slugs = {item.slug for item in extract(app)}
        self.assertFalse(any(slug.startswith(("brew", "pip")) for slug in slugs), slugs)
        self.assertNotIn("make-test", slugs)
        self.assertIn("make-report", slugs)

    def test_finds_node_go_and_rust_commands(self) -> None:
        app = fresh("rules-languages")
        for name in ("ledger.py", "pyproject.toml", "openapi.yaml"):
            (app / name).unlink()
        (app / "package.json").write_text('{"name": "@acme/tally", "bin": "cli.js"}')
        (app / "go.mod").write_text("module github.com/acme/gotally/v2\n\ngo 1.22\n")
        (app / "main.go").write_text("package main\n\nfunc main() {}\n")
        (app / "cmd" / "tally-sync").mkdir(parents=True)
        (app / "cmd" / "tally-sync" / "main.go").write_text("package main\n")
        (app / "Cargo.toml").write_text('[workspace]\nmembers = ["cli", "xtask", "dev"]\n')
        for crate, manifest in {
            "cli": '[package]\nname = "tally-cli"\n\n[[bin]]\nname = "rtally"\npath = "src/main.rs"\n',
            "xtask": '[package]\nname = "xtask"\n',
            "dev": '[package]\nname = "dev-tool"\npublish = false\n',
        }.items():
            (app / crate / "src").mkdir(parents=True)
            (app / crate / "Cargo.toml").write_text(manifest)
            (app / crate / "src" / "main.rs").write_text("fn main() {}\n")
        commands = {item.invocations[0].command for item in extract(app) if item.kind == "cli"}
        self.assertTrue({"tally", "gotally", "tally-sync", "rtally"} <= commands, commands)
        self.assertFalse({"xtask", "dev-tool"} & commands, commands)

    def test_rules_run_validates_and_publishes(self) -> None:
        app = fresh("rules-run")
        result = run(app, select="rules")
        self.assertEqual(result["selector"], "rules")
        self.assertIn("ledger-add", result["approved"])
        self.assertTrue((app / ".cursor" / "skills" / "ledger-add" / "SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
