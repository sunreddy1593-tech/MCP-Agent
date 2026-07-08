"""Orchestrator: entry point that runs the full weekly pipeline.

Phase 0: sequences every stage as a no-op and prints the loaded run config so
the skeleton runs end-to-end. Later phases fill in each stage.

Usage:
    review-pulse run --config config/run_config.example.yaml
    python -m review_pulse.orchestrator run --config config/run_config.example.yaml
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone

from review_pulse.config import RunConfig, load_config
from review_pulse.delivery.deliver import DeliveryResult, deliver
from review_pulse.llm.client import LlmClient
from review_pulse.pipeline import normalize, render, summarize, theming
from review_pulse.sources import download as downloader
from review_pulse.sources.app_store import AppStoreAdapter
from review_pulse.sources.play_store import PlayStoreAdapter
from review_pulse.store.local_store import LocalStore, RunManifest

logger = logging.getLogger("review_pulse")


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")


class _StageTimer:
    """Times each pipeline stage and remembers which one is in flight.

    On failure the orchestrator reads `current` to record the failing stage in
    the manifest, making runs diagnosable and replayable.
    """

    def __init__(self) -> None:
        self.timings: dict[str, float] = {}
        self.current: str | None = None

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        self.current = name
        logger.info("[stage] %s: starting", name)
        start = time.perf_counter()
        ok = False
        try:
            yield
            ok = True
        finally:
            elapsed = round(time.perf_counter() - start, 3)
            self.timings[name] = elapsed
            if ok:
                logger.info("[stage] %s: done in %.3fs", name, elapsed)
            else:
                logger.error("[stage] %s: FAILED after %.3fs", name, elapsed)


def run_pipeline(config: RunConfig) -> RunManifest:
    """Run the full pipeline (ingest -> deliver), persisting artifacts per stage.

    Every stage is timed and its output persisted to the run store, so a run can
    be inspected or replayed. If any stage raises, the error is logged with the
    failing stage's context and a **partial manifest** (`status="failed"`) is
    written before re-raising — failures are diagnosable and replayable.
    """
    run_id = _new_run_id()
    started = time.perf_counter()
    logger.info("Starting run %s for product '%s'", run_id, config.product_name)
    logger.info("Loaded run config:\n%s", json.dumps(config.redacted_dict(), indent=2))

    store = LocalStore(config.store_dir)
    llm = LlmClient.from_config(config)
    timer = _StageTimer()

    # State captured incrementally so the manifest is complete even on failure.
    counts: dict[str, int] = {"ingested": 0, "normalized": 0, "themes": 0}
    week_of = date.today().isoformat()
    delivery: DeliveryResult | None = None

    try:
        # 1. Ingest (Phase 1) — persist the raw merged reviews.
        with timer.stage("ingest"):
            reviews: list = []
            for source in (AppStoreAdapter(), PlayStoreAdapter()):
                reviews.extend(source.fetch(config))
            counts["ingested"] = len(reviews)
            store.write_reviews(run_id, "raw.json", reviews)
            logger.info("Ingested %d reviews total (merged, in-window)", counts["ingested"])

        # 2. Normalize + PII strip (Phase 2) — persist the anonymized set.
        with timer.stage("normalize"):
            reviews = normalize.normalize(reviews, config)
            counts["normalized"] = len(reviews)
            store.write_reviews(run_id, "normalized.json", reviews)
            logger.info("Persisted %d anonymized reviews", counts["normalized"])

        # 3. Theming (Phase 3) — persist themes.json.
        with timer.stage("theming"):
            themes = theming.build_themes(reviews, config, llm)
            store.write_json(
                run_id, "themes.json", [t.model_dump(mode="json") for t in themes]
            )
            top_themes = [t for t in themes if t.rank in (1, 2, 3)]
            counts["themes"] = sum(1 for t in themes if t.rank > 0)
            logger.info("Persisted %d themes (%d ranked)", len(themes), counts["themes"])

        # 4. Summarize via Groq (Phase 4) — persist note.json.
        with timer.stage("summarize"):
            note = summarize.summarize(top_themes, config, llm)
            if note is not None:
                store.write_json(run_id, "note.json", note.model_dump(mode="json"))
                week_of = note.week_of
                logger.info(
                    "Weekly note ready (%s, %d words)",
                    note.generated_by, note.word_count,
                )

        # 5. Render (Phase 5) — persist note.md + email.txt.
        with timer.stage("render"):
            rendered = render.render(note, config, top_themes)
            if rendered.doc_body:
                store.write_text(run_id, "note.md", rendered.doc_body)
                store.write_text(run_id, "email.txt", rendered.email_body)
                logger.info("Persisted rendered note.md and email.txt")

        # 6. Deliver via MCP (Phase 6) — idempotent, degrades to local artifacts.
        with timer.stage("deliver"):
            delivery = deliver(rendered, config, store, week_of)
            logger.info(
                "Delivery status: %s%s",
                delivery.status,
                f" ({'; '.join(delivery.issues)})" if delivery.issues else "",
            )

    except Exception as exc:
        # Fail loudly with context, but persist a manifest so the run (and its
        # partial artifacts already on disk) can be diagnosed and replayed.
        duration = round(time.perf_counter() - started, 3)
        logger.exception(
            "Run %s FAILED during '%s' stage after %.3fs: %s",
            run_id, timer.current, duration, exc,
        )
        manifest = _build_manifest(
            run_id, config, counts, week_of, delivery, timer, duration,
            status="failed", failed_stage=timer.current,
            error=f"{type(exc).__name__}: {exc}",
        )
        path = store.write_manifest(manifest)
        logger.error("Wrote partial (failed) manifest for replay to %s", path)
        raise

    # 7. Record manifest (Phase 7).
    duration = round(time.perf_counter() - started, 3)
    manifest = _build_manifest(
        run_id, config, counts, week_of, delivery, timer, duration, status="completed"
    )
    manifest_path = store.write_manifest(manifest)
    logger.info("Wrote run manifest to %s", manifest_path)
    logger.info(
        "Run %s complete in %.3fs (delivery: %s).",
        run_id, duration, delivery.status if delivery else "n/a",
    )
    return manifest


def _build_manifest(
    run_id: str,
    config: RunConfig,
    counts: dict[str, int],
    week_of: str,
    delivery: DeliveryResult | None,
    timer: _StageTimer,
    duration: float,
    *,
    status: str,
    failed_stage: str | None = None,
    error: str | None = None,
) -> RunManifest:
    """Assemble a RunManifest from whatever state a run has reached."""
    return RunManifest(
        run_id=run_id,
        product_id=config.product_id,
        window_weeks=config.window.weeks,
        status=status,
        counts=counts,
        week_of=week_of,
        doc_id=delivery.doc_id if delivery else None,
        doc_url=delivery.doc_url if delivery else None,
        draft_id=delivery.draft_id if delivery else None,
        delivery_status=delivery.status if delivery else "pending",
        timings=timer.timings,
        duration_seconds=duration,
        failed_stage=failed_stage,
        error=error,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review-pulse",
        description="Weekly Mobile-Store Review Pulse pipeline.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="Run the weekly pulse pipeline.")
    run_cmd.add_argument(
        "--config",
        default="config/run_config.example.yaml",
        help="Path to the run config YAML.",
    )
    run_cmd.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ...).",
    )

    dl_cmd = sub.add_parser(
        "download", help="Download public reviews into the exports directory."
    )
    dl_cmd.add_argument(
        "--config",
        default="config/run_config.example.yaml",
        help="Path to the run config YAML.",
    )
    dl_cmd.add_argument(
        "--query",
        default=None,
        help="App name to resolve store ids from when not set in config.",
    )
    dl_cmd.add_argument(
        "--max",
        type=int,
        default=None,
        help="Override max reviews per store.",
    )
    dl_cmd.add_argument("--log-level", default="INFO", help="Logging level.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if args.command in ("run", "download"):
        try:
            config = load_config(args.config)
        except FileNotFoundError as exc:
            logger.error("%s", exc)
            return 1

        if args.command == "download":
            if args.max is not None:
                config.sources.max_reviews = args.max
            counts = downloader.run_download(config, query=args.query)
            if not counts:
                logger.error(
                    "No reviews downloaded. Set sources.app_store.app_id / "
                    "sources.play_store.app_id in config or pass --query."
                )
                return 1
            logger.info("Download complete: %s", counts)
            return 0

        try:
            manifest = run_pipeline(config)
        except Exception:  # noqa: BLE001 - already logged with context + manifest
            logger.error(
                "Run failed. A partial manifest and any completed-stage artifacts "
                "were persisted under '%s' for diagnosis/replay.",
                config.store_dir,
            )
            return 1
        return 0 if manifest.status == "completed" else 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
