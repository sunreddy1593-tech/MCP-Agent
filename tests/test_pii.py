"""Phase 2 tests: PII scrubbing + normalization (drop short, dedupe)."""

from __future__ import annotations

from datetime import datetime, timezone

from review_pulse.config import RunConfig
from review_pulse.models import Review
from review_pulse.pipeline.normalize import normalize
from review_pulse.pipeline.pii import contains_pii, scrub_text


def _config() -> RunConfig:
    return RunConfig(product_id="com.test", product_name="Test")


def _review(text: str, date: str = "2026-06-01", store: str = "play_store", rating=5) -> Review:
    return Review(
        store=store,
        rating=rating,
        title=None,
        text=text,
        date=datetime.fromisoformat(date).replace(tzinfo=timezone.utc),
    )


def test_scrub_email():
    assert scrub_text("mail me at john.doe@example.com please") == (
        "mail me at [EMAIL] please"
    )


def test_scrub_phone_variants():
    assert "[PHONE]" in scrub_text("call +91 98765 43210 now")
    assert "[PHONE]" in scrub_text("reach 9876543210")
    assert "[PHONE]" in scrub_text("number: 987-654-3210")


def test_scrub_pan():
    assert scrub_text("my PAN is ABCDE1234F ok") == "my PAN is [PAN] ok"


def test_scrub_long_numeric_id():
    # Long account numbers (<10 digits) become [ID].
    assert "[ID]" in scrub_text("account 12345678")
    # A 12-digit contiguous number (Aadhaar-like) is masked (token may be
    # [PHONE] or [ID]); what matters is the raw digits are gone.
    masked = scrub_text("aadhaar 123412341234")
    assert "123412341234" not in masked
    assert "[" in masked


def test_scrub_handle():
    assert scrub_text("thanks @support_team") == "thanks [HANDLE]"


def test_currency_amounts_preserved():
    # Amounts must remain readable (not masked as ids).
    assert scrub_text("I withdrew 50,000 and paid ₹500") == (
        "I withdrew 50,000 and paid ₹500"
    )
    assert scrub_text("lost 5000 rupees") == "lost 5000 rupees"


def test_contains_pii_safety_net():
    assert contains_pii("ping me at a@b.com") is True
    assert contains_pii("great app, smooth onboarding") is False


def test_normalize_drops_short_and_scrubs():
    long_ok = "Please email me at a@b.com because the app keeps crashing every time"
    reviews = [
        _review("ok"),                                   # too short -> dropped
        _review("KYC failed three times"),               # 4 words -> dropped
        _review(long_ok),                                # 12 words, scrubbed -> kept
    ]
    out = normalize(reviews, _config())
    assert len(out) == 1
    assert "@" not in out[0].text
    assert "[EMAIL]" in out[0].text


def test_normalize_dedupes_true_duplicates_only():
    base = "the onboarding process was smooth and the app works really well overall"
    reviews = [
        _review(base, date="2026-06-01"),
        _review(base.upper(), date="2026-06-01"),  # case dupe -> dropped
        _review(base, date="2026-06-02"),          # different day -> kept
    ]
    out = normalize(reviews, _config())
    assert len(out) == 2
