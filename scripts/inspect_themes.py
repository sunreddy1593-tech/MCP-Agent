"""Print a summary of the most recent themes.json (dev utility)."""

from __future__ import annotations

import glob
import json
import os

files = sorted(glob.glob("store/runs/*/themes.json"), key=os.path.getmtime)
path = files[-1]
themes = json.load(open(path, encoding="utf-8"))

print(f"file: {path}\n")
for t in themes:
    print(
        f"rank {t['rank']:>1} | {t['label']:<22} count={t['count']:<4} "
        f"avg={t['avg_rating']} neg={t['negative_share']} "
        f"score={t['score']} quotes={len(t['quotes'])}"
    )

print("\n--- top theme sample quotes ---")
top = [t for t in themes if t["rank"] == 1][0]
print(f"theme: {top['label']}")
for q in top["quotes"][:3]:
    print(f"  [{q['rating']}star] {q['text'][:150]}")
