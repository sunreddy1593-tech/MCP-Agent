"""Play Store review source adapter.

Reads a public Play Store review export (JSON or CSV) and maps it to canonical
`Review` objects. Reviewer-identity fields (e.g. `userName`, `reviewId`) are
intentionally dropped here. Play Store reviews typically have no title.
"""

from __future__ import annotations

import logging
from typing import Any

from review_pulse.models import Review
from review_pulse.sources.base import ReviewSource, parse_date, parse_rating

logger = logging.getLogger(__name__)


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


class PlayStoreAdapter(ReviewSource):
    store = "play_store"
    export_basename = "play_store"

    def _map_row(self, row: dict[str, Any]) -> Review | None:
        text = _first(row, "content", "text", "review", "body")
        if not text or not str(text).strip():
            return None  # edge case: missing/empty text -> drop

        date = parse_date(_first(row, "at", "date", "reviewCreatedVersion", "created_at"))
        if date is None:
            return None  # edge case: missing/unparseable date -> exclude

        return Review(
            store=self.store,
            rating=parse_rating(_first(row, "score", "rating", "stars")),
            title=_first(row, "title"),
            text=str(text),
            date=date,
        )
