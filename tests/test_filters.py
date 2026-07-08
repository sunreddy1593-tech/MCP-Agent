"""Phase 2 quality/language filter tests: word count, Hindi, emoji-heavy."""

from __future__ import annotations

from datetime import datetime, timezone

from review_pulse.config import RunConfig
from review_pulse.models import Review
from review_pulse.pipeline.normalize import normalize
from review_pulse.pipeline.textstats import (
    count_emojis,
    devanagari_ratio,
    is_hindi,
    word_count,
)


def _config() -> RunConfig:
    return RunConfig(product_id="com.test", product_name="Test")


def _review(text: str, date: str = "2026-06-01") -> Review:
    return Review(
        store="play_store",
        rating=4,
        title=None,
        text=text,
        date=datetime.fromisoformat(date).replace(tzinfo=timezone.utc),
    )


# --- textstats units ------------------------------------------------------- #
def test_word_count():
    assert word_count("this app is genuinely very good and useful") == 8
    assert word_count("good") == 1
    assert word_count("") == 0


def test_count_emojis():
    assert count_emojis("great app") == 0
    assert count_emojis("love it 😍🔥👍") == 3
    assert count_emojis("⭐⭐⭐⭐⭐") == 5


def test_devanagari_and_is_hindi():
    assert devanagari_ratio("hello world") == 0.0
    assert devanagari_ratio("बहुत अच्छा ऐप है") > 0.8
    assert is_hindi("यह ऐप बहुत अच्छा है और तेज़ चलता है")
    assert not is_hindi("this app is very good and fast to use")


# --- normalize integration ------------------------------------------------- #
def test_drop_reviews_under_8_words():
    reviews = [
        _review("nice app good work"),                                  # 4 words
        _review("this application is genuinely useful and works well"),  # 8 words
    ]
    out = normalize(reviews, _config())
    assert len(out) == 1
    assert out[0].text.startswith("this application")


def test_drop_hindi_reviews():
    reviews = [
        _review("यह ऐप बहुत अच्छा है और बहुत तेज़ चलता है"),
        _review("this english review is long enough to be kept here"),
    ]
    out = normalize(reviews, _config())
    assert len(out) == 1
    assert out[0].text.startswith("this english")


def test_drop_reviews_with_any_emoji():
    reviews = [
        _review("this review is otherwise long enough but has one emoji 🙂"),  # 1 emoji -> dropped
        _review("awesome app really love using it 😍😍🔥🔥👍👍🎉"),           # many -> dropped
        _review("this is a clean review with enough words to pass"),           # kept
    ]
    out = normalize(reviews, _config())
    assert len(out) == 1
    assert count_emojis(out[0].text) == 0
