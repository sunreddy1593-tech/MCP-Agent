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
    """Audit record for a single pipeline run."""

    run_id: str
    product_id: str
    window_weeks: int
    counts: dict[str, int] = Field(default_factory=dict)
    doc_id: str | None = None
    doc_url: str | None = None
    draft_id: str | None = None
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
