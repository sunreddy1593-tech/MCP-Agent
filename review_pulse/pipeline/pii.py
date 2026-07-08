"""PII scrubbing.

Masks personally identifiable information inside free text BEFORE anything
downstream (theming, the LLM, artifacts) can see it. Detectors are tuned to
target *identifiers* while leaving ordinary content — including currency
amounts like "50,000" or "₹500" — readable, so themes and quotes stay useful.

Masked categories -> replacement token:
  emails                              -> [EMAIL]
  phone numbers (>=10 digits)         -> [PHONE]
  Indian PAN (ABCDE1234F)             -> [PAN]
  long numeric ids (>=7 digits,       -> [ID]
    e.g. Aadhaar, account/order/device)
  social handles (@name)              -> [HANDLE]
"""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
# Candidate phone runs: an optional +, then digits separated by spaces/dashes.
# A callback confirms there are >=10 digits so short number groups are kept.
_PHONE_CAND_RE = re.compile(r"\+?\d[\d\-\s]{7,}\d")
# Standalone long digit runs (ids); currency like 50,000 has <7 contiguous digits.
_LONGNUM_RE = re.compile(r"\b\d{7,}\b")
_HANDLE_RE = re.compile(r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]{2,}")


def _mask_phones(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group())
        return "[PHONE]" if len(digits) >= 10 else match.group()

    return _PHONE_CAND_RE.sub(repl, text)


def scrub_text(text: str | None) -> str:
    """Return `text` with PII masked. None/empty yields an empty string."""
    if not text:
        return ""
    result = _EMAIL_RE.sub("[EMAIL]", text)
    result = _mask_phones(result)
    result = _PAN_RE.sub("[PAN]", result)
    result = _LONGNUM_RE.sub("[ID]", result)
    result = _HANDLE_RE.sub("[HANDLE]", result)
    return result


def contains_pii(text: str | None) -> bool:
    """True if any PII pattern is detected (used as a render-time safety net)."""
    if not text:
        return False
    return scrub_text(text) != text
