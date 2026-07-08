"""Theming / clustering (Phase 3).

Groups normalized reviews into <=5 themes (plus an "Other" bucket), ranks them
by volume x severity, selects the top 3, and attaches candidate verbatim quotes.

Classification strategy:
  - Primary: **batched Groq** classification (requires GROQ_API_KEY). Reviews are
    sent ~40 at a time with the taxonomy + a keyword hint; the LLM returns a
    theme label per review as JSON.
  - Fallback: if no LLM/key is available, a deterministic **keyword pre-pass**
    assigns themes (or "Other") so the pipeline still runs offline / in tests.

Theme labels come from config; human-readable descriptions and keyword lexicons
for the known Groww labels live here.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from typing import TYPE_CHECKING

from review_pulse.models import Quote, Review, Theme

if TYPE_CHECKING:
    from review_pulse.config import RunConfig
    from review_pulse.llm.client import LlmClient

logger = logging.getLogger(__name__)

OTHER = "other"
BATCH_SIZE = 40
MAX_QUOTES_PER_THEME = 5
NEGATIVE_RATINGS = {1, 2}

# Human-readable descriptions for known labels (used in the LLM prompt).
THEME_DESCRIPTIONS: dict[str, str] = {
    "charges_fees": "Brokerage, fees, hidden/extra charges, commissions, AMC/DP charges",
    "trading_products": "Stocks, mutual funds, SIPs, IPOs, orders, trading experience, portfolio",
    "app_ux_updates": "App UI/interface, design, updates, crashes, bugs, slowness, layout",
    "customer_support": "Customer care, support response, ticket resolution, complaints",
    "withdrawals_payments": "Withdrawals, deposits, UPI, redemptions, refunds, bank transfers",
}

# Keyword lexicon per label for the deterministic pre-pass / offline fallback.
LEXICON: dict[str, list[str]] = {
    "charges_fees": [
        "charge", "charges", "brokerage", "fee", "fees", "hidden charge",
        "amc", "dp charge", "commission", "expensive", "extra charge",
    ],
    "trading_products": [
        "stock", "share", "mutual fund", "fund", "sip", "ipo", "order",
        "trade", "trading", "dividend", "portfolio", "invest", "equity",
    ],
    "app_ux_updates": [
        "update", "ui", "interface", "design", "layout", "crash", "bug",
        "glitch", "slow", "lag", "hang", "dark mode", "landscape",
    ],
    "customer_support": [
        "support", "customer care", "customer service", "service", "response",
        "ticket", "resolve", "complaint", "help", "agent", "grievance",
    ],
    "withdrawals_payments": [
        "withdraw", "withdrawal", "deposit", "upi", "payment", "redeem",
        "redemption", "transfer", "refund", "credited", "debited", "bank",
    ],
}


def _keyword_theme(text: str, labels: list[str]) -> str:
    """Return the best keyword-matched label for `text`, or OTHER."""
    low = text.lower()
    best_label = OTHER
    best_hits = 0
    for label in labels:
        hits = sum(low.count(kw) for kw in LEXICON.get(label, []))
        if hits > best_hits:
            best_hits = hits
            best_label = label
    return best_label


def _classify_keyword(reviews: list[Review], labels: list[str]) -> list[str]:
    return [_keyword_theme(r.text, labels) for r in reviews]


def _classify_groq(
    reviews: list[Review], labels: list[str], llm: "LlmClient"
) -> list[str]:
    """Classify reviews via batched Groq calls. Falls back to keyword on error."""
    assignments: list[str] = [OTHER] * len(reviews)
    valid = set(labels) | {OTHER}
    taxonomy = "\n".join(
        f"- {label}: {THEME_DESCRIPTIONS.get(label, label)}" for label in labels
    )
    system = (
        "You are a precise classifier for mobile app reviews. Assign each review "
        "to exactly one theme from the taxonomy, or 'other' if none fit. "
        "Respond ONLY with JSON."
    )

    for start in range(0, len(reviews), BATCH_SIZE):
        batch = reviews[start : start + BATCH_SIZE]
        lines = []
        for i, review in enumerate(batch):
            hint = _keyword_theme(review.text, labels)
            lines.append(
                f'{i}. (hint: {hint}) "{review.text[:400]}"'
            )
        user = (
            f"Taxonomy:\n{taxonomy}\n- other: none of the above\n\n"
            f"Classify these {len(batch)} reviews. Return JSON of the form "
            '{"assignments": [{"i": 0, "theme": "<label>"}, ...]} covering every '
            f"index 0..{len(batch) - 1}.\n\nReviews:\n" + "\n".join(lines)
        )
        try:
            raw = llm.complete_json(system, user)
            data = json.loads(raw)
            for item in data.get("assignments", []):
                idx = item.get("i")
                theme = item.get("theme")
                if isinstance(idx, int) and 0 <= idx < len(batch):
                    assignments[start + idx] = theme if theme in valid else OTHER
        except Exception as exc:  # noqa: BLE001 - degrade gracefully per batch
            logger.warning(
                "[theming] Groq batch %d-%d failed (%s); using keyword fallback",
                start,
                start + len(batch),
                exc,
            )
            for i, review in enumerate(batch):
                assignments[start + i] = _keyword_theme(review.text, labels)

        logger.info(
            "[theming] classified %d/%d reviews", min(start + BATCH_SIZE, len(reviews)),
            len(reviews),
        )
    return assignments


def _canonical(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _select_quotes(reviews: list[Review], indices: list[int], negative_theme: bool) -> list[Quote]:
    """Pick representative, de-duplicated verbatim quotes for a theme."""
    ordered = sorted(
        indices,
        key=lambda i: (
            # For pain-point themes prefer negative reviews first.
            (reviews[i].rating or 3) not in NEGATIVE_RATINGS if negative_theme else False,
            # Prefer medium-length, informative quotes (~12-40 words).
            abs(len(reviews[i].text.split()) - 20),
        ),
    )
    quotes: list[Quote] = []
    seen: set[str] = set()
    for i in ordered:
        review = reviews[i]
        words = len(review.text.split())
        if words < 8 or words > 60:
            continue
        key = _canonical(review.text)
        if key in seen:
            continue
        seen.add(key)
        quotes.append(
            Quote(
                text=review.text,
                rating=review.rating,
                date=review.date,
                store=review.store,
            )
        )
        if len(quotes) >= MAX_QUOTES_PER_THEME:
            break
    return quotes


def build_themes(
    reviews: list[Review],
    config: "RunConfig",
    llm: "LlmClient | None" = None,
) -> list[Theme]:
    """Classify, aggregate, rank, and attach quotes. Returns themes ranked desc."""
    if not reviews:
        logger.info("[theming] no reviews to theme")
        return []

    labels = list(config.themes.labels)

    use_llm = llm is not None and getattr(llm, "api_key", None)
    if use_llm:
        logger.info("[theming] classifying %d reviews via batched Groq", len(reviews))
        assignments = _classify_groq(reviews, labels, llm)
    else:
        logger.warning(
            "[theming] no GROQ_API_KEY; using deterministic keyword fallback"
        )
        assignments = _classify_keyword(reviews, labels)

    grouped: dict[str, list[int]] = defaultdict(list)
    for idx, label in enumerate(assignments):
        grouped[label].append(idx)

    themes: list[Theme] = []
    for label, indices in grouped.items():
        ratings = [reviews[i].rating for i in indices if reviews[i].rating is not None]
        avg = sum(ratings) / len(ratings) if ratings else None
        neg = sum(1 for r in ratings if r in NEGATIVE_RATINGS)
        negative_share = neg / len(ratings) if ratings else 0.0
        # Volume x severity: negative-heavy themes surface higher.
        score = len(indices) * (1.0 + negative_share)
        themes.append(
            Theme(
                label=label,
                description=THEME_DESCRIPTIONS.get(label, ""),
                count=len(indices),
                avg_rating=round(avg, 2) if avg is not None else None,
                negative_share=round(negative_share, 3),
                score=round(score, 2),
                review_indices=indices,
            )
        )

    # Rank real themes (exclude OTHER) deterministically.
    real = [t for t in themes if t.label != OTHER]
    real.sort(key=lambda t: (-t.score, -t.negative_share, -t.count, t.label))
    for rank, theme in enumerate(real, start=1):
        theme.rank = rank

    # Attach quotes to the top 3.
    for theme in real[:3]:
        theme.quotes = _select_quotes(
            reviews, theme.review_indices, negative_theme=theme.negative_share >= 0.5
        )

    other = [t for t in themes if t.label == OTHER]
    ranked = real + other
    top = ", ".join(f"{t.label}({t.count})" for t in real[:3])
    logger.info("[theming] %d themes; top 3: %s", len(real), top or "none")
    return ranked
