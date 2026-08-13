"""Tests for the isolated authenticated Gemini connectivity diagnostic."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.config import settings
from src.gemini_smoke import EXPECTED_RESULT, PROMPT, run_gemini_smoke_test
from src.main import _execute_request
from src.validation import SecurityValidationError, validate_request_payload


def _response(status_code: int, body: object = None):
    response = MagicMock(status_code=status_code)
    response.json.return_value = body
    return response


def _client(*, response=None, error=None):
    client = MagicMock()
    client.post = AsyncMock(return_value=response, side_effect=error)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _success():
    return {
        "candidates": [{"content": {"parts": [{"text": EXPECTED_RESULT}]}}],
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


@pytest.mark.asyncio
async def test_authenticated_smoke_action_bypasses_profile_quota_analysis_and_persistence():
    response = {"ok": True, "provider": "gemini", "model": "gemini-3.1-flash-lite", "result": EXPECTED_RESULT}
    with patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "user", "emailVerification": True})), patch(
        "src.main.ensure_user_profile", new=AsyncMock()
    ) as profile, patch("src.main.AppwriteTablesRateLimitStore") as rate_store, patch(
        "src.main.enforce_admission", new=AsyncMock()
    ) as admission, patch("src.main._analyze", new=AsyncMock()) as analyze, patch(
        "src.main.persist_check_result", new=AsyncMock()
    ) as persist, patch("src.main.run_gemini_smoke_test", new=AsyncMock(return_value=response)) as smoke:
        result = await _execute_request(
            {"action": "gemini_smoke_test"}, "dynamic-key", "user", "jwt", diagnostic_log=MagicMock()
        )
    assert result is response
    smoke.assert_awaited_once()
    profile.assert_not_awaited()
    rate_store.assert_not_called()
    admission.assert_not_awaited()
    analyze.assert_not_awaited()
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_unverified_user_cannot_run_smoke_action():
    with patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "user", "emailVerification": False})), patch(
        "src.main.run_gemini_smoke_test", new=AsyncMock()
    ) as smoke:
        with pytest.raises(PermissionError):
            await _execute_request({"action": "gemini_smoke_test"}, "dynamic-key", "user", "jwt")
    smoke.assert_not_awaited()
