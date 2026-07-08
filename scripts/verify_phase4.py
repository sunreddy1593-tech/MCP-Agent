"""Run Phase 4 summarization against the latest persisted themes.json (dev utility).

Avoids re-running the ~7-min Phase 3 Groq classification while verifying the
weekly-note generation end to end with the real LLM.
"""

from __future__ import annotations

import glob
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from review_pulse.config import load_config
from review_pulse.llm.client import LlmClient
from review_pulse.models import Theme
from review_pulse.pipeline.summarize import summarize
from review_pulse.pipeline.validators import validate_note, word_count
from review_pulse.pipeline.summarize import _allowed_quotes

config = load_config("config/run_config.example.yaml")
path = sorted(glob.glob("store/runs/*/themes.json"), key=os.path.getmtime)[-1]
themes = [Theme.model_validate(t) for t in json.load(open(path, encoding="utf-8"))]
print(f"themes file: {path}")

llm = LlmClient.from_config(config)
note = summarize(themes, config, llm)

print(f"\ngenerated_by: {note.generated_by}   words: {note.word_count}")
top = [t for t in themes if t.rank in (1, 2, 3)][:3]
errors = validate_note(note, _allowed_quotes(top))
print(f"validation: {'OK' if not errors else errors}\n")

print(f"=== {note.product} — Weekly Review Pulse ({note.week_of}) ===\n")
print("TOP THEMES")
for t in note.themes:
    print(f"  - {t.name} ({t.stat})\n      {t.summary}")
print("\nQUOTES")
for q in note.quotes:
    print(f'  - "{q}"')
print("\nACTIONS")
for a in note.actions:
    print(f"  - {a}")

run_dir = os.path.dirname(path)
with open(os.path.join(run_dir, "note.json"), "w", encoding="utf-8") as fh:
    json.dump(note.model_dump(mode="json"), fh, indent=2, ensure_ascii=False)
print(f"\npersisted note.json -> {run_dir}")
