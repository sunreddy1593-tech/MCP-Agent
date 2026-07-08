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
from datetime import datetime, timezone

from review_pulse.config import RunConfig, load_config
from review_pulse.delivery.docs_client import DocsClient
from review_pulse.delivery.gmail_client import GmailClient
from review_pulse.llm.client import LlmClient
from review_pulse.pipeline import normalize, render, summarize, theming
from review_pulse.sources import download as downloader
from review_pulse.sources.app_store import AppStoreAdapter
from review_pulse.sources.play_store import PlayStoreAdapter
from review_pulse.store.local_store import LocalStore, RunManifest

logger = logging.getLogger("review_pulse")


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")


def run_pipeline(config: RunConfig) -> RunManifest:
    """Execute the pipeline stages in order. Phase 0 stages are no-ops."""
    run_id = _new_run_id()
    logger.info("Starting run %s for product '%s'", run_id, config.product_name)

    logger.info("Loaded run config:\n%s", json.dumps(config.redacted_dict(), indent=2))

    store = LocalStore(config.store_dir)
    llm = LlmClient.from_config(config)

    # 1. Ingest (Phase 1)
    sources = [AppStoreAdapter(), PlayStoreAdapter()]
    reviews: list = []
    for source in sources:
        reviews.extend(source.fetch(config))
    ingested_count = len(reviews)
    logger.info("Ingested %d reviews total (merged, in-window)", ingested_count)

    # 2. Normalize + PII strip (Phase 2)
    reviews = normalize.normalize(reviews, config)
    reviews_path = store.write_reviews(run_id, "normalized.json", reviews)
    logger.info("Persisted %d anonymized reviews to %s", len(reviews), reviews_path)

    # 3. Theming (Phase 3)
    themes = theming.build_themes(reviews, config, llm)
    themes_path = store.write_json(
        run_id, "themes.json", [t.model_dump(mode="json") for t in themes]
    )
    top_themes = [t for t in themes if t.rank in (1, 2, 3)]
    logger.info("Persisted %d themes to %s", len(themes), themes_path)

    # 4. Summarize via Groq (Phase 4)
    note = summarize.summarize(top_themes, config, llm)
    if note is not None:
        note_path = store.write_json(run_id, "note.json", note.model_dump(mode="json"))
        logger.info(
            "Weekly note ready (%s, %d words) -> %s",
            note.generated_by,
            note.word_count,
            note_path,
        )

    # 5. Render (Phase 5)
    rendered = render.render(note, config, top_themes)
    if rendered.doc_body:
        store.write_text(run_id, "note.md", rendered.doc_body)
        store.write_text(run_id, "email.txt", rendered.email_body)
        logger.info("Persisted rendered note.md and email.txt for run %s", run_id)

    # 6. Deliver via MCP (Phase 6)
    docs_result: dict = {"doc_id": None, "doc_url": None}
    gmail_result: dict = {"draft_id": None}
    if rendered.ok:
        docs_result = DocsClient().publish(rendered, config)
        gmail_result = GmailClient().create_draft(
            rendered, docs_result.get("doc_url"), config
        )
    else:
        logger.warning(
            "Delivery blocked (%s); rendered artifacts kept locally with status 'pending'",
            "; ".join(rendered.issues),
        )

    # 7. Record manifest (Phase 7)
    manifest = RunManifest(
        run_id=run_id,
        product_id=config.product_id,
        window_weeks=config.window.weeks,
        counts={
            "ingested": ingested_count,
            "normalized": len(reviews),
            "themes": sum(1 for t in themes if t.rank > 0),
        },
        doc_id=docs_result.get("doc_id"),
        doc_url=docs_result.get("doc_url"),
        draft_id=gmail_result.get("draft_id"),
    )
    manifest_path = store.write_manifest(manifest)
    logger.info("Wrote run manifest to %s", manifest_path)
    logger.info("Run %s complete (Phase 0 no-op stages).", run_id)
    return manifest


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

        run_pipeline(config)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
