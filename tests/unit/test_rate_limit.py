"""Focused BE-04 fixed-window and fail-closed configuration tests."""
import re
from datetime import datetime, timezone

import pytest

from src.rate_limit import AppwriteTablesRateLimitStore, RateLimitError, _window, normalize_client_ip


def _store(monkeypatch, now=None):
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project")
    monkeypatch.setenv("RATE_LIMIT_IP_HMAC_KEY", "test-secret")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    return AppwriteTablesRateLimitStore("key", now=now)


def test_ipv4_mapped_ipv6_has_same_canonical_identity():
    assert normalize_client_ip("::ffff:192.0.2.1") == "192.0.2.1"


@pytest.mark.parametrize("value", ["0", "-1", "not-an-int"])
def test_invalid_quota_limits_fail_closed(monkeypatch, value):
    store = _store(monkeypatch)
    monkeypatch.setenv("FREE_DAILY_LIMIT", value)
    with pytest.raises(RateLimitError) as raised:
        store.limit("FREE_DAILY_LIMIT", 3)
    assert raised.value.code == "rate_limit_unavailable"


def test_production_cannot_silently_disable_limiter(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RateLimitError) as raised:
        AppwriteTablesRateLimitStore("key")
    assert raised.value.code == "rate_limit_unavailable"


def test_utc_daily_and_monthly_windows_are_deterministic(monkeypatch):
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    _store(monkeypatch, now)
    assert _window(now, "day").key == "2026-08-09"
    assert _window(now, "month").key == "2026-08"


def test_counter_row_ids_are_appwrite_safe_deterministic_and_nonidentifying(monkeypatch):
    store = _store(monkeypatch)
    raw_ip = "192.0.2.1"
    subject = store.ip_subject(raw_ip)
    row_id = store._row_id("ip_minute", subject, "2026-08-09T12:00")

    assert len(row_id) <= 36
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,35}", row_id)
    assert row_id == store._row_id("ip_minute", subject, "2026-08-09T12:00")
    assert raw_ip not in row_id
    assert len({
        row_id,
        store._row_id("user_minute", "trusted-user", "2026-08-09T12:00"),
        store._row_id("user_hour", "trusted-user", "2026-08-09T12"),
        store._row_id("provider_minute", "resemble", "2026-08-09T12:00"),
    }) == 4
    assert len({
        row_id,
        store._row_id("ip_minute", "another-subject", "2026-08-09T12:00"),
        store._row_id("ip_minute", subject, "2026-08-09T12:01"),
    }) == 3
