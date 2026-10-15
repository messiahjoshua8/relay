"""The one-line install and the page an agent follows."""

import os
import shutil
import subprocess
import sys
import tarfile
import unittest

from helpers import ROOT, TMP


def _home(name: str):
    home = TMP / name
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True)
    return home


def _version(binary, env) -> subprocess.CompletedProcess:
    return subprocess.run([str(binary), "--version"], env=env, capture_output=True, text=True, check=False)


class InstallTest(unittest.TestCase):
    def test_installer_puts_ellict_on_path(self) -> None:
        home = _home("home")
        env = {**os.environ, "HOME": str(home)}
        subprocess.run(["sh", "install.sh"], cwd=ROOT, env=env, check=True, capture_output=True)
        completed = _version(home / ".local" / "bin" / "ellict", env)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("ellict", completed.stdout)

    def test_piped_installer_downloads_the_source(self) -> None:
        home = _home("home-piped")
        archive = home / "ellict-main.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for name in ("src", "pyproject.toml", "README.md"):
                tar.add(
                    ROOT / name,
                    arcname=f"ellict-main/{name}",
                    filter=lambda info: None if "__pycache__" in info.name else info,
                )
        env = {**os.environ, "HOME": str(home), "ELLICT_SOURCE": archive.as_uri()}
        with open(ROOT / "install.sh", "rb") as script:
            completed = subprocess.run(["sh"], stdin=script, cwd=home, env=env, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Installed", completed.stdout)
        self.assertTrue((home / ".local" / "share" / "ellict" / "src" / "ellict" / "cli.py").exists())
        wrapper = (home / ".local" / "bin" / "ellict").read_text()
        self.assertNotIn(f'"{ROOT / "src"}', wrapper)
        self.assertEqual(_version(home / ".local" / "bin" / "ellict", env).returncode, 0)

    def test_piped_installer_fails_loudly_on_a_bad_source(self) -> None:
        home = _home("home-bad")
        env = {**os.environ, "HOME": str(home), "ELLICT_SOURCE": (home / "missing.tar.gz").as_uri()}
        with open(ROOT / "install.sh", "rb") as script:
            completed = subprocess.run(["sh"], stdin=script, cwd=home, env=env, capture_output=True, text=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("could not download", completed.stderr)
        self.assertFalse((home / ".local" / "bin" / "ellict").exists())

    def test_package_installs_with_pip_so_pipx_works(self) -> None:
        venv = _home("pip-install") / "env"
        created = subprocess.run([sys.executable, "-m", "venv", str(venv)], capture_output=True, text=True)
        self.assertEqual(created.returncode, 0, created.stderr + created.stdout)
        completed = subprocess.run(
            [str(venv / "bin" / "pip"), "install", "--quiet", str(ROOT)], capture_output=True, text=True
        )
        self.assertEqual(completed.returncode, 0, completed.stderr[-800:])
        self.assertEqual(_version(venv / "bin" / "ellict", os.environ).returncode, 0)

    def test_agent_page_matches_the_product(self) -> None:
        page = (ROOT / "agent" / "index.html").read_text(encoding="utf-8")
        self.assertIn("curl -fsSL https://eliolabs.xyz/install.sh | sh", page)
        self.assertIn("ellict run . --publish-to ~/ellict-skills", page)
        self.assertIn("ellict prompt .", page)
        self.assertIn("--answer .ellict/answer.json", page)
        self.assertIn("ANTHROPIC_API_KEY", page)
        self.assertIn("no terminal attached", page)
        self.assertNotIn("ClawHub", page)
        self.assertNotIn("Set a price", page)

    def test_site_serves_the_page_and_the_installer(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "site.yml").read_text(encoding="utf-8")
        self.assertIn("agent/index.html", workflow)
        self.assertIn("install.sh", workflow)
        self.assertEqual((ROOT / "agent" / "CNAME").read_text().strip(), "eliolabs.xyz")


if __name__ == "__main__":
    unittest.main()
