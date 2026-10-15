"""Stand-in model for offline flow tests. Logs each prompt and prints a fixed reply."""

import os
import sys
from pathlib import Path

prompt = sys.stdin.read()
log = Path(os.environ["SCRIPTED_MODEL_LOG"])
log.mkdir(parents=True, exist_ok=True)
kind = "review" if "Group them into jobs" in prompt else "prompt"
count = len(list(log.glob(f"{kind}-*.txt"))) + 1
(log / f"{kind}-{count}.txt").write_text(prompt, encoding="utf-8")
if kind == "review":
    reply = os.environ.get("SCRIPTED_REVIEW_REPLY")
    sys.stdout.write(Path(reply).read_text(encoding="utf-8") if reply else "{}")
else:
    sys.stdout.write(Path(os.environ["SCRIPTED_MODEL_REPLY"]).read_text(encoding="utf-8"))
