"""Normalization & PII stripping (Phase 2).

Takes the merged review stream and produces the clean, anonymized set that is
the ONLY data allowed downstream:
  1. Scrub PII from text (and any title).
  2. Apply quality/language filters, dropping a review if any apply:
       - fewer than `filters.min_words` words,
       - Hindi (Devanagari-script) content,
       - contains emojis (more than `filters.max_emojis`, default 0).
  3. Drop empty text.
  4. Deduplicate genuine duplicates (same store + text + timestamp), which
     removes re-export/window overlap without collapsing independent reviews
     that happen to share short text.

The canonical `Review` already carries no reviewer-identity fields, so the
output is reduced to `{store, rating, title, text, date}` by construction.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import TYPE_CHECKING

from review_pulse.models import Review
from review_pulse.pipeline.pii import scrub_text
from review_pulse.pipeline.textstats import count_emojis, is_hindi, word_count

if TYPE_CHECKING:
    from review_pulse.config import RunConfig

logger = logging.getLogger(__name__)


def _canonical(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def normalize(reviews: list[Review], config: "RunConfig") -> list[Review]:
    """Scrub PII, apply quality/language filters, and dedupe anonymized reviews."""
    filters = config.filters
    kept: list[Review] = []
    dropped = Counter()

    for review in reviews:
        text = scrub_text(review.text).strip()
        if not text:
            dropped["empty"] += 1
            continue
        if filters.drop_hindi and is_hindi(text, filters.hindi_ratio):
            dropped["hindi"] += 1
            continue
        if count_emojis(text) > filters.max_emojis:
            dropped["has_emoji"] += 1
            continue
        if word_count(text) < filters.min_words:
            dropped["too_short"] += 1
            continue
        title = scrub_text(review.title).strip() or None if review.title else None
        kept.append(review.model_copy(update={"text": text, "title": title}))

    seen: set[tuple[str, str, str]] = set()
    deduped: list[Review] = []
    duplicates = 0
    for review in kept:
        key = (review.store, _canonical(review.text), review.date.isoformat())
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        deduped.append(review)

    logger.info(
        "Normalized %d -> %d reviews; dropped %s, %d duplicates; PII scrubbed",
        len(reviews),
        len(deduped),
        dict(dropped),
        duplicates,
    )
    return deduped
