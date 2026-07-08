"""Phase 3 theming tests: keyword pre-pass, ranking, fallback, quote traceability."""

from __future__ import annotations

from datetime import datetime, timezone

from review_pulse.config import RunConfig
from review_pulse.models import Review
from review_pulse.pipeline.theming import OTHER, _keyword_theme, build_themes


def _config() -> RunConfig:
    return RunConfig(product_id="com.test", product_name="Test")


def _labels() -> list[str]:
    return _config().themes.labels


def _review(text: str, rating: int, day: int = 1) -> Review:
    return Review(
        store="play_store",
        rating=rating,
        title=None,
        text=text,
        date=datetime(2026, 6, day, tzinfo=timezone.utc),
    )


def test_keyword_prepass_matches_labels():
    labels = _labels()
    assert _keyword_theme("brokerage charges are too high here", labels) == "charges_fees"
    assert _keyword_theme("cannot withdraw my money via upi", labels) == "withdrawals_payments"
    assert _keyword_theme("the new ui update is buggy and slow", labels) == "app_ux_updates"
    assert _keyword_theme("weather is nice today outside", labels) == OTHER


def test_build_themes_keyword_fallback_and_ranking():
    # No llm -> keyword fallback. Charges theme is larger AND more negative.
    reviews = [
        _review("hidden brokerage charges everywhere and extra fee added", 1),
        _review("too many charges and high brokerage fee on every trade", 1),
        _review("charges are ridiculous, fee keeps increasing every month", 2),
        _review("the app ui update looks clean and the interface is nice", 5),
        _review("love the new design update, interface is smooth overall", 5),
    ]
    themes = build_themes(reviews, _config(), llm=None)
    ranked = [t for t in themes if t.rank > 0]
    assert ranked[0].label == "charges_fees"
    assert ranked[0].rank == 1
    # charges theme should be flagged high negative share
    assert ranked[0].negative_share == 1.0
    assert all(t.count > 0 for t in ranked)


def test_top_themes_have_traceable_quotes():
    reviews = [
        _review("brokerage charges are unfair and the fee is very high indeed", 1, 1),
        _review("hidden charges keep appearing and the brokerage fee is huge", 1, 2),
        _review("extra charges added silently, fees are not transparent at all", 2, 3),
    ]
    themes = build_themes(reviews, _config(), llm=None)
    charges = next(t for t in themes if t.label == "charges_fees")
    assert charges.quotes
    source_texts = {r.text for r in reviews}
    for q in charges.quotes:
        assert q.text in source_texts  # verbatim, traceable
