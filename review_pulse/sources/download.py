"""Download public reviews from the App Store and Play Store into export files.

Sources (both public, no login):
  - App Store: Apple's customer-reviews RSS feed (JSON).
  - Play Store: the `google-play-scraper` package (public listing data).

Results are written to `{exports_dir}/app_store.json` and
`{exports_dir}/play_store.json` in a neutral shape the adapters already read
(`rating, title, text, date`). Reviewer-identity fields are never written.

App identifiers can be given explicitly in config, or resolved from a search
query (app name) via the iTunes Search API / google-play-scraper search.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from review_pulse.config import RunConfig

logger = logging.getLogger(__name__)

_USER_AGENT = "review-pulse/0.1 (+https://example.com)"
_APP_STORE_MAX_PAGES = 10  # Apple caps the RSS feed at ~10 pages / 500 reviews.


def _http_get_json(url: str, timeout: int = 30) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https only
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------- #
# App identifier resolution
# --------------------------------------------------------------------------- #
def resolve_app_store_id(term: str, country: str = "us") -> str | None:
    """Resolve an App Store numeric track id from an app name via iTunes Search."""
    query = urllib.parse.urlencode(
        {"term": term, "country": country, "entity": "software", "limit": 1}
    )
    data = _http_get_json(f"https://itunes.apple.com/search?{query}")
    results = data.get("results") or []
    if not results:
        return None
    track_id = results[0].get("trackId")
    name = results[0].get("trackName")
    logger.info("Resolved App Store id %s for '%s' (matched '%s')", track_id, term, name)
    return str(track_id) if track_id else None


def resolve_play_store_id(term: str, lang: str = "en", country: str = "us") -> str | None:
    """Resolve a Play Store package name from an app name via google-play-scraper."""
    try:
        from google_play_scraper import search  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "google-play-scraper is not installed. Run: pip install -r requirements.txt"
        ) from exc

    results = search(term, lang=lang, country=country, n_hits=5)
    # Skip featured/sponsored hits that carry no package id.
    for result in results or []:
        app_id = result.get("appId")
        if app_id:
            logger.info(
                "Resolved Play Store id %s for '%s' (matched '%s')",
                app_id,
                term,
                result.get("title"),
            )
            return app_id
    return None


# --------------------------------------------------------------------------- #
# Downloaders
# --------------------------------------------------------------------------- #
def download_app_store(
    app_id: str, country: str, max_reviews: int, out_path: Path
) -> int:
    """Download recent App Store reviews via the public RSS feed."""
    records: list[dict[str, Any]] = []
    for page in range(1, _APP_STORE_MAX_PAGES + 1):
        url = (
            f"https://itunes.apple.com/{country}/rss/customerreviews/"
            f"page={page}/id={app_id}/sortby=mostrecent/json"
        )
        try:
            data = _http_get_json(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[app_store] page %d fetch failed: %s", page, exc)
            break

        entries = data.get("feed", {}).get("entry", [])
        if isinstance(entries, dict):
            entries = [entries]
        # The first entry on page 1 is app metadata (no 'im:rating') — skipped below.
        review_entries = [e for e in entries if isinstance(e, dict) and "im:rating" in e]
        if not review_entries:
            break

        for entry in review_entries:
            records.append(
                {
                    "rating": _label(entry.get("im:rating")),
                    "title": _label(entry.get("title")),
                    "text": _label(entry.get("content")),
                    "date": _label(entry.get("updated")),
                }
            )
        if len(records) >= max_reviews:
            break

    records = records[:max_reviews]
    _write(out_path, records)
    logger.info("[app_store] downloaded %d reviews -> %s", len(records), out_path)
    return len(records)


def download_play_store(
    app_id: str,
    lang: str,
    country: str,
    max_reviews: int,
    out_path: Path,
    since: datetime | None = None,
) -> int:
    """Download recent Play Store reviews via google-play-scraper.

    Paginates newest-first using the continuation token and stops once reviews
    predate `since` (the window cutoff) or the `max_reviews` safety cap is hit.
    """
    try:
        from google_play_scraper import Sort, reviews  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "google-play-scraper is not installed. Run: pip install -r requirements.txt"
        ) from exc

    # google-play-scraper returns naive datetimes; compare in naive form.
    since_naive = since.replace(tzinfo=None) if since else None
    batch = 200
    token = None
    records: list[dict[str, Any]] = []
    reached_cutoff = False

    while len(records) < max_reviews and not reached_cutoff:
        result, token = reviews(
            app_id,
            lang=lang,
            country=country,
            sort=Sort.NEWEST,
            count=min(batch, max_reviews - len(records)),
            continuation_token=token,
        )
        if not result:
            break

        for item in result:
            at = item.get("at")
            if since_naive and at is not None and at < since_naive:
                reached_cutoff = True
                continue  # older than window; drop and stop after this batch
            date = at.isoformat() if hasattr(at, "isoformat") else (str(at) if at else None)
            records.append(
                {
                    "rating": item.get("score"),
                    "title": None,
                    "text": item.get("content"),
                    "date": date,
                }
            )

        if token is None:
            break

    _write(out_path, records)
    if since_naive and not reached_cutoff and len(records) >= max_reviews:
        logger.warning(
            "[play_store] hit max_reviews cap (%d) before reaching the %s cutoff; "
            "raise sources.max_reviews to cover the full window",
            max_reviews,
            since_naive.date().isoformat(),
        )
    logger.info("[play_store] downloaded %d reviews -> %s", len(records), out_path)
    return len(records)


def run_download(
    config: "RunConfig", query: str | None = None
) -> dict[str, int]:
    """Resolve identifiers as needed and download from both stores."""
    exports = Path(config.exports_dir)
    exports.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    # Only pull reviews back to the configured window (last N weeks).
    since = datetime.now(timezone.utc) - timedelta(weeks=config.window.weeks)
    logger.info("Downloading reviews back to %s (last %d weeks)", since.date(), config.window.weeks)

    # App Store
    apple = config.sources.app_store
    app_store_id = apple.app_id
    if not app_store_id and query:
        app_store_id = resolve_app_store_id(query, apple.country)
    if app_store_id:
        counts["app_store"] = download_app_store(
            app_store_id, apple.country, config.sources.max_reviews,
            exports / "app_store.json",
        )
    else:
        logger.warning("[app_store] no app id (and no query to resolve); skipping")

    # Play Store
    play = config.sources.play_store
    play_store_id = play.app_id
    if not play_store_id and query:
        play_store_id = resolve_play_store_id(query, play.lang, play.country)
    if play_store_id:
        counts["play_store"] = download_play_store(
            play_store_id, play.lang, play.country, config.sources.max_reviews,
            exports / "play_store.json", since=since,
        )
    else:
        logger.warning("[play_store] no app id (and no query to resolve); skipping")

    return counts


def _label(node: Any) -> Any:
    """Apple RSS wraps values as {'label': value}; unwrap defensively."""
    if isinstance(node, dict):
        return node.get("label")
    return node


def _write(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
