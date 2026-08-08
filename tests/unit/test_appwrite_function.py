"""Unit tests for Appwrite Function runtime integration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main import _download_file_bytes, _execute_request, main


def test_main_passes_dynamic_api_key_from_request_headers():
    log = MagicMock()
    context = SimpleNamespace(
        req=SimpleNamespace(
            body_json={"fileId": "file-id", "mediaType": "image"},
            headers={
                "X-Appwrite-Key": "runtime-key",
                "X-Appwrite-User-Id": "runtime-user",
                "X-Appwrite-User-Jwt": "runtime-jwt",
            },
        ),
        res=SimpleNamespace(json=lambda payload, status=200: (payload, status)),
        log=log,
    )

    result = {
        "media_type": "image",
        "verdict": "UNCERTAIN",
        "confidence": 0.5,
        "model_used": "sightengine",
        "processing_ms": 10,
    }
    with patch(
        "src.main._execute_request", new=MagicMock(return_value=object())
    ) as execute_mock, patch(
        "src.main._run_coro_sync", return_value=result
    ):
        main(context)

    execute_mock.assert_called_once_with(
        {"fileId": "file-id", "mediaType": "image"},
        "runtime-key",
        "runtime-user",
        "runtime-jwt",
    )
    assert "analysis_result media_type=image" in log.call_args.args[0]


@pytest.mark.asyncio
async def test_download_prefers_dynamic_api_key_over_environment(monkeypatch):
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project-id")
    monkeypatch.setenv("APPWRITE_FUNCTION_API_KEY", "fallback-key")

    response = MagicMock(status_code=200, content=b"file-bytes")
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.main.httpx.AsyncClient", return_value=client):
        result = await _download_file_bytes("file-id", "bucket-id", "runtime-key")

    assert result == b"file-bytes"
    assert client.get.await_args.kwargs["headers"]["X-Appwrite-Key"] == "runtime-key"


@pytest.mark.asyncio
async def test_execute_request_uses_runtime_identity_not_payload_user_id():
    result = {
        "verdict": "REAL",
        "confidence": 0.8,
        "model_used": "sapling",
        "media_type": "text",
    }
    with patch(
        "src.main.get_authenticated_account",
        new=AsyncMock(return_value={"$id": "runtime-user"}),
    ), patch(
        "src.main.ensure_user_profile",
        new=AsyncMock(return_value={"$id": "runtime-user"}),
    ), patch(
        "src.main._analyze", new=AsyncMock(return_value=result)
    ), patch(
        "src.main.persist_check_result", new=AsyncMock(return_value="check-1")
    ) as persist_mock:
        response = await _execute_request(
            {"text": "content", "userId": "foreign-user"},
            "dynamic-key",
            "runtime-user",
            "runtime-jwt",
        )

    assert response["check_id"] == "check-1"
    assert persist_mock.await_args.args[1] == "runtime-user"


@pytest.mark.asyncio
async def test_execute_request_rejects_unauthenticated_request():
    with pytest.raises(PermissionError, match="Authenticated Appwrite user context"):
        await _execute_request({"text": "content"}, "dynamic-key", "", "")


@pytest.mark.asyncio
async def test_execute_request_rejects_unknown_action_before_side_effects():
    with patch("src.main.get_authenticated_account", new=AsyncMock()) as account_mock:
        with pytest.raises(ValueError, match="Unsupported action"):
            await _execute_request(
                {"action": "delete_all_users"},
                "dynamic-key",
                "runtime-user",
                "runtime-jwt",
            )
    account_mock.assert_not_awaited()
