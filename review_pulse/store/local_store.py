"""Local filesystem store for run artifacts and the run manifest.

Enables replay, inspection, and idempotent delivery (Doc/draft ids per run).
Phase 0 provides the manifest model and directory handling; stages write their
intermediate artifacts here in later phases.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class RunManifest(BaseModel):
    """Audit record for a single pipeline run.

    Written at the end of every run — including failed ones — so a run is always
    diagnosable and replayable from the persisted artifacts.
    """

    run_id: str
    product_id: str
    window_weeks: int
    # "completed" (ran to the end) | "failed" (a stage raised). Delivery outcome
    # is tracked separately in `delivery_status`.
    status: str = "completed"
    counts: dict[str, int] = Field(default_factory=dict)
    week_of: str | None = None
    doc_id: str | None = None
    doc_url: str | None = None
    draft_id: str | None = None
    # "delivered" | "partial" | "pending" — set by the Phase 6 delivery layer.
    delivery_status: str = "pending"
    # Per-stage wall-clock timings (seconds) and their sum, for auditing.
    timings: dict[str, float] = Field(default_factory=dict)
    duration_seconds: float | None = None
    # Stage + message when status == "failed"; None on success.
    failed_stage: str | None = None
    error: str | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class LocalStore:
    """Manages the on-disk layout for a run's artifacts."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)

    def run_dir(self, run_id: str) -> Path:
        path = self.base_dir / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_manifest(self, manifest: RunManifest) -> Path:
        path = self.run_dir(manifest.run_id) / "manifest.json"
        path.write_text(
            json.dumps(manifest.model_dump(), indent=2), encoding="utf-8"
        )
        return path

    def write_json(self, run_id: str, filename: str, data: Any) -> Path:
        """Persist an arbitrary JSON-serializable artifact for a run."""
        path = self.run_dir(run_id) / filename
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return path

    def write_text(self, run_id: str, filename: str, text: str) -> Path:
        """Persist a plain-text artifact (e.g. rendered note.md / email.txt)."""
        path = self.run_dir(run_id) / filename
        path.write_text(text, encoding="utf-8")
        return path

    def write_reviews(self, run_id: str, filename: str, reviews: list) -> Path:
        """Persist a list of pydantic Review models as JSON."""
        payload = [
            r.model_dump(mode="json") if hasattr(r, "model_dump") else r
            for r in reviews
        ]
        return self.write_json(run_id, filename, payload)

    # --- Delivery idempotency ledger -------------------------------------
    # A small cross-run ledger (keyed by doc/draft + week_of) so re-running the
    # same week does not append a duplicate Doc section or create a second draft.
    # It lives at the store root, not under a run dir, because it spans runs.

    def _ledger_path(self) -> Path:
        return self.base_dir / "delivery_ledger.json"

    def read_delivery_ledger(self) -> dict[str, Any]:
        """Return the delivery ledger, or an empty dict if absent/corrupt."""
        path = self._ledger_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def write_delivery_marker(self, key: str, value: dict[str, Any]) -> Path:
        """Record a delivery marker (read-modify-write on the ledger file)."""
        ledger = self.read_delivery_ledger()
        ledger[key] = value
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self._ledger_path()
        path.write_text(
            json.dumps(ledger, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return path
