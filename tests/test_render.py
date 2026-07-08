"""Phase 5 tests: Doc/email rendering, attribution, sanitization, PII gate."""

from __future__ import annotations

from review_pulse.config import RunConfig
from review_pulse.models import NoteTheme, Quote, Theme, WeeklyNote
from review_pulse.pipeline.render import DOC_LINK_PLACEHOLDER, render


def _config() -> RunConfig:
    return RunConfig(product_id="com.test", product_name="Groww")


def _note(generated_by: str = "groq") -> WeeklyNote:
    return WeeklyNote(
        week_of="2026-07-05",
        product="Groww",
        themes=[
            NoteTheme(name="Charges & Fees", summary="Users dislike charges.", stat="249 reviews, 62% negative"),
            NoteTheme(name="Trading & Products", summary="Mixed on trading.", stat="209 reviews, 40% negative"),
            NoteTheme(name="App UX", summary="UI complaints.", stat="152 reviews, 55% negative"),
        ],
        quotes=[
            "brokerage charges are too high",
            "order execution is slow during market open",
            "the latest ui update broke the layout",
        ],
        actions=["Cut hidden charges.", "Speed up order execution.", "Fix the UI layout."],
        generated_by=generated_by,
    )


def _themes() -> list[Theme]:
    return [
        Theme(
            label="charges_fees",
            rank=1,
            quotes=[Quote(text="brokerage charges are too high", rating=1, store="play_store")],
        ),
        Theme(
            label="trading_products",
            rank=2,
            quotes=[Quote(text="order execution is slow during market open", rating=2, store="app_store")],
        ),
        Theme(
            label="app_ux_updates",
            rank=3,
            quotes=[Quote(text="the latest ui update broke the layout", rating=1, store="play_store")],
        ),
    ]


def test_render_produces_doc_and_email():
    rendered = render(_note(), _config(), _themes())
    assert rendered.ok
    assert not rendered.issues
    assert "Groww \u2014 Weekly Review Pulse (2026-07-05)" in rendered.doc_body
    assert "## Top Themes" in rendered.doc_body
    assert "## Real User Quotes" in rendered.doc_body
    assert "## Action Ideas" in rendered.doc_body
    assert rendered.subject == "Groww Weekly Review Pulse \u2014 2026-07-05"
    # email carries the placeholder for the Doc link (resolved in Phase 6)
    assert DOC_LINK_PLACEHOLDER in rendered.email_body


def test_quote_attribution_from_themes():
    rendered = render(_note(), _config(), _themes())
    assert "Play Store" in rendered.doc_body
    assert "1\u2605" in rendered.doc_body


def test_fallback_note_is_marked_non_llm():
    rendered = render(_note(generated_by="fallback"), _config(), _themes())
    assert "no LLM" in rendered.doc_body
    assert "no LLM" in rendered.email_body


def test_none_note_blocks_delivery():
    rendered = render(None, _config(), None)
    assert not rendered.ok
    assert rendered.issues


def test_pii_leak_blocks_delivery():
    note = _note()
    note.actions[0] = "Email the user at test@example.com about charges."
    rendered = render(note, _config(), _themes())
    assert not rendered.ok
    assert any("PII" in i for i in rendered.issues)


def test_markdown_in_quote_is_escaped():
    note = _note()
    note.quotes[0] = "charges are *way* too [high]"
    rendered = render(note, _config(), _themes())
    # the raw markdown control chars are escaped in the Doc body
    assert "\\*way\\*" in rendered.doc_body
    assert "\\[high\\]" in rendered.doc_body
