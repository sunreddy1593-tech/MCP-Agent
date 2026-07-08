"""App Store review source adapter.

Reads a public App Store review export (CSV or JSON) and maps it to canonical
`Review` objects. Reviewer-identity fields present in the export (e.g.
`reviewer_name`, `country`) are intentionally dropped here.
"""

from __future__ import annotations

import logging
from typing import Any

from review_pulse.models import Review
from review_pulse.sources.base import ReviewSource, parse_date, parse_rating

logger = logging.getLogger(__name__)


def _first(row: dict[str, Any], *keys: str) -> Any:
    """Return the first present, non-empty value among candidate keys."""
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


class AppStoreAdapter(ReviewSource):
    store = "app_store"
    export_basename = "app_store"

    def _map_row(self, row: dict[str, Any]) -> Review | None:
        text = _first(row, "text", "body", "review", "content")
        if not text or not str(text).strip():
            return None  # edge case: missing/empty text -> drop

        date = parse_date(_first(row, "date", "updated_at", "created_at", "review_date"))
        if date is None:
            return None  # edge case: missing/unparseable date -> exclude

        return Review(
            store=self.store,
            rating=parse_rating(_first(row, "rating", "stars", "score")),
            title=_first(row, "title", "review_title"),
            text=str(text),
            date=date,
        )
