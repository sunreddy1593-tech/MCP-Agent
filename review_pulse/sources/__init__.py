"""Review source adapters (App Store, Play Store) behind a common interface."""

from review_pulse.sources.app_store import AppStoreAdapter
from review_pulse.sources.base import ReviewSource
from review_pulse.sources.play_store import PlayStoreAdapter

__all__ = ["ReviewSource", "AppStoreAdapter", "PlayStoreAdapter"]
