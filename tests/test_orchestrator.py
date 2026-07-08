"""Phase 7 tests: full orchestration, persistence, timings, and failure replay.

These run fully offline: no GROQ_API_KEY (keyword theming + template note) and no
MCP server (delivery degrades to local artifacts with status 'pending').
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from review_pulse.config import MCPConfig, OutputTargets, RunConfig, WindowConfig
from review_pulse.orchestrator import run_pipeline
from review_pulse.pipeline import theming


def _recent(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


# Three clearly-separable themes, each with quotable (8-60 word) reviews.
_ROWS = [
    # charges_fees (negative-heavy)
    (1, "Fees", "The brokerage charges here are far too high and the hidden fees keep rising every single month"),
    (1, "Charges", "I hate the extra charges and commission fees they deduct without any clear explanation at all"),
    (2, "Fee", "Brokerage fee is expensive compared to other apps and the charges are never transparent enough here"),
    (1, "Scam", "Too many hidden charges and the fee structure feels unfair for small retail investors like me"),
    # trading_products (positive-heavy)
    (5, "Great", "Buying stocks and mutual fund orders work smoothly and the trading portfolio view is really helpful"),
    (4, "Nice", "The stock trading experience is great and placing an order for shares feels fast and reliable"),
    (5, "Love", "Love the mutual fund and sip options, investing in equity has never been this simple before"),
    (3, "Ok", "Order execution for stocks is quick but sometimes the trade portfolio does not refresh properly"),
    # app_ux_updates (negative-heavy)
    (1, "Broken", "The latest update broke the ui and the app now crashes every time i open the interface"),
    (2, "Slow", "New design is slow and laggy, the interface hangs often after the recent update rolled out"),
    (2, "Buggy", "App ui looks cluttered after the update and there are too many bugs and glitches lately here"),
    (2, "Confusing", "The interface update made navigation confusing and the layout feels buggy on my older phone now"),
]


def _write_exports(exports_dir) -> None:
    exports_dir.mkdir(parents=True, exist_ok=True)
    lines = ["rating,title,body,updated_at"]
    for rating, title, body in _ROWS:
        lines.append(f'{rating},{title},"{body}",{_recent(5)}')
    (exports_dir / "app_store.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _config(tmp_path) -> RunConfig:
    return RunConfig(
        product_id="com.test.groww",
        product_name="Groww",
        window=WindowConfig(weeks=12),
        exports_dir=str(tmp_path / "exports"),
        store_dir=str(tmp_path / "store"),
        outputs=OutputTargets(doc_id=None, email_to="me@example.com"),
        mcp=MCPConfig(transport="http", url=None),  # not configured -> pending
    )


def _run_dir(config: RunConfig) -> Path:
    base = Path(config.store_dir)
    dirs = [p for p in base.iterdir() if p.is_dir()] if base.exists() else []
    assert len(dirs) == 1, f"expected exactly one run dir, got {dirs}"
    return dirs[0]


def test_full_run_persists_all_artifacts_and_manifest(tmp_path):
    config = _config(tmp_path)
    _write_exports(tmp_path / "exports")

    manifest = run_pipeline(config)

    assert manifest.status == "completed"
    assert manifest.failed_stage is None and manifest.error is None
    # Offline delivery degrades gracefully.
    assert manifest.delivery_status == "pending"
    # Counts reflect the fixture.
    assert manifest.counts["ingested"] == len(_ROWS)
    assert manifest.counts["normalized"] == len(_ROWS)
    assert manifest.counts["themes"] == 3
    # Every stage is timed and totalled.
    assert set(manifest.timings) == {
        "ingest", "normalize", "theming", "summarize", "render", "deliver"
    }
    assert manifest.duration_seconds is not None

    run_dir = _run_dir(config)
    for name in (
        "raw.json", "normalized.json", "themes.json",
        "note.json", "note.md", "email.txt", "manifest.json",
    ):
        assert (run_dir / name).exists(), f"missing artifact {name}"

    # Raw holds all ingested rows; normalized is the anonymized set.
    raw = json.loads((run_dir / "raw.json").read_text(encoding="utf-8"))
    assert len(raw) == len(_ROWS)


def test_failed_stage_writes_partial_manifest_for_replay(tmp_path, monkeypatch):
    config = _config(tmp_path)
    _write_exports(tmp_path / "exports")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("theming exploded")

    monkeypatch.setattr(theming, "build_themes", _boom)

    with pytest.raises(RuntimeError, match="theming exploded"):
        run_pipeline(config)

    run_dir = _run_dir(config)
    # Artifacts from completed stages remain for replay...
    assert (run_dir / "raw.json").exists()
    assert (run_dir / "normalized.json").exists()
    # ...but theming never produced its output.
    assert not (run_dir / "themes.json").exists()

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["failed_stage"] == "theming"
    assert "theming exploded" in manifest["error"]
    # Stages that ran before the failure are timed.
    assert "ingest" in manifest["timings"]
    assert "normalize" in manifest["timings"]
    assert "theming" in manifest["timings"]
