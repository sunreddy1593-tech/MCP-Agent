"""Canonical data models shared across the pipeline.

The canonical `Review` deliberately carries NO reviewer-identity fields — no
username, email, device id, country, or any other identifier. Adapters map
their store-specific export rows onto this shape at ingestion time.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator


class Review(BaseModel):
    """A single, anonymized store review in canonical form."""

    store: str  # "app_store" | "play_store"
    rating: int | None  # 1-5, or None when missing/out-of-range
    title: str | None
    text: str  # required, non-empty
    date: datetime  # timezone-aware (UTC)

    @field_validator("title")
    @classmethod
    def _blank_title_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("text")
    @classmethod
    def _text_required(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("review text must be non-empty")
        return value


class Quote(BaseModel):
    """A verbatim, anonymized snippet from a review, attached to a theme."""

    text: str
    rating: int | None = None
    date: datetime | None = None
    store: str | None = None


class Theme(BaseModel):
    """A cluster of reviews around one topic, with ranking metrics."""

    label: str
    description: str = ""
    count: int = 0
    avg_rating: float | None = None
    negative_share: float = 0.0  # fraction of 1-2 star reviews
    score: float = 0.0           # volume x severity ranking score
    rank: int = 0                # 1 = top; 0 = unranked / "other"
    review_indices: list[int] = []
    quotes: list[Quote] = []


class NoteTheme(BaseModel):
    """A theme as presented in the weekly note."""

    name: str
    summary: str
    stat: str = ""


class WeeklyNote(BaseModel):
    """The structured weekly pulse: 3 themes, 3 verbatim quotes, 3 actions."""

    week_of: str
    product: str
    themes: list[NoteTheme] = []
    quotes: list[str] = []
    actions: list[str] = []
    word_count: int = 0
    generated_by: str = "groq"  # "groq" | "fallback"
