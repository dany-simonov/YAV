"""Focused BE-04 fixed-window and fail-closed configuration tests."""
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
