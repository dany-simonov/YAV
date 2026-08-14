"""Focused production-MVP admission plan coverage (no external providers)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config import settings
from src.rate_limit import (
    AdmissionDimension, AdmissionPlan, AppwriteTablesRateLimitStore, RateLimitError, Window,
    build_admission_plan,
)
from src.validation import SecurityValidationError


def _store(monkeypatch, now: datetime) -> AppwriteTablesRateLimitStore:
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project")
    monkeypatch.setenv("RATE_LIMIT_IP_HMAC_KEY", "test-secret")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    return AppwriteTablesRateLimitStore("server-key", now=now)


def _plan(monkeypatch, *, media_type="text", text="short", input_size=None, hybrid=False, created=None):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    return build_admission_plan(
        _store(monkeypatch, now), user_id="user", client_ip="192.0.2.1",
        account_created_at=(created or now - timedelta(hours=1)).isoformat(), media_type=media_type,
        text=text, input_size=len(text) if input_size is None else input_size, hybrid=hybrid,
    )


def test_new_user_short_text_reserves_one_check_and_two_gemini_operations(monkeypatch):
    plan = _plan(monkeypatch)
    dimensions = {item.dimension: item for item in plan.dimensions}
    assert {"ip_total_daily", "new_user_total_daily", "new_user_total_first7d", "new_user_text_daily", "global_gemini_daily"} <= set(dimensions)
    assert dimensions["global_gemini_daily"].units == 2
    assert plan.units_for("gemini") == 2


def test_long_text_uses_actual_aiornot_words_and_one_gemini_operation(monkeypatch):
    text = "word " * 64
    plan = _plan(monkeypatch, text=text)
    dimensions = {item.dimension: item for item in plan.dimensions}
    assert dimensions["global_aiornot_words_daily"].units == 64
    assert dimensions["global_aiornot_words_monthly"].units == 64
    assert dimensions["global_gemini_daily"].units == 1
    assert plan.units_for("aiornot") == 64
    assert plan.units_for("gemini") == 1


def test_complex_text_has_no_sapling_or_aiornot_and_exactly_two_gemini_operations(monkeypatch):
    plan = _plan(monkeypatch, text="word " * 100, hybrid=True)
    names = {item.dimension for item in plan.dimensions}
    assert plan.units_for("gemini") == 2
    assert plan.units_for("sapling") == 0
    assert plan.units_for("aiornot") == 0
    assert "global_gemini_daily" in names


@pytest.mark.parametrize(
    ("media_type", "dimension"),
    [("image", "new_user_image_daily"), ("audio", "new_user_audio_72h"), ("video", "new_user_video_first7d")],
)
def test_new_user_media_plan_has_type_and_shared_heavy_ip_dimensions(monkeypatch, media_type, dimension):
    plan = _plan(monkeypatch, media_type=media_type, input_size=1)
    names = {item.dimension for item in plan.dimensions}
    assert {"ip_total_daily", "ip_heavy_media_daily", "new_user_total_daily", "new_user_total_first7d", dimension} <= names


def test_old_user_has_no_new_user_dimensions(monkeypatch):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    plan = _plan(monkeypatch, created=now - timedelta(days=settings.new_user_period_days + 1))
    assert not any(item.dimension.startswith("new_user_") for item in plan.dimensions)
    assert {item.dimension for item in plan.dimensions} == {"ip_total_daily", "global_gemini_daily"}


@pytest.mark.parametrize(
    ("media_type", "hybrid", "size"),
    [
        ("text", False, settings.new_user_text_max_chars + 1),
        ("text", True, settings.new_user_hybrid_max_chars + 1),
        ("image", False, settings.new_user_image_max_bytes + 1),
        ("audio", False, settings.new_user_audio_max_bytes + 1),
        ("video", False, settings.new_user_video_max_bytes + 1),
    ],
)
def test_new_user_size_overflow_is_rejected_before_admission(monkeypatch, media_type, hybrid, size):
    with pytest.raises(SecurityValidationError):
        _plan(monkeypatch, media_type=media_type, hybrid=hybrid, input_size=size, text="x" * min(size, 3001))


def _response(status: int, body=None):
    response = MagicMock(status_code=status)
    response.json.return_value = body or {}
    return response


@pytest.mark.asyncio
async def test_admit_stages_every_dimension_and_one_reservation_in_one_transaction(monkeypatch):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    store = _store(monkeypatch, now)
    window = Window("2026-08-09", now + timedelta(days=1))
    plan = AdmissionPlan("user", (
        AdmissionDimension("user", "user", window, 1, 4, "daily_quota_exceeded", "safe"),
        AdmissionDimension("gemini", "global", window, 2, 100, "provider_temporarily_unavailable", "safe"),
    ))
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=[_response(404), _response(404)])
    client.post = AsyncMock(side_effect=[_response(201, {"$id": "tx"}), _response(201), _response(201), _response(201)])
    client.patch = AsyncMock(return_value=_response(200))

    with patch("src.rate_limit.httpx.AsyncClient", return_value=client):
        await store.admit(plan)

    assert client.get.await_count == 2
    assert all(call.kwargs["params"] == {"transactionId": "tx"} for call in client.get.await_args_list)
    staged = client.post.await_args_list[1:]
    assert all(call.kwargs["json"]["transactionId"] == "tx" for call in staged)
    assert client.patch.await_args_list[-1].kwargs["json"] == {"commit": True}


@pytest.mark.asyncio
async def test_late_dimension_denial_rolls_back_without_commit(monkeypatch):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    store = _store(monkeypatch, now)
    window = Window("2026-08-09", now + timedelta(days=1))
    plan = AdmissionPlan("user", (
        AdmissionDimension("user", "user", window, 1, 4, "daily_quota_exceeded", "safe"),
        AdmissionDimension("gemini", "global", window, 2, 2, "provider_temporarily_unavailable", "safe"),
    ))
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=[_response(404), _response(200, {"count": 0})])
    client.post = AsyncMock(side_effect=[_response(201, {"$id": "tx"}), _response(201)])
    client.patch = AsyncMock(side_effect=[_response(400, {"type": "row_max_exceeded"}), _response(200)])

    with patch("src.rate_limit.httpx.AsyncClient", return_value=client), pytest.raises(RateLimitError) as raised:
        await store.admit(plan)

    assert raised.value.code == "provider_temporarily_unavailable"
    assert any(call.kwargs["json"] == {"rollback": True} for call in client.patch.await_args_list)
    assert not any(call.kwargs["json"] == {"commit": True} for call in client.patch.await_args_list)
