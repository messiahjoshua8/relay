"""Model clients. Standard library only, so ellict installs without extra packages."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-5-mini",
}


class ModelError(RuntimeError):
    pass


@dataclass
class Model:
    """A model ellict can ask. ``spec`` is ``provider:name`` and is recorded with every answer."""

    provider: str
    name: str
    command: str | None = None
    timeout: int = 300

    @property
    def spec(self) -> str:
        return f"{self.provider}:{self.name}"

    def complete(self, system: str, user: str) -> str:
        if self.provider == "anthropic":
            return self._anthropic(system, user)
        if self.provider == "openai":
            return self._openai(system, user)
        if self.provider == "command":
            return self._command(system, user)
        if self.provider == "answer":
            return self._answer()
        raise ModelError(f"unknown model provider: {self.provider}")

    def _answer(self) -> str:
        path = Path(self.command or "")
        if not path.is_file():
            raise ModelError(f"answer file not found: {path}; run `ellict prompt` and write the reply there")
        return path.read_text(encoding="utf-8")

    def _post(self, url: str, headers: dict[str, str], body: dict) -> dict:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"content-type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise ModelError(f"{self.spec} returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ModelError(f"{self.spec} unreachable: {exc.reason}") from exc

    def _anthropic(self, system: str, user: str) -> str:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ModelError("ANTHROPIC_API_KEY is not set")
        data = self._post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            {
                "model": self.name,
                "max_tokens": 8000,
                "temperature": 0,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        return "".join(part.get("text", "") for part in data.get("content", []))

    def _openai(self, system: str, user: str) -> str:
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        headers = {}
        if os.environ.get("OPENAI_API_KEY"):
            headers["authorization"] = f"Bearer {os.environ['OPENAI_API_KEY']}"
        body = {
            "model": self.name,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # GPT-5 and o-series reasoning models only accept the default temperature.
        if not self.name.startswith(("gpt-5", "o1", "o3", "o4")):
            body["temperature"] = 0
        data = self._post(f"{base}/chat/completions", headers, body)
        choices = data.get("choices") or []
        if not choices:
            raise ModelError(f"{self.spec} returned no choices")
        return choices[0].get("message", {}).get("content", "")

    def _command(self, system: str, user: str) -> str:
        if not self.command:
            raise ModelError("ELLICT_MODEL_COMMAND is not set")
        try:
            completed = subprocess.run(
                shlex.split(self.command),
                input=f"{system}\n\n{user}",
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ModelError(f"model command failed: {exc}") from exc
        if completed.returncode != 0:
            raise ModelError(f"model command exited {completed.returncode}: {completed.stderr[:500]}")
        return completed.stdout


def load_dotenv(*paths: Path) -> None:
    """Read KEY=value lines into the environment without overriding variables already set."""
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.removeprefix("export ").partition("=")
            key, value = key.strip(), value.strip().strip("\"'")
            if key and value and key not in os.environ:
                os.environ[key] = value


def from_env(config: dict | None = None) -> Model | None:
    """Pick a model from ``ELLICT_MODEL``, ``ellict.json``, or whichever key is present."""
    config = config or {}
    spec = os.environ.get("ELLICT_MODEL") or config.get("model")
    command = os.environ.get("ELLICT_MODEL_COMMAND") or config.get("modelCommand")
    if spec:
        provider, _, name = spec.partition(":")
        if provider == "command":
            return Model("command", name or "custom", command=command)
        if provider not in DEFAULT_MODELS:
            raise ModelError(f"unknown model provider in {spec!r}")
        return Model(provider, name or DEFAULT_MODELS[provider])
    if os.environ.get("ANTHROPIC_API_KEY"):
        return Model("anthropic", DEFAULT_MODELS["anthropic"])
    if os.environ.get("OPENAI_API_KEY"):
        return Model("openai", DEFAULT_MODELS["openai"])
    if command:
        return Model("command", "custom", command=command)
    return None


def parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ModelError("model reply contained no JSON object")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ModelError(f"model reply was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ModelError("model reply was not a JSON object")
    return data


def ask(model: Model, system: str, user: str, *, cache_dir: Path) -> tuple[dict, str, bool]:
    """Return the parsed answer, its cache key, and whether it came from the cache."""
    key = hashlib.sha256(f"{model.spec}\n{system}\n{user}".encode()).hexdigest()[:24]
    path = cache_dir / f"{key}.json"
    if model.provider == "answer":
        return parse_json(model.complete(system, user)), key, False
    if path.exists() and not os.environ.get("ELLICT_NO_CACHE"):
        record = json.loads(path.read_text(encoding="utf-8"))
        return parse_json(record["response"]), key, True
    response = model.complete(system, user)
    answer = parse_json(response)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"model": model.spec, "system": system, "user": user, "response": response},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return answer, key, False
