"""Phase 8: end-to-end run on realistic exports + verification of ALL constraints.

Runs the full pipeline offline (no GROQ_API_KEY -> keyword theming + template
note) over a realistic two-store fixture that includes PII, an emoji review, a
Hindi review, a too-short review, and a duplicate. Delivery is exercised with a
fake Workspace MCP so the run produces a Doc append + a Gmail *draft* without
touching Google.

Constraints asserted (context.md / implementation-plan.md):
  - at most 5 themes; exactly the top 3 are ranked and carry quotes;
  - the note has exactly 3 themes / 3 quotes / 3 actions and is <= 250 words;
  - zero PII in the anonymized set, the note, or the rendered artifacts;
  - every emitted quote is verbatim from a source review (candidate pool);
  - Gmail is draft-only: send_gmail is never called.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from review_pulse.config import MCPConfig, OutputTargets, RunConfig, WindowConfig
from review_pulse.delivery.mcp_client import WorkspaceMcpClient
from review_pulse.orchestrator import run_pipeline
from review_pulse.pipeline.pii import contains_pii
from review_pulse.pipeline.validators import MAX_WORDS


def _recent(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


class FakeMcp:
    """Records tool calls; returns canned Doc/draft results, never sends."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def is_configured(self) -> bool:
        return True

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        assert name != "send_gmail", "draft-only: send_gmail must never be called"
        if name == "append_to_google_doc":
            return {"document_id": "DOC123", "status": "appended"}
        if name == "draft_gmail":
            return {"draft_id": "DRAFT1", "message_id": "M1", "status": "drafted"}
        raise AssertionError(f"unexpected tool {name}")


# A charges review carrying PII (email + phone) — must survive as clean text.
_CHARGES_PII = (
    "The brokerage charges here are too high please contact me at "
    "test@example.com or 9876543210 today"
)

_CSV_ROWS = [
    # charges_fees (negative-heavy)
    (1, "The brokerage charges here are far too high and the hidden fees keep rising every single month", _recent(5)),
    (1, "I hate the extra charges and commission fees they deduct without any clear explanation at all", _recent(6)),
    (2, "Brokerage fee is expensive compared to other apps and the charges are never transparent enough here", _recent(7)),
    (1, "Too many hidden charges and the fee structure feels unfair for small retail investors like me", _recent(8)),
    # exact duplicate of the first charges row (same text + date) -> deduped
    (1, "The brokerage charges here are far too high and the hidden fees keep rising every single month", _recent(5)),
    # trading_products (positive-heavy)
    (5, "Buying stocks and mutual fund orders work smoothly and the trading portfolio view is really helpful", _recent(9)),
    (4, "The stock trading experience is great and placing an order for shares feels fast and reliable", _recent(10)),
    (5, "Love the mutual fund and sip options investing in equity has never been this simple before", _recent(11)),
    (3, "Order execution for stocks is quick but sometimes the trade portfolio does not refresh properly", _recent(12)),
    # emoji -> dropped
    (1, "The app keeps crashing after every single update it is honestly terrible now \U0001f621", _recent(13)),
    # Hindi (Devanagari) -> dropped
    (1, "\u092f\u0939 \u0910\u092a \u092c\u0939\u0941\u0924 \u0916\u0930\u093e\u092c \u0939\u0948 \u0914\u0930 \u0936\u0941\u0932\u094d\u0915 \u092c\u0939\u0941\u0924 \u0905\u0927\u093f\u0915 \u0939\u0948 \u092f\u093e\u0930", _recent(14)),
    # too short (<8 words) -> dropped
    (1, "Way too expensive app", _recent(15)),
]

_JSON_ROWS = [
    # app_ux_updates (negative-heavy)
    (1, "The latest update broke the ui and the app now crashes every time i open the interface", _recent(6)),
    (2, "New design is slow and laggy the interface hangs often after the recent update rolled out", _recent(7)),
    (2, "App ui looks cluttered after the update and there are too many bugs and glitches lately here", _recent(8)),
    (2, "The interface update made navigation confusing and the layout feels buggy on my older phone now", _recent(9)),
    # charges_fees with PII -> scrubbed, still themed + quotable
    (1, _CHARGES_PII, _recent(10)),
]


def _write_exports(exports_dir: Path) -> None:
    exports_dir.mkdir(parents=True, exist_ok=True)
    lines = ["rating,body,updated_at"]
    for rating, body, when in _CSV_ROWS:
        lines.append(f'{rating},"{body}",{when}')
    (exports_dir / "app_store.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    data = [
        {"userName": "someone", "score": score, "content": body, "at": when}
        for score, body, when in _JSON_ROWS
    ]
    (exports_dir / "play_store.json").write_text(json.dumps(data), encoding="utf-8")


def _config(tmp_path: Path) -> RunConfig:
    return RunConfig(
        product_id="com.test.groww",
        product_name="Groww",
        window=WindowConfig(weeks=12),
        exports_dir=str(tmp_path / "exports"),
        store_dir=str(tmp_path / "store"),
        outputs=OutputTargets(doc_id="DOC123", email_to="me@example.com"),
        mcp=MCPConfig(transport="http", url="http://fake/mcp"),
    )


def _run_dir(config: RunConfig) -> Path:
    base = Path(config.store_dir)
    dirs = [p for p in base.iterdir() if p.is_dir()]
    assert len(dirs) == 1
    return dirs[0]


def test_e2e_pulse_satisfies_all_constraints(tmp_path, monkeypatch):
    config = _config(tmp_path)
    _write_exports(tmp_path / "exports")

    fake = FakeMcp()
    # deliver() builds its client via WorkspaceMcpClient.from_config(config); make
    # that return our fake so the run exercises real Docs-append + Gmail-draft
    # code paths without touching Google.
    monkeypatch.setattr(
        WorkspaceMcpClient, "from_config", classmethod(lambda cls, cfg: fake)
    )

    manifest = run_pipeline(config)
    run_dir = _run_dir(config)

    # --- run completed and delivered via MCP -----------------------------
    assert manifest.status == "completed"
    assert manifest.delivery_status == "delivered"
    assert manifest.doc_url == "https://docs.google.com/document/d/DOC123/edit"
    assert manifest.draft_id == "DRAFT1"

    # --- draft-only: a draft was created, nothing was ever sent ----------
    called = [name for name, _ in fake.calls]
    assert "append_to_google_doc" in called
    assert "draft_gmail" in called
    assert "send_gmail" not in called

    # --- ingestion + filters (emoji / Hindi / short dropped, dup deduped)-
    raw = json.loads((run_dir / "raw.json").read_text(encoding="utf-8"))
    normalized = json.loads((run_dir / "normalized.json").read_text(encoding="utf-8"))
    assert len(raw) == len(_CSV_ROWS) + len(_JSON_ROWS)  # 17 ingested
    assert len(normalized) == 13  # 3 filtered (emoji/Hindi/short) + 1 duplicate
    assert manifest.counts["normalized"] == 13

    # --- zero PII in the anonymized set ----------------------------------
    norm_texts = [r["text"] for r in normalized]
    assert any("test@example.com" in r["text"] for r in raw)  # PII was present raw
    for text in norm_texts:
        assert not contains_pii(text), f"PII leaked into normalized: {text!r}"
    assert any("[EMAIL]" in t and "[PHONE]" in t for t in norm_texts)  # it was scrubbed

    # --- theming: <= 5 themes; exactly the top 3 ranked, each with quotes -
    themes = json.loads((run_dir / "themes.json").read_text(encoding="utf-8"))
    ranked = [t for t in themes if t["rank"] in (1, 2, 3)]
    real = [t for t in themes if t["label"] != "other"]
    assert len(real) <= 5
    assert len(ranked) == 3
    candidate_pool: set[str] = set()
    for theme in ranked:
        assert theme["quotes"], f"top theme {theme['label']} has no quote"
        for q in theme["quotes"]:
            candidate_pool.add(q["text"].strip())

    # --- note: structure + word budget + generation path -----------------
    note = json.loads((run_dir / "note.json").read_text(encoding="utf-8"))
    assert len(note["themes"]) == 3
    assert len(note["quotes"]) == 3
    assert len(note["actions"]) == 3
    assert note["word_count"] <= MAX_WORDS
    assert note["generated_by"] == "fallback"

    # --- quotes are verbatim + traceable to a source review --------------
    norm_set = {t.strip() for t in norm_texts}
    for quote in note["quotes"]:
        q = quote.strip()
        assert any(q == c or q in c for c in candidate_pool), "quote not from candidate pool"
        assert q in norm_set, "quote not traceable to a normalized source review"
        assert not contains_pii(q)

    # --- rendered artifacts carry no PII ---------------------------------
    for name in ("note.md", "email.txt"):
        rendered = (run_dir / name).read_text(encoding="utf-8")
        # the email link placeholder is resolved to a docs URL, not PII
        assert not contains_pii(rendered), f"PII in {name}"
