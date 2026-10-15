import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
LEDGER = ROOT / "examples" / "ledger"
TMP = Path(__file__).resolve().parent / "_tmp"
os.environ.setdefault("ELLICT_EXAMPLE_TIMEOUT", "2")
MODEL_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ELLICT_MODEL", "ELLICT_MODEL_COMMAND", "ELLICT_NO_CACHE", "ELLICT_DECISIONS")


def fresh(name: str, source: Path = LEDGER) -> Path:
    target = TMP / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target / "app")
    return target / "app"


def scripted_command() -> str:
    return f"{sys.executable} {FIXTURES / 'scripted_model.py'}"


def without_model_env() -> dict:
    return {key: value for key, value in os.environ.items() if key not in MODEL_KEYS}
