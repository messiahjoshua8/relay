"""The gold list catches the mistakes it was written to catch."""

import unittest

from gold import GOLD, score, skill_examples

TTOK = GOLD["projects"]["ttok"]


class GoldTest(unittest.TestCase):
    def test_truncate_folded_into_count_is_a_missing_job(self) -> None:
        result = score(TTOK, {"count-tokens": ["ttok Hello world", "cat a.txt | ttok", "cat a.txt | ttok -t 100"]})
        self.assertEqual(result["missing_jobs"], ["truncate-to-token-budget"])

    def test_truncate_as_its_own_skill_passes(self) -> None:
        result = score(
            TTOK,
            {
                "count-tokens": ["ttok Hello world", "ttok --encode hi"],
                "truncate-text": ["cat a.txt | ttok -t 100", "ttok --truncate 5 a b c d e f"],
            },
        )
        self.assertEqual(result["missing_jobs"], [])
        self.assertEqual(result["split_jobs"], {})
        self.assertEqual(result["jobs"]["truncate-text"], "truncate-to-token-budget")

    def test_one_job_split_into_two_skills_fails(self) -> None:
        result = score(
            GOLD["projects"]["files-to-prompt"],
            {"bundle-xml": ["files-to-prompt src --cxml"], "bundle-markdown": ["files-to-prompt src --markdown"]},
        )
        self.assertEqual(sorted(result["split_jobs"]["bundle-files-for-a-prompt"]), ["bundle-markdown", "bundle-xml"])

    def test_remove_selector_is_cleanup_not_extraction(self) -> None:
        project = GOLD["projects"]["strip-tags"]
        result = score(
            project,
            {
                "extract": ["cat page.html | strip-tags .content", "strip-tags -i page.html article --first"],
                "clean": ["strip-tags -i page.html -r .ads -m", "cat page.html | strip-tags -r nav"],
            },
        )
        self.assertEqual(result["jobs"], {"extract": "extract-by-selector", "clean": "html-to-text"})

    def test_rich_terminal_rendering_alone_misses_the_export_job(self) -> None:
        result = score(
            GOLD["projects"]["rich-cli"],
            {"render-markdown": ["rich README.md"], "pretty-json": ["rich data.json"], "syntax": ["rich app.py -n"]},
        )
        self.assertEqual(result["missing_jobs"], ["export-rendered-file"])

    def test_every_project_has_jobs_or_says_why_not(self) -> None:
        for name, project in GOLD["projects"].items():
            with self.subTest(project=name):
                self.assertTrue(project["jobs"] or project.get("why"))
                for job in project["jobs"]:
                    if job.get("required", True):
                        self.assertTrue(job.get("why"), job["job"])
                self.assertLessEqual(len(project["jobs"]), GOLD["max_skills_per_command"] + 2)

    def test_reads_examples_from_a_skill(self) -> None:
        skill = "# x\n\n## Examples\n- Count: `ttok hi`\n- `cat a | ttok -t 3`\n\n## Verify\n`ttok nope`\n"
        self.assertEqual(skill_examples(skill), ["ttok hi", "cat a | ttok -t 3"])


if __name__ == "__main__":
    unittest.main()
