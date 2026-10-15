"""Full flow with a model in the loop: read, select, ground, package, validate, publish."""

import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from ellict.cli import main
from ellict.evidence import load
from ellict.llm import Model
from ellict.pipeline import SelectionError, run

from helpers import FIXTURES, fresh, scripted_command, without_model_env


class AgentFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = fresh(self.id().split(".")[-1])
        self.log = self.app.parent / "model-log"
        self.env = mock.patch.dict(
            os.environ,
            {
                **without_model_env(),
                "SCRIPTED_MODEL_LOG": str(self.log),
                "SCRIPTED_MODEL_REPLY": str(FIXTURES / "ledger_reply.json"),
            },
            clear=True,
        )
        self.env.start()
        self.model = Model("command", "scripted", command=scripted_command())

    def tearDown(self) -> None:
        self.env.stop()

    def calls(self) -> int:
        return len(list(self.log.glob("prompt-*.txt")))

    def test_model_reads_selects_and_ellict_packages_and_publishes(self) -> None:
        owned = self.app.parent / "owned"
        result = run(self.app, select="agent", model=self.model, publish_to=owned)

        self.assertEqual(result["selector"], "agent")
        self.assertEqual(result["approved"], ["create-entry", "record-ledger-entries"])
        self.assertEqual(result["rejected"], [])

        prompt = (self.log / "prompt-1.txt").read_text(encoding="utf-8")
        for question in (
            "What can you see here that is worthy of becoming a skill?",
            "What would you actually use?",
            "How would you use it?",
            "What do you think it would help you do?",
        ):
            self.assertIn(question, prompt)
        self.assertIn("ledger list", prompt)
        self.assertIn("usage: ledger", prompt)
        self.assertIn('"path": "/entries"', prompt)

        dropped = {item["slug"]: item["reason"] for item in result["selection"]["grounding_dropped"]}
        self.assertIn("subcommand not found", dropped["export-ledger"])
        self.assertIn("route not in the API spec", dropped["delete-entry"])
        self.assertIn("unanswered", dropped["list-ledger"])
        self.assertIn("needs a person", dropped["browse-ledger"])
        self.assertIn("a mode of record-ledger-entries", dropped["record-ledger-entries-quietly"])
        self.assertEqual(result["selection"]["model_rejected"][0]["name"], "make report")

        skill = (self.app / ".cursor" / "skills" / "record-ledger-entries" / "SKILL.md").read_text()
        self.assertTrue(skill.startswith("---\nname: record-ledger-entries\n"))
        self.assertIn("Records a text entry in the ledger.", skill)
        self.assertIn("## When to use", skill)
        self.assertIn("## Verify", skill)
        self.assertIn('`ledger add "milk"`', skill)
        self.assertNotIn("--json", skill)
        self.assertIn("`ledger add bread`", skill)
        self.assertIn("source:README.md", skill)
        self.assertNotIn("missing.md", skill)

        self.assertIn("`ledger add --from note.txt`", skill)
        self.assertIn("`ledger add eggs | wc -l`", skill)
        self.assertNotIn("absent.txt", skill)
        self.assertNotIn("rm -rf", skill)
        self.assertFalse((self.app / ".ellict" / "headless" / "outside.txt").exists())

        why = (self.app / ".cursor" / "skills" / "record-ledger-entries" / "references" / "selection.md").read_text()
        self.assertIn("Selected by: command:scripted", why)
        self.assertIn("Writing to the ledger is the application's core action.", why)
        self.assertIn("ledger add milk --json (undocumented --json)", why)
        self.assertIn("ledger add eggs; rm -rf ~/notes (uses shell control syntax)", why)
        self.assertIn("ledger add --from absent.txt (exit 1: FileNotFoundError", why)

        runs = next(e for e in load(self.app) if e["phase"] == "headless" and e["slug"] == "record-ledger-entries")
        self.assertEqual([r["ok"] for r in runs["runs"]], [True, True, False, True, True])

        self.assertTrue((owned / "record-ledger-entries" / "SKILL.md").exists())
        self.assertTrue((owned / "create-entry" / "SKILL.md").exists())
        self.assertIn("push", (owned / "PUSH.txt").read_text())

        record = json.loads((self.app / ".ellict" / "selection.json").read_text())
        self.assertEqual(record["model"], "command:scripted")
        self.assertIn("README.md", record["evidence"]["docs"])
        phases = [event["phase"] for event in load(self.app)]
        for phase in ("select", "build", "validate", "publish", "adopt"):
            self.assertIn(phase, phases)

    def test_skill_whose_examples_wait_for_a_person_is_not_published(self) -> None:
        os.environ["SCRIPTED_MODEL_REPLY"] = str(FIXTURES / "ledger_hang_reply.json")
        result = run(self.app, select="agent", model=self.model)
        self.assertEqual(result["approved"], [])
        self.assertEqual(result["rejected"], ["follow-ledger"])
        check = next(
            c for e in load(self.app) if e["phase"] == "validate" for c in e["checks"] if c["name"] == "headless"
        )
        self.assertIn("no exit after 2s with no terminal attached", check["detail"])
        self.assertFalse((self.app / ".cursor" / "skills" / "follow-ledger").exists())

    def _review(self, jobs: list) -> None:
        reply = self.app.parent / "review.json"
        reply.write_text(json.dumps({"jobs": jobs}))
        os.environ["SCRIPTED_REVIEW_REPLY"] = str(reply)

    def test_review_merges_skills_that_are_one_job(self) -> None:
        self._review([{"skills": ["record-ledger-entries", "record-ledger-entries-quietly"], "why": "both log an entry"}])
        result = run(self.app, select="agent", model=self.model)
        review = (self.log / "review-1.txt").read_text(encoding="utf-8")
        self.assertIn("These skills all run `ledger add`", review)
        self.assertIn("record-ledger-entries-quietly", review)
        dropped = {item["slug"]: item["reason"] for item in result["selection"]["grounding_dropped"]}
        self.assertIn("same job as record-ledger-entries", dropped["record-ledger-entries-quietly"])
        self.assertIn("both log an entry", dropped["record-ledger-entries-quietly"])
        skill = (self.app / ".cursor" / "skills" / "record-ledger-entries" / "SKILL.md").read_text()
        self.assertIn("`ledger add bread`", skill)

    def test_review_keeps_a_different_job_the_model_did_not_justify(self) -> None:
        self._review([
            {"skills": ["record-ledger-entries"], "why": "log an entry"},
            {"skills": ["record-ledger-entries-quietly"], "why": "a different result"},
        ])
        result = run(self.app, select="agent", model=self.model)
        self.assertIn("record-ledger-entries-quietly", result["approved"])
        self.assertNotIn(
            "record-ledger-entries-quietly", [d["slug"] for d in result["selection"]["grounding_dropped"]]
        )

    def test_agent_answers_the_prompt_itself_without_an_api_key(self) -> None:
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["prompt", str(self.app)]), 0)
        prompt = (self.app / ".ellict" / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("What would you actually use?", prompt)
        self.assertIn("--answer", out.getvalue())
        answer = self.app / ".ellict" / "answer.json"
        answer.write_text((FIXTURES / "ledger_reply.json").read_text(encoding="utf-8"))
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(StringIO()):
            code = main(["run", str(self.app), "--answer", str(answer)])
        self.assertEqual(code, 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result["selector"], "agent")
        self.assertEqual(result["approved"], ["create-entry", "record-ledger-entries"])
        self.assertEqual(self.calls(), 0)

    def test_unchanged_repository_reuses_the_recorded_answer(self) -> None:
        run(self.app, select="agent", model=self.model)
        second = run(self.app, select="agent", model=self.model)
        self.assertEqual(self.calls(), 1)
        self.assertTrue(second["selection"]["cached"])
        self.assertEqual(second["approved"], ["create-entry", "record-ledger-entries"])

    def test_changed_docs_ask_again_and_maintain_rebuilds(self) -> None:
        run(self.app, select="agent", model=self.model)
        readme = self.app / "README.md"
        readme.write_text(readme.read_text() + "\nEntries are plain text.\n")
        second = run(self.app, select="agent", model=self.model, maintain_only=True)
        self.assertEqual(self.calls(), 2)
        self.assertEqual(sorted(second["changed"]), ["create-entry", "record-ledger-entries"])

    def test_unreadable_reply_fails_and_publishes_nothing(self) -> None:
        bad = self.app.parent / "bad.txt"
        bad.write_text("I think the ledger is great.")
        os.environ["SCRIPTED_MODEL_REPLY"] = str(bad)
        with self.assertRaises(SelectionError):
            run(self.app, select="agent", model=self.model)
        self.assertFalse((self.app / ".ellict" / "catalog").exists())
        self.assertFalse(load(self.app)[-1]["ok"])

    def test_cli_uses_the_model_from_the_environment(self) -> None:
        os.environ["ELLICT_MODEL"] = "command:scripted"
        os.environ["ELLICT_MODEL_COMMAND"] = scripted_command()
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["run", str(self.app)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["selector"], "agent")
        self.assertNotIn("static rules", err.getvalue())


class NoModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = fresh(self.id().split(".")[-1])
        self.env = mock.patch.dict(os.environ, without_model_env(), clear=True)
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()

    def test_agent_selection_without_a_model_is_an_error(self) -> None:
        with self.assertRaises(SelectionError):
            run(self.app, select="agent")

    def test_auto_falls_back_to_rules_and_says_so(self) -> None:
        err = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(err):
            code = main(["run", str(self.app)])
        self.assertEqual(code, 0)
        self.assertIn("using static rules (no model configured)", err.getvalue())


if __name__ == "__main__":
    unittest.main()
