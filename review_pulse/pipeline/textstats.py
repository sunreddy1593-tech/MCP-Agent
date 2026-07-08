"""Lightweight text statistics used by the Phase 2 quality/language filters.

Deterministic and dependency-free:
  - `word_count`      : number of alphanumeric-bearing tokens.
  - `count_emojis`    : number of emoji characters.
  - `devanagari_ratio`: fraction of letters that are Devanagari (Hindi script).
  - `is_hindi`        : Devanagari ratio at/above a threshold.

Note: Hindi detection is script-based (Devanagari). Romanized "Hinglish"
written in the Latin alphabet is not detected by design.
"""

from __future__ import annotations

import re

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
# Unicode letters (excludes digits, underscore, punctuation); includes Devanagari.
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

# Common emoji ranges (pictographs, emoticons, transport, supplemental,
# extended-A, misc symbols, dingbats, stars/symbols, and regional-indicator flags).
_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001faff"
    "\U00002600-\U000027bf"
    "\U00002b00-\U00002bff"
    "\U0001f1e6-\U0001f1ff"
    "]",
    flags=re.UNICODE,
)


def word_count(text: str) -> int:
    """Count whitespace-separated tokens that contain at least one alphanumeric."""
    if not text:
        return 0
    return sum(1 for tok in text.split() if any(ch.isalnum() for ch in tok))


def count_emojis(text: str) -> int:
    """Count emoji characters in the text."""
    if not text:
        return 0
    return len(_EMOJI_RE.findall(text))


def devanagari_ratio(text: str) -> float:
    """Fraction of alphabetic characters that are Devanagari (0.0 if no letters)."""
    if not text:
        return 0.0
    letters = _LETTER_RE.findall(text)
    if not letters:
        return 0.0
    devanagari = _DEVANAGARI_RE.findall(text)
    return len(devanagari) / len(letters)


def is_hindi(text: str, ratio_threshold: float = 0.2) -> bool:
    """True if the text is predominantly Devanagari (Hindi) script."""
    return devanagari_ratio(text) >= ratio_threshold
