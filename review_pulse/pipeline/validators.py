"""Validators for the weekly note (Phase 4 guardrails).

Enforced in code, not just via the prompt:
  - structure    : exactly 3 themes, 3 quotes, 3 actions.
  - verbatim     : every quote must match a provided candidate quote (no
                   paraphrase, no invention).
  - word budget  : the note's prose is <= max_words (default 250).
  - no PII       : safety-net re-scan of the note's text.
"""

from __future__ import annotations

from review_pulse.models import WeeklyNote
from review_pulse.pipeline.pii import contains_pii

MAX_WORDS = 250


def note_text(note: WeeklyNote) -> str:
    """Assemble the human-readable prose of the note (for counting/scanning)."""
    parts: list[str] = []
    for theme in note.themes:
        parts += [theme.name, theme.summary, theme.stat]
    parts += list(note.quotes)
    parts += list(note.actions)
    return " ".join(p for p in parts if p)


def word_count(note: WeeklyNote) -> int:
    return len(note_text(note).split())


def _quote_is_verbatim(quote: str, allowed: set[str]) -> bool:
    q = quote.strip()
    if q in allowed:
        return True
    # Allow an exact substring of a candidate (LLM may quote part of a review).
    return any(q and q in candidate for candidate in allowed)


def validate_note(note: WeeklyNote, allowed_quotes: set[str], max_words: int = MAX_WORDS) -> list[str]:
    """Return a list of validation error strings (empty == valid)."""
    errors: list[str] = []

    if len(note.themes) != 3:
        errors.append(f"expected 3 themes, got {len(note.themes)}")
    if len(note.quotes) != 3:
        errors.append(f"expected 3 quotes, got {len(note.quotes)}")
    if len(note.actions) != 3:
        errors.append(f"expected 3 actions, got {len(note.actions)}")

    for i, quote in enumerate(note.quotes):
        if not _quote_is_verbatim(quote, allowed_quotes):
            errors.append(f"quote {i + 1} is not verbatim from the candidate quotes")

    wc = word_count(note)
    if wc > max_words:
        errors.append(f"note is {wc} words (>{max_words})")

    if contains_pii(note_text(note)):
        errors.append("note contains PII")

    return errors
