"""Summarization via Groq (Phase 4).

Consumes the top-3 `Theme` objects from Phase 3 (each with stats and
pre-selected candidate `Quote`s) and produces a validated `WeeklyNote`:
3 themes, 3 verbatim quotes, 3 action ideas, <=250 words.

Primary path uses Groq JSON mode with a bounded retry/repair loop. If no
`GROQ_API_KEY` is present, a deterministic template note is composed from the
themes and their first candidate quotes so the pipeline still runs offline.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import TYPE_CHECKING

from review_pulse.models import NoteTheme, Theme, WeeklyNote
from review_pulse.pipeline.validators import validate_note, word_count

if TYPE_CHECKING:
    from review_pulse.config import RunConfig
    from review_pulse.llm.client import LlmClient

logger = logging.getLogger(__name__)

MAX_REPAIRS = 3

# Friendly display names for the data-derived Groww labels.
THEME_DISPLAY: dict[str, str] = {
    "charges_fees": "Charges & Fees",
    "trading_products": "Trading & Products",
    "app_ux_updates": "App UX, UI & Updates",
    "customer_support": "Customer Support",
    "withdrawals_payments": "Withdrawals & Payments",
}


def _display_name(label: str) -> str:
    return THEME_DISPLAY.get(label, label.replace("_", " ").title())


def _stat(theme: Theme) -> str:
    avg = f", avg {theme.avg_rating}\u2605" if theme.avg_rating is not None else ""
    return f"{theme.count} reviews, {round(theme.negative_share * 100)}% negative{avg}"


def _allowed_quotes(themes: list[Theme]) -> set[str]:
    return {q.text.strip() for t in themes for q in t.quotes}


def summarize(
    themes: list[Theme],
    config: "RunConfig",
    llm: "LlmClient | None" = None,
) -> WeeklyNote | None:
    """Produce a validated WeeklyNote from the top themes."""
    top = [t for t in themes if t.rank in (1, 2, 3)][:3]
    if len(top) < 3:
        logger.warning("[summarize] only %d ranked themes; need 3", len(top))
    if not top:
        return None

    allowed = _allowed_quotes(top)
    week_of = date.today().isoformat()

    use_llm = llm is not None and getattr(llm, "api_key", None)
    if use_llm:
        note = _summarize_groq(top, config, llm, week_of, allowed)
        if note is not None:
            return note
        logger.warning("[summarize] Groq path failed validation; using fallback note")

    return _fallback_note(top, config, week_of)


def _build_prompt(top: list[Theme], config: "RunConfig", week_of: str) -> tuple[str, str]:
    system = (
        "You are a product analyst writing a concise weekly review pulse for "
        "stakeholders. Use ONLY the provided candidate quotes, copied EXACTLY "
        "(verbatim) — never invent or paraphrase a quote. The entire note must be "
        "under 250 words. Respond ONLY with JSON."
    )
    blocks = []
    for t in top:
        quotes = "\n".join(f'    - "{q.text}"' for q in t.quotes)
        blocks.append(
            f"Theme: {_display_name(t.label)}\n"
            f"  Description: {t.description}\n"
            f"  Stats: {_stat(t)}\n"
            f"  Candidate quotes:\n{quotes if quotes else '    (none)'}"
        )
    user = (
        f"Product: {config.product_name}\nWeek of: {week_of}\n\n"
        f"Top 3 themes:\n\n" + "\n\n".join(blocks) + "\n\n"
        "Return JSON exactly of this shape:\n"
        '{\n'
        '  "themes": [{"name": "...", "summary": "...", "stat": "..."}, x3],\n'
        '  "quotes": ["<verbatim quote>", x3],\n'
        '  "actions": ["<concrete next step>", x3]\n'
        "}\n"
        "Rules: exactly 3 themes, 3 quotes, 3 actions. Each quote MUST be copied "
        "verbatim from the candidate quotes above. Actions must be concrete and "
        "grounded in the themes. Keep the whole note under 250 words."
    )
    return system, user


def _parse_note(raw: str, config: "RunConfig", week_of: str) -> WeeklyNote:
    data = json.loads(raw)
    note = WeeklyNote(
        week_of=week_of,
        product=config.product_name,
        themes=[NoteTheme(**t) for t in data.get("themes", [])],
        quotes=[str(q) for q in data.get("quotes", [])],
        actions=[str(a) for a in data.get("actions", [])],
        generated_by="groq",
    )
    note.word_count = word_count(note)
    return note


def _summarize_groq(
    top: list[Theme],
    config: "RunConfig",
    llm: "LlmClient",
    week_of: str,
    allowed: set[str],
) -> WeeklyNote | None:
    system, user = _build_prompt(top, config, week_of)
    prompt = user
    for attempt in range(1, MAX_REPAIRS + 1):
        try:
            raw = llm.complete_json(system, prompt)
            note = _parse_note(raw, config, week_of)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[summarize] attempt %d parse error: %s", attempt, exc)
            prompt = user + f"\n\nYour previous output could not be parsed ({exc}). Return valid JSON."
            continue

        errors = validate_note(note, allowed)
        if not errors:
            logger.info(
                "[summarize] valid note on attempt %d (%d words)", attempt, note.word_count
            )
            return note
        logger.warning("[summarize] attempt %d invalid: %s", attempt, errors)
        prompt = (
            user
            + "\n\nYour previous output had these problems:\n- "
            + "\n- ".join(errors)
            + "\nFix them and return valid JSON. Quotes must be copied verbatim "
            "from the candidate quotes."
        )
    return None


def _fallback_note(top: list[Theme], config: "RunConfig", week_of: str) -> WeeklyNote:
    """Deterministic template note (no LLM) built from themes + first quotes."""
    logger.warning("[summarize] building deterministic fallback note (no GROQ_API_KEY)")
    themes: list[NoteTheme] = []
    quotes: list[str] = []
    actions: list[str] = []
    for t in top:
        name = _display_name(t.label)
        themes.append(
            NoteTheme(
                name=name,
                summary=t.description or f"User feedback about {name.lower()}.",
                stat=_stat(t),
            )
        )
        if t.quotes:
            quotes.append(t.quotes[0].text)
        verb = "Address" if t.negative_share >= 0.5 else "Build on"
        actions.append(f"{verb} feedback on {name.lower()} ({round(t.negative_share * 100)}% negative).")

    note = WeeklyNote(
        week_of=week_of,
        product=config.product_name,
        themes=themes[:3],
        quotes=quotes[:3],
        actions=actions[:3],
        generated_by="fallback",
    )
    note.word_count = word_count(note)
    return note
