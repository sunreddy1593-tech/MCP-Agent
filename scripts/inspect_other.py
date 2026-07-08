"""Sample reviews from the 'other' bucket of the latest run (dev utility)."""

from __future__ import annotations

import glob
import json
import os
import random

run_dir = os.path.dirname(sorted(glob.glob("store/runs/*/themes.json"), key=os.path.getmtime)[-1])
themes = json.load(open(os.path.join(run_dir, "themes.json"), encoding="utf-8"))
reviews = json.load(open(os.path.join(run_dir, "normalized.json"), encoding="utf-8"))

other = next(t for t in themes if t["label"] == "other")
idxs = other["review_indices"]
print(f"other count: {len(idxs)}\n--- 15 random samples ---")
random.seed(1)
for i in random.sample(idxs, min(15, len(idxs))):
    r = reviews[i]
    print(f"[{r['rating']}star] {r['text'][:160]}")
