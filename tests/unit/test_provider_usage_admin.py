"""Contracts for the independent provider-usage admin actions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.admin import AppwriteAdminStore
from src.provider_external_usage import ProviderExternalUsageService
from src.provider_telemetry import AppwriteProviderTelemetryStore
from src.validation import validate_request_payload


def _response(status: int, body=None):
    response = MagicMock(status_code=status)
    response.json.return_value = body or {}
    return response


def _admin_store(monkeypatch) -> AppwriteAdminStore:
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project")
    monkeypatch.setenv("APPWRITE_DATABASE_ID", "yav")
    monkeypatch.setenv("APPWRITE_USERS_TABLE_ID", "users")
    monkeypatch.setenv("APPWRITE_RATE_LIMITS_TABLE_ID", "rate_limits")
    monkeypatch.setenv("APPWRITE_QUOTA_RESERVATIONS_TABLE_ID", "quota_reservations")
    monkeypatch.setenv("APPWRITE_USER_QUOTA_GENERATIONS_TABLE_ID", "generations")
    monkeypatch.setenv("APPWRITE_ADMIN_AUDIT_TABLE_ID", "admin_audit_log")
    return AppwriteAdminStore("runtime-key")


def _client(*, get=None, post=None, patch_method=None):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=get)
    client.post = AsyncMock(side_effect=post)
    client.patch = AsyncMock(side_effect=patch_method)
    return client


@pytest.mark.asyncio
async def test_provider_budget_overview_reads_global_counters_without_users(monkeypatch):
    store = _admin_store(monkeypatch)

    async def get(url, **_kwargs):
        assert "/users/" not in url
        assert "/rate_limits/" in url
        return _response(404)

    client = _client(get=get)
    with patch("src.admin.httpx.AsyncClient", return_value=client):
        result = await store.provider_budget_overview()

    providers = {item["provider"]: item for item in result["providers"]}
    assert providers["gemini"]["daily"] == {"used": 0, "limit": 100, "remaining": 100}
    assert providers["gemini"]["monthly"] is None
    assert providers["huggingface"]["monthly"]["used"] == 0
    aiornot = providers["aiornot"]
    assert aiornot["daily"] is None
    assert {metric["unit"] for metric in aiornot["metrics"]} == {"words", "image_checks"}


@pytest.mark.asyncio
async def test_provider_budget_overview_keeps_daily_and_monthly_counter_values(monkeypatch):
    store = _admin_store(monkeypatch)

    async def get(url, **_kwargs):
        if store._counter_row_id("global_huggingface_daily", "global", __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%d")) in url:
            return _response(200, {"count": 3})
        if "global_huggingface" in url:
            return _response(404)
        return _response(404)

    client = _client(get=get)
    with patch("src.admin.httpx.AsyncClient", return_value=client):
        result = await store.provider_budget_overview()
    huggingface = next(item for item in result["providers"] if item["provider"] == "huggingface")
    assert huggingface["daily"] == {"used": 3, "limit": 50, "remaining": 47}
    assert huggingface["monthly"] == {"used": 0, "limit": 1500, "remaining": 1500}


def test_new_admin_usage_actions_are_server_only_contracts():
    overview = validate_request_payload({"action": "admin_get_provider_budget_overview"})
    external = validate_request_payload({"action": "admin_get_provider_external_usage"})
    assert overview.action == "admin_get_provider_budget_overview"
    assert external.action == "admin_get_provider_external_usage"


@pytest.mark.asyncio
async def test_sapling_reporting_returns_only_sanitized_aggregate_fields(monkeypatch):
    monkeypatch.setattr("src.provider_external_usage.settings.sapling_api_key", "super-secret")
    client = _client(post=[_response(200, {"total_characters": 120}), _response(200, {"usage": 100, "quota": 500})])
    with patch("src.provider_external_usage.httpx.AsyncClient", return_value=client):
        result = await ProviderExternalUsageService()._sapling()
    assert result["status"] == "ok"
    assert result["usage"] == 100
    assert result["quota"] == 500
    assert result["remaining"] == 400
    assert "super-secret" not in str(result)
    assert "Authorization" not in str(result)


@pytest.mark.asyncio
async def test_sapling_reporting_error_is_controlled_and_has_no_secret(monkeypatch):
    monkeypatch.setattr("src.provider_external_usage.settings.sapling_api_key", "super-secret")
    client = _client(post=[_response(401), _response(200, {})])
    with patch("src.provider_external_usage.httpx.AsyncClient", return_value=client):
        result = await ProviderExternalUsageService()._sapling()
    assert result["reason"] == "provider_auth_error"
    assert "super-secret" not in str(result)


@pytest.mark.asyncio
async def test_resemble_account_and_billing_status_are_not_labeled_detect_usage(monkeypatch):
    monkeypatch.setattr("src.provider_external_usage.settings.resemble_api_key", "super-secret")
    client = _client(get=[
        _response(200, {"success": True, "items": [{"current_usage": 12, "voice_limit": 20, "units": "seconds"}]}),
        _response(200, {"subscription": {}}),
        _response(403, {}),
    ])
    with patch("src.provider_external_usage.httpx.AsyncClient", return_value=client):
        result = await ProviderExternalUsageService()._resemble()
    assert result["status"] == "ok"
    assert result["metric_scope"] == "team_account_not_detect_specific"
    assert result["usage"][0]["remaining"] == 8
    assert result["billing"]["wallet"] == "unavailable"
    assert "super-secret" not in str(result)


@pytest.mark.asyncio
async def test_resemble_reporting_error_is_controlled(monkeypatch):
    monkeypatch.setattr("src.provider_external_usage.settings.resemble_api_key", "super-secret")
    client = _client(get=[_response(429), _response(200, {}), _response(200, {})])
    with patch("src.provider_external_usage.httpx.AsyncClient", return_value=client):
        result = await ProviderExternalUsageService()._resemble()
    assert result == {
        "provider": "resemble", "source": "provider", "metric_scope": "team_account_not_detect_specific",
        "status": "error", "reason": "provider_rate_limited",
    }


@pytest.mark.asyncio
async def test_sightengine_provider_operations_are_stored_as_provider_telemetry(monkeypatch):
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project")
    monkeypatch.setenv("APPWRITE_DATABASE_ID", "yav")
    monkeypatch.setenv("APPWRITE_PROVIDER_TELEMETRY_TABLE_ID", "provider_telemetry")
    client = _client(post=[_response(201), _response(409)], patch_method=[_response(200)])
    with patch("src.provider_telemetry.httpx.AsyncClient", return_value=client):
        await AppwriteProviderTelemetryStore("runtime-key").record_sightengine_operations(2)
    assert client.post.await_count == 2
    assert client.patch.await_args.kwargs["json"] == {"value": 2}


@pytest.mark.asyncio
async def test_sightengine_response_operations_are_exposed_only_as_sanitized_telemetry(monkeypatch):
    from adapters.sightengine import SightengineAdapter

    monkeypatch.setattr("adapters.sightengine.settings.sightengine_api_user", "user")
    monkeypatch.setattr("adapters.sightengine.settings.sightengine_api_secret", "secret")
    client = _client(post=[_response(200, {"status": "success", "type": {"ai_generated": 0}, "request": {"operations": 2}})])
    with patch("adapters.sightengine.httpx.AsyncClient", return_value=client), patch("adapters.sightengine.admit_provider_operation", new=AsyncMock()), patch("adapters.sightengine.record_sightengine_operations", new=AsyncMock()) as record:
        result = await SightengineAdapter().analyze(b"image")
    assert result.provider_evidence is not None
    assert result.provider_evidence.safe_details["request_operations"] == 2
    assert "secret" not in str(result.provider_evidence.safe_details)
    record.assert_awaited_once_with(2)


@pytest.mark.asyncio
async def test_provider_without_usage_api_is_explicitly_unavailable(monkeypatch):
    monkeypatch.setattr("src.provider_external_usage.settings.sapling_api_key", "")
    monkeypatch.setattr("src.provider_external_usage.settings.resemble_api_key", "")
    result = await ProviderExternalUsageService().get_usage()
    providers = {item["provider"]: item for item in result["providers"]}
    assert providers["gemini"]["reason"] == "no_supported_usage_api"
    assert providers["huggingface"]["reason"] == "no_supported_usage_api"
    assert providers["aiornot"]["reason"] == "no_supported_usage_api"
