"""Shared interface and helpers for review sources.

Concrete adapters (App Store, Play Store) implement `_map_row` to translate one
store-specific export row into a canonical `Review`. The base class handles
locating the export file, loading CSV/JSON rows, lenient field parsing, and
date-window filtering — so adapters only worry about field names.

Only public exports are read here; there is no login-gated scraping.
"""

from __future__ import annotations

import csv
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from review_pulse.models import Review

if TYPE_CHECKING:
    from review_pulse.config import RunConfig

logger = logging.getLogger(__name__)

# Formats tried (in order) when a date is not ISO-8601.
_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%b %d, %Y",
    "%Y/%m/%d",
)


def parse_date(value: Any) -> datetime | None:
    """Parse a date value into a timezone-aware UTC datetime, or None.

    Naive datetimes are assumed to be UTC so that window filtering is
    consistent across stores (edge case: timezone-naive exports).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        dt = None
        # ISO-8601 first (handles trailing 'Z').
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for fmt in _DATE_FORMATS:
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_rating(value: Any) -> int | None:
    """Parse a rating into an int in 1..5, else None (out-of-range/invalid)."""
    if value is None or value == "":
        return None
    try:
        rating = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return rating if 1 <= rating <= 5 else None


class ReviewSource(ABC):
    """Abstract review source. One implementation per store."""

    #: Canonical store identifier, e.g. "app_store" or "play_store".
    store: str = "unknown"

    #: Base filename (without extension) to look for in the exports directory.
    export_basename: str = ""

    @abstractmethod
    def _map_row(self, row: dict[str, Any]) -> Review | None:
        """Map one raw export row to a canonical Review, or None to skip it."""
        raise NotImplementedError

    def fetch(self, config: "RunConfig") -> list[Review]:
        """Read the public export and return canonical reviews within window."""
        path = self._locate_export(config.exports_dir)
        if path is None:
            logger.warning(
                "[%s] no export found in '%s' (looked for %s.csv/.json); skipping source",
                self.store,
                config.exports_dir,
                self.export_basename,
            )
            return []

        rows = self._load_rows(path)
        reviews: list[Review] = []
        skipped = 0
        for row in rows:
            try:
                review = self._map_row(row)
            except Exception as exc:  # noqa: BLE001 - tolerate bad rows, keep going
                logger.debug("[%s] skipping malformed row: %s", self.store, exc)
                review = None
            if review is None:
                skipped += 1
                continue
            reviews.append(review)

        in_window = self._filter_window(reviews, config.window.weeks)
        logger.info(
            "[%s] parsed %d, skipped %d, in-window %d (last %d weeks) from %s",
            self.store,
            len(reviews),
            skipped,
            len(in_window),
            config.window.weeks,
            path.name,
        )
        return in_window

    def _locate_export(self, exports_dir: str) -> Path | None:
        base = Path(exports_dir) / self.export_basename
        for ext in (".csv", ".json"):
            candidate = base.with_suffix(ext)
            if candidate.exists():
                return candidate
        return None

    def _load_rows(self, path: Path) -> list[dict[str, Any]]:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                # Allow {"reviews": [...]} or a single object.
                data = data.get("reviews", [data])
            return [row for row in data if isinstance(row, dict)]
        # CSV
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _filter_window(self, reviews: list[Review], weeks: int) -> list[Review]:
        cutoff = datetime.now(timezone.utc) - timedelta(weeks=weeks)
        return [r for r in reviews if r.date >= cutoff]
