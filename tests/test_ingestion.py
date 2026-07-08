"""Phase 1 ingestion tests: canonical mapping + date-window filtering."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from review_pulse.config import RunConfig, WindowConfig
from review_pulse.sources.app_store import AppStoreAdapter
from review_pulse.sources.base import parse_date, parse_rating
from review_pulse.sources.play_store import PlayStoreAdapter


def _make_config(exports_dir, weeks: int = 12) -> RunConfig:
    return RunConfig(
        product_id="com.test.app",
        product_name="Test App",
        exports_dir=str(exports_dir),
        window=WindowConfig(weeks=weeks),
    )


def _recent(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def test_parse_rating_bounds():
    assert parse_rating("5") == 5
    assert parse_rating(3.0) == 3
    assert parse_rating("6") is None
    assert parse_rating("0") is None
    assert parse_rating("") is None
    assert parse_rating("abc") is None


def test_parse_date_variants():
    assert parse_date("2026-06-01").tzinfo is not None
    assert parse_date("2026-06-01T10:00:00Z").tzinfo is not None
    assert parse_date("not-a-date") is None
    assert parse_date("") is None


def test_app_store_ingestion_window_and_mapping(tmp_path):
    csv_path = tmp_path / "app_store.csv"
    csv_path.write_text(
        "reviewer_name,rating,title,body,updated_at\n"
        f'jane,5,Nice,"Onboarding was smooth",{_recent(5)}\n'
        f'bad,6,OOB,"Rating out of range",{_recent(6)}\n'
        f'missing,4,No body,,{_recent(7)}\n'
        f'old,2,Old,"Way outside the window",{_recent(400)}\n',
        encoding="utf-8",
    )
    config = _make_config(tmp_path, weeks=12)
    reviews = AppStoreAdapter().fetch(config)

    # 2 kept (jane + out-of-range rating row); missing-body dropped; old filtered.
    assert len(reviews) == 2
    assert all(r.store == "app_store" for r in reviews)
    ratings = sorted((r.rating is None, r.rating) for r in reviews)
    # One valid rating (5) and one coerced to None (was 6).
    assert (False, 5) in ratings
    assert any(r.rating is None for r in reviews)


def test_play_store_ingestion_json(tmp_path):
    data = [
        {"userName": "a", "score": 2, "content": "KYC failed", "at": _recent(3)},
        {"userName": "b", "score": 5, "content": "", "at": _recent(4)},
        {"userName": "c", "score": 4, "content": "Old one", "at": _recent(500)},
    ]
    (tmp_path / "play_store.json").write_text(json.dumps(data), encoding="utf-8")

    reviews = PlayStoreAdapter().fetch(_make_config(tmp_path))
    assert len(reviews) == 1
    assert reviews[0].store == "play_store"
    assert reviews[0].title is None


def test_missing_export_returns_empty(tmp_path):
    assert AppStoreAdapter().fetch(_make_config(tmp_path)) == []
