"""Run configuration schema and loader.

The run config captures everything a single weekly run needs: which product,
what date window, the theme taxonomy, output targets, and LLM settings.

Config is loaded from a YAML file, with secrets (the Groq API key) sourced from
the environment / .env so they are never committed.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
MAX_THEMES = 5


class WindowConfig(BaseModel):
    """Date window for reviews to include (last N weeks, 8-12 per constraints)."""

    weeks: int = Field(default=12, ge=8, le=12)


class ThemeTaxonomy(BaseModel):
    """Predefined themes to classify reviews into (at most 5)."""

    labels: list[str] = Field(
        default_factory=lambda: [
            "charges_fees",
            "trading_products",
            "app_ux_updates",
            "customer_support",
            "withdrawals_payments",
        ]
    )

    @field_validator("labels")
    @classmethod
    def _cap_themes(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("theme taxonomy must contain at least one label")
        if len(value) > MAX_THEMES:
            raise ValueError(f"at most {MAX_THEMES} themes allowed, got {len(value)}")
        return value


class AppStoreSource(BaseModel):
    """App Store download settings. app_id is the numeric iTunes track id."""

    app_id: str | None = None
    country: str = "us"


class PlayStoreSource(BaseModel):
    """Play Store download settings. app_id is the package name."""

    app_id: str | None = None
    lang: str = "en"
    country: str = "us"


class SourcesConfig(BaseModel):
    """Where and how many reviews to download from each store."""

    app_store: AppStoreSource = Field(default_factory=AppStoreSource)
    play_store: PlayStoreSource = Field(default_factory=PlayStoreSource)
    # Safety cap on reviews pulled per store. Window (weeks) is the primary
    # bound; this cap just prevents runaway downloads for very high-volume apps.
    max_reviews: int = Field(default=20000, ge=1, le=200000)


class FilterConfig(BaseModel):
    """Quality/language filters applied during normalization (Phase 2)."""

    # Drop reviews with fewer than this many words.
    min_words: int = Field(default=8, ge=1)
    # Drop reviews with more than this many emoji characters.
    # Default 0 => drop any review containing one or more emojis.
    max_emojis: int = Field(default=0, ge=0)
    # Drop Hindi (Devanagari-script) reviews.
    drop_hindi: bool = True
    # Fraction of letters that must be Devanagari to treat a review as Hindi.
    hindi_ratio: float = Field(default=0.2, ge=0.0, le=1.0)


class OutputTargets(BaseModel):
    """Where the pulse is published and drafted."""

    doc_title: str = "Weekly Review Pulse"
    # If set, an existing Doc is updated (idempotent re-runs); otherwise created.
    doc_id: str | None = None
    # Recipient of the Gmail draft (yourself or an alias). Draft only, never sent.
    email_to: str | None = None


class GroqConfig(BaseModel):
    """Groq LLM settings. api_key is injected from the environment, not YAML."""

    model: str = DEFAULT_GROQ_MODEL
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    api_key: str | None = Field(default=None, exclude=True)


class RunConfig(BaseModel):
    """Top-level configuration for one weekly pulse run."""

    product_id: str
    product_name: str
    window: WindowConfig = Field(default_factory=WindowConfig)
    themes: ThemeTaxonomy = Field(default_factory=ThemeTaxonomy)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    filters: FilterConfig = Field(default_factory=FilterConfig)
    outputs: OutputTargets = Field(default_factory=OutputTargets)
    groq: GroqConfig = Field(default_factory=GroqConfig)

    # Directories for local artifacts / source exports.
    exports_dir: str = "data/exports"
    store_dir: str = "store/runs"

    def redacted_dict(self) -> dict:
        """Config as a dict safe for logging (api_key excluded by GroqConfig)."""
        return self.model_dump()


def load_config(path: str | Path) -> RunConfig:
    """Load a RunConfig from YAML, overlaying secrets from the environment.

    The Groq API key is read from GROQ_API_KEY and the model may be overridden
    with GROQ_MODEL. Missing secrets are allowed at load time (validated when
    the LLM is actually used) so that a no-op run works without a key.
    """
    load_dotenv()

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = RunConfig.model_validate(raw)

    config.groq.api_key = os.getenv("GROQ_API_KEY")
    env_model = os.getenv("GROQ_MODEL")
    if env_model:
        config.groq.model = env_model

    return config
