"""Phase 4 tests: note validators, fallback note, quote traceability."""

from __future__ import annotations

from review_pulse.config import RunConfig
from review_pulse.models import NoteTheme, Quote, Theme, WeeklyNote
from review_pulse.pipeline.summarize import summarize
from review_pulse.pipeline.validators import note_text, validate_note, word_count


def _config() -> RunConfig:
    return RunConfig(product_id="com.test", product_name="Groww")


def _theme(label: str, rank: int, neg: float, quotes: list[str]) -> Theme:
    return Theme(
        label=label,
        description=f"about {label}",
        count=100,
        avg_rating=2.5,
        negative_share=neg,
        score=100.0,
        rank=rank,
        quotes=[Quote(text=q, rating=1, store="play_store") for q in quotes],
    )


def _valid_note() -> WeeklyNote:
    return WeeklyNote(
        week_of="2026-07-05",
        product="Groww",
        themes=[
            NoteTheme(name="Charges & Fees", summary="Users dislike charges.", stat="100 reviews"),
            NoteTheme(name="Trading & Products", summary="Mixed on trading.", stat="90 reviews"),
            NoteTheme(name="App UX", summary="UI complaints.", stat="80 reviews"),
        ],
        quotes=["brokerage charges are too high", "the app keeps crashing daily", "cannot place orders"],
        actions=["Cut hidden charges.", "Fix crashes.", "Improve order flow."],
    )


def test_validate_note_ok():
    note = _valid_note()
    allowed = set(note.quotes)
    assert validate_note(note, allowed) == []


def test_validate_note_catches_non_verbatim_quote():
    note = _valid_note()
    note.quotes[0] = "this paraphrased quote was invented"
    allowed = {"brokerage charges are too high", "the app keeps crashing daily", "cannot place orders"}
    errors = validate_note(note, allowed)
    assert any("verbatim" in e for e in errors)


def test_validate_note_catches_wrong_counts_and_length():
    note = _valid_note()
    note.actions = ["only one action"]
    errors = validate_note(note, set(note.quotes))
    assert any("3 actions" in e for e in errors)


def test_validate_note_flags_pii():
    note = _valid_note()
    note.actions[0] = "Email the user at test@example.com about charges."
    errors = validate_note(note, set(note.quotes))
    assert any("PII" in e for e in errors)


def test_fallback_note_when_no_llm():
    themes = [
        _theme("charges_fees", 1, 0.7, ["brokerage charges are far too high for retail traders"]),
        _theme("trading_products", 2, 0.4, ["order execution is slow during market open hours"]),
        _theme("app_ux_updates", 3, 0.5, ["the latest ui update broke the portfolio screen layout"]),
    ]
    note = summarize(themes, _config(), llm=None)
    assert note is not None
    assert note.generated_by == "fallback"
    assert len(note.themes) == 3
    assert len(note.quotes) == 3
    assert len(note.actions) == 3
    assert note.word_count <= 250
    # quotes are verbatim from the theme candidate quotes
    allowed = {q.text for t in themes for q in t.quotes}
    for q in note.quotes:
        assert q in allowed
