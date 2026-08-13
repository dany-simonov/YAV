"""Tests for the isolated authenticated Gemini connectivity diagnostic."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.config import settings
from src.gemini_smoke import EXPECTED_RESULT, PROMPT, run_gemini_list_models, run_gemini_smoke_test
from src.main import _execute_request
from src.validation import SecurityValidationError, validate_request_payload


def _response(status_code: int, body: object = None):
    response = MagicMock(status_code=status_code)
    response.json.return_value = body
    return response


def _client(*, response=None, error=None):
    client = MagicMock()
    client.post = AsyncMock(return_value=response, side_effect=error)
    client.get = AsyncMock(return_value=response, side_effect=error)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _success():
    return {
        "candidates": [{"content": {"parts": [{"text": EXPECTED_RESULT}]}}],
    }


def _models_response():
    return {
        "models": [
            {
                "name": "models/gemini-2.5-flash-lite",
                "baseModelId": "gemini-2.5-flash-lite",
                "displayName": "Gemini 2.5 Flash-Lite",
                "supportedGenerationMethods": ["generateContent", "countTokens"],
                "description": "must not be returned",
            },
            {
                "name": "models/gemini-2.5-flash",
                "supportedGenerationMethods": ["generateContent"],
            },
            {
                "name": "models/gemini-3.1-flash-lite",
                "supportedGenerationMethods": ["embedContent"],
            },
            {"name": "models/gemini-pro", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-flash-private", "supportedGenerationMethods": ["generateContent"], "apiKey": "never-return"},
        ],
        "nextPageToken": "never-return",
    }


@pytest.fixture(autouse=True)
def _gemini_settings():
    with patch.object(settings, "gemini_api_key", "gemini-unit-test-key"), patch.object(
        settings, "gemini_api_url", "https://generativelanguage.googleapis.com"
    ), patch.object(settings, "gemini_model", "gemini-3.1-flash-lite"):
        yield


@pytest.mark.asyncio
async def test_success_sends_one_minimal_generate_content_request_and_logs_safe_metadata():
    client = _client(response=_response(200, _success()))
    diagnostic_log = MagicMock()
    with patch("src.gemini_smoke.httpx.AsyncClient", return_value=client):
        result = await run_gemini_smoke_test(diagnostic_log)
    assert result == {
        "ok": True,
        "provider": "gemini",
        "model": "gemini-3.1-flash-lite",
        "result": EXPECTED_RESULT,
    }
    request = client.post.await_args
    assert request.args[0] == (
        "https://generativelanguage.googleapis.com/v1/models/"
        "gemini-3.1-flash-lite:generateContent"
    )
    assert request.kwargs["headers"] == {"x-goog-api-key": "gemini-unit-test-key"}
    assert request.kwargs["json"] == {"contents": [{"parts": [{"text": PROMPT}]}]}
    logged = diagnostic_log.call_args.args[0]
    assert "provider=gemini stage=smoke_test" in logged
    assert "http_status=200" in logged
    assert "gemini-unit-test-key" not in logged


@pytest.mark.asyncio
async def test_missing_api_key_returns_controlled_result_without_http():
    with patch.object(settings, "gemini_api_key", ""), patch(
        "src.gemini_smoke.httpx.AsyncClient"
    ) as client:
        result = await run_gemini_smoke_test()
    assert result["ok"] is False
    assert result["provider_code"] == "MISSING_API_KEY"
    client.assert_not_called()


@pytest.mark.asyncio
async def test_list_models_returns_only_allowlisted_flash_generate_content_metadata():
    client = _client(response=_response(200, _models_response()))
    diagnostic_log = MagicMock()
    with patch("src.gemini_smoke.httpx.AsyncClient", return_value=client):
        result = await run_gemini_list_models(diagnostic_log)

    assert client.get.await_args.args[0] == "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"
    assert client.get.await_args.kwargs["headers"] == {"x-goog-api-key": "gemini-unit-test-key"}
    assert result["models"] == [
        {
            "name": "models/gemini-2.5-flash-lite",
            "supportedGenerationMethods": ["generateContent", "countTokens"],
            "baseModelId": "gemini-2.5-flash-lite",
            "displayName": "Gemini 2.5 Flash-Lite",
        },
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-2.5-flash-private", "supportedGenerationMethods": ["generateContent"]},
    ]
    assert result["requested_models"] == {
        "gemini-2.5-flash-lite": {"present": True, "generateContent": True},
        "gemini-2.5-flash": {"present": True, "generateContent": True},
        "gemini-3.1-flash-lite": {"present": True, "generateContent": False},
    }
    serialized = str(result) + str(diagnostic_log.call_args.args[0])
    assert "never-return" not in serialized
    assert "gemini-unit-test-key" not in serialized
    assert "description" not in serialized and "nextPageToken" not in serialized


@pytest.mark.asyncio
async def test_list_models_malformed_response_is_safe_and_controlled():
    response = _response(200)
    response.json.side_effect = ValueError("untrusted body")
    client = _client(response=response)
    with patch("src.gemini_smoke.httpx.AsyncClient", return_value=client):
        result = await run_gemini_list_models()
    assert result == {
        "ok": False, "provider": "gemini", "operation": "list_models",
        "provider_status": 200, "provider_code": "INVALID_RESPONSE",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 503])
async def test_list_models_provider_errors_are_safe(status):
    client = _client(response=_response(status, {"error": {
        "status": "FAILED_PRECONDITION", "message": "api_key=never-return",
    }}))
    with patch("src.gemini_smoke.httpx.AsyncClient", return_value=client):
        result = await run_gemini_list_models()
    assert result == {
        "ok": False, "provider": "gemini", "operation": "list_models",
        "provider_status": status, "provider_code": "FAILED_PRECONDITION",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (400, {"error": {"status": "FAILED_PRECONDITION", "message": "User location is not supported for the API use."}}, "FAILED_PRECONDITION"),
        (401, {"error": {"status": "UNAUTHENTICATED", "message": "Invalid API key."}}, "UNAUTHENTICATED"),
        (403, {"error": {"status": "PERMISSION_DENIED", "message": "Permission denied."}}, "PERMISSION_DENIED"),
        (404, {"error": {"status": "NOT_FOUND", "message": "Model not found."}}, "NOT_FOUND"),
        (429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded."}}, "RESOURCE_EXHAUSTED"),
        (503, {"error": {"status": "UNAVAILABLE", "message": "Service unavailable."}}, "UNAVAILABLE"),
    ],
)
async def test_provider_errors_keep_only_status_code_and_short_official_message(status, body, code):
    client = _client(response=_response(status, body))
    with patch("src.gemini_smoke.httpx.AsyncClient", return_value=client):
        result = await run_gemini_smoke_test()
    assert result["ok"] is False
    assert result["provider_status"] == status
    assert result["provider_code"] == code
    assert result["message"] == body["error"]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (httpx.ConnectTimeout("connection timed out"), "CONNECT_TIMEOUT"),
        (httpx.ReadTimeout("read timed out"), "READ_TIMEOUT"),
        (httpx.ConnectError("network down"), "NETWORK_ERROR"),
    ],
)
async def test_network_failures_are_distinguished(error, code):
    client = _client(error=error)
    with patch("src.gemini_smoke.httpx.AsyncClient", return_value=client):
        result = await run_gemini_smoke_test()
    assert result["ok"] is False
    assert result["provider_code"] == code
    assert "connection timed out" not in result["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"candidates": []}, {"candidates": [{"content": {"parts": [{"text": "wrong"}]}}]}])
async def test_malformed_or_unexpected_success_response_is_controlled(body):
    client = _client(response=_response(200, body))
    with patch("src.gemini_smoke.httpx.AsyncClient", return_value=client):
        result = await run_gemini_smoke_test()
    assert result == {
        "ok": False,
        "provider": "gemini",
        "model": "gemini-3.1-flash-lite",
        "provider_status": 200,
        "provider_code": "INVALID_RESPONSE",
        "message": "Gemini returned an unexpected response.",
    }


def test_smoke_action_is_strictly_validated_without_analyze_input():
    request = validate_request_payload({"action": "gemini_smoke_test"})
    assert request.action == "gemini_smoke_test"
    with pytest.raises(SecurityValidationError):
        validate_request_payload({"action": "gemini_smoke_test", "text": "x" * 50})
    assert validate_request_payload({"action": "gemini_list_models"}).action == "gemini_list_models"
    with pytest.raises(SecurityValidationError):
        validate_request_payload({"action": "gemini_list_models", "text": "x" * 50})


@pytest.mark.asyncio
async def test_enabled_diagnostic_smoke_bypasses_profile_quota_analysis_and_persistence():
    response = {"ok": True, "provider": "gemini", "model": "gemini-3.1-flash-lite", "result": EXPECTED_RESULT}
    with patch.object(settings, "gemini_smoke_enabled", True), patch.object(
        settings, "gemini_smoke_diagnostic_secret", "diagnostic-secret"
    ), patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "user", "emailVerification": True})), patch(
        "src.main.ensure_user_profile", new=AsyncMock()
    ) as profile, patch("src.main.AppwriteTablesRateLimitStore") as rate_store, patch(
        "src.main.enforce_admission", new=AsyncMock()
    ) as admission, patch("src.main._analyze", new=AsyncMock()) as analyze, patch(
        "src.main.persist_check_result", new=AsyncMock()
    ) as persist, patch("src.main.run_gemini_smoke_test", new=AsyncMock(return_value=response)) as smoke:
        result = await _execute_request(
            {"action": "gemini_smoke_test"}, "dynamic-key", "user", "jwt", diagnostic_log=MagicMock(),
            diagnostic_authorization="diagnostic-secret",
        )
    assert result is response
    smoke.assert_awaited_once()
    profile.assert_not_awaited()
    rate_store.assert_not_called()
    admission.assert_not_awaited()
    analyze.assert_not_awaited()
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_list_models_uses_same_guard_and_bypasses_analysis_lifecycle():
    response = {"ok": True, "provider": "gemini", "operation": "list_models", "models": []}
    with patch.object(settings, "gemini_smoke_enabled", True), patch.object(
        settings, "gemini_smoke_diagnostic_secret", "diagnostic-secret"
    ), patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "user", "emailVerification": True})), patch(
        "src.main.ensure_user_profile", new=AsyncMock()
    ) as profile, patch("src.main.AppwriteTablesRateLimitStore") as rate_store, patch(
        "src.main.enforce_admission", new=AsyncMock()
    ) as admission, patch("src.main._analyze", new=AsyncMock()) as analyze, patch(
        "src.main.persist_check_result", new=AsyncMock()
    ) as persist, patch("src.main.run_gemini_list_models", new=AsyncMock(return_value=response)) as list_models:
        result = await _execute_request(
            {"action": "gemini_list_models"}, "dynamic-key", "user", "jwt", diagnostic_log=MagicMock(),
            diagnostic_authorization="diagnostic-secret",
        )
    assert result is response
    list_models.assert_awaited_once()
    profile.assert_not_awaited()
    rate_store.assert_not_called()
    admission.assert_not_awaited()
    analyze.assert_not_awaited()
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_user_is_denied_gemini_smoke_by_default():
    with patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "user", "emailVerification": True})), patch(
        "src.main.run_gemini_smoke_test", new=AsyncMock()
    ) as smoke, pytest.raises(SecurityValidationError) as raised:
        await _execute_request({"action": "gemini_smoke_test"}, "dynamic-key", "user", "jwt")
    assert raised.value.code == "diagnostic_access_denied"
    smoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_gemini_smoke_is_denied_even_with_secret():
    with patch.object(settings, "gemini_smoke_enabled", False), patch.object(
        settings, "gemini_smoke_diagnostic_secret", "diagnostic-secret"
    ), patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "user", "emailVerification": True})), patch(
        "src.main.run_gemini_smoke_test", new=AsyncMock()
    ) as smoke, pytest.raises(SecurityValidationError) as raised:
        await _execute_request(
            {"action": "gemini_smoke_test"}, "dynamic-key", "user", "jwt",
            diagnostic_authorization="diagnostic-secret",
        )
    assert raised.value.code == "diagnostic_access_denied"
    smoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_or_wrong_secret_denies_list_models():
    for enabled, secret in ((False, "diagnostic-secret"), (True, "wrong-secret")):
        with patch.object(settings, "gemini_smoke_enabled", enabled), patch.object(
            settings, "gemini_smoke_diagnostic_secret", "diagnostic-secret"
        ), patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "user", "emailVerification": True})), patch(
            "src.main.run_gemini_list_models", new=AsyncMock()
        ) as list_models, pytest.raises(SecurityValidationError) as raised:
            await _execute_request(
                {"action": "gemini_list_models"}, "dynamic-key", "user", "jwt",
                diagnostic_authorization=secret,
            )
        assert raised.value.code == "diagnostic_access_denied"
        list_models.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_diagnostic_authorization_is_denied():
    with patch.object(settings, "gemini_smoke_enabled", True), patch.object(
        settings, "gemini_smoke_diagnostic_secret", "diagnostic-secret"
    ), patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "user", "emailVerification": True})), patch(
        "src.main.run_gemini_smoke_test", new=AsyncMock()
    ) as smoke, pytest.raises(SecurityValidationError) as raised:
        await _execute_request(
            {"action": "gemini_smoke_test"}, "dynamic-key", "user", "jwt",
            diagnostic_authorization="wrong-secret",
        )
    assert raised.value.code == "diagnostic_access_denied"
    smoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_unverified_user_cannot_run_smoke_action():
    with patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "user", "emailVerification": False})), patch(
        "src.main.run_gemini_smoke_test", new=AsyncMock()
    ) as smoke:
        with pytest.raises(PermissionError):
            await _execute_request({"action": "gemini_smoke_test"}, "dynamic-key", "user", "jwt")
    smoke.assert_not_awaited()
