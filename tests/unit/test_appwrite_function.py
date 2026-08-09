"""Unit tests for the Appwrite Function security boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main import (
    EmailNotVerifiedError,
    _download_file_bytes,
    _execute_request,
    _get_file_metadata,
    main,
)
from src.validation import SecurityValidationError


def _context(payload, headers=None):
    return SimpleNamespace(
        req=SimpleNamespace(body_json=payload, headers=headers or {}),
        res=SimpleNamespace(json=lambda response, status=200: (response, status)),
        log=MagicMock(),
    )


def _stream_response(status_code=200, chunks=(b"file-bytes",)):
    response = MagicMock(status_code=status_code)

    async def aiter_bytes():
        for chunk in chunks:
            yield chunk

    response.aiter_bytes = aiter_bytes
    stream = MagicMock()
    stream.__aenter__ = AsyncMock(return_value=response)
    stream.__aexit__ = AsyncMock(return_value=False)
    return stream


def _client_for_stream(stream):
    client = AsyncMock()
    client.stream = MagicMock(return_value=stream)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def test_main_passes_dynamic_api_key_from_request_headers():
    context = _context(
        {"fileId": "file-id", "mediaType": "image"},
        {
            "X-Appwrite-Key": "runtime-key",
            "X-Appwrite-User-Id": "runtime-user",
            "X-Appwrite-User-Jwt": "runtime-jwt",
        },
    )
    result = {"media_type": "image", "verdict": "UNCERTAIN", "confidence": 0.5, "model_used": "sightengine", "processing_ms": 10}
    with patch("src.main._execute_request", new=MagicMock(return_value=object())) as execute_mock, patch(
        "src.main._run_coro_sync", return_value=result
    ):
        main(context)

    request, api_key, user_id, user_jwt = execute_mock.call_args.args
    assert request.file_id == "file-id"
    assert api_key == "runtime-key"
    assert user_id == "runtime-user"
    assert user_jwt == "runtime-jwt"
    assert "analysis_result media_type=image" in context.log.call_args.args[0]


@pytest.mark.asyncio
async def test_download_uses_runtime_user_jwt_and_not_dynamic_key(monkeypatch):
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project-id")
    client = _client_for_stream(_stream_response())

    with patch("src.main.httpx.AsyncClient", return_value=client):
        result = await _download_file_bytes("file-id", "bucket-id", "runtime-jwt")

    assert result == b"file-bytes"
    headers = client.stream.call_args.kwargs["headers"]
    assert headers["X-Appwrite-JWT"] == "runtime-jwt"
    assert "X-Appwrite-Key" not in headers


@pytest.mark.asyncio
async def test_download_rejects_actual_stream_over_limit(monkeypatch):
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project-id")
    stream = _stream_response(chunks=(b"x" * (20 * 1024 * 1024), b"x"))
    with patch("src.main.httpx.AsyncClient", return_value=_client_for_stream(stream)):
        with pytest.raises(SecurityValidationError) as raised:
            await _download_file_bytes("file-id", "uploads", "runtime-jwt")
    assert raised.value.code == "file_too_large"


@pytest.mark.asyncio
async def test_download_hides_forbidden_file_details(monkeypatch):
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project-id")
    with patch("src.main.httpx.AsyncClient", return_value=_client_for_stream(_stream_response(403))):
        with pytest.raises(SecurityValidationError) as raised:
            await _download_file_bytes("foreign-file", "uploads", "runtime-jwt")
    assert raised.value.code == "file_not_accessible"
    assert "foreign-file" not in raised.value.detail


@pytest.mark.asyncio
async def test_metadata_checks_size_before_download(monkeypatch):
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project-id")
    response = MagicMock(status_code=200)
    response.json.return_value = {"$sizeOriginal": 20 * 1024 * 1024 + 1, "$name": "image.jpg"}
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("src.main.httpx.AsyncClient", return_value=client):
        with pytest.raises(SecurityValidationError) as raised:
            await _get_file_metadata("file-id", "uploads", "runtime-jwt")
    assert raised.value.code == "file_too_large"


@pytest.mark.asyncio
async def test_execute_request_uses_runtime_identity_not_legacy_user_id():
    result = {"verdict": "REAL", "confidence": 0.8, "model_used": "sapling", "media_type": "text"}
    payload = {"text": "x" * 50, "userId": "foreign-user"}
    with patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True})), patch(
        "src.main.ensure_user_profile", new=AsyncMock(return_value={"$id": "runtime-user"})
    ), patch("src.main._analyze", new=AsyncMock(return_value=result)), patch(
        "src.main.persist_check_result", new=AsyncMock(return_value="check-1")
    ) as persist_mock:
        response = await _execute_request(payload, "dynamic-key", "runtime-user", "runtime-jwt")

    assert response["check_id"] == "check-1"
    assert persist_mock.await_args.args[1] == "runtime-user"
    assert persist_mock.await_args.args[3] == "dynamic-key"


@pytest.mark.asyncio
async def test_unverified_analyze_is_rejected_before_analysis_or_persistence():
    with patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": False})), patch(
        "src.main.ensure_user_profile", new=AsyncMock(return_value={"$id": "runtime-user"})
    ), patch("src.main._analyze", new=AsyncMock()) as analyze_mock, patch(
        "src.main.persist_check_result", new=AsyncMock()
    ) as persist_mock:
        with pytest.raises(EmailNotVerifiedError):
            await _execute_request({"text": "x" * 50}, "dynamic-key", "runtime-user", "runtime-jwt")
    analyze_mock.assert_not_awaited()
    persist_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_profile_remains_available_to_unverified_user():
    with patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": False})), patch(
        "src.main.ensure_user_profile", new=AsyncMock(return_value={"$id": "runtime-user"})
    ), patch("src.main._analyze", new=AsyncMock()) as analyze_mock:
        response = await _execute_request({"action": "ensure_profile"}, "dynamic-key", "runtime-user", "runtime-jwt")
    assert response == {"profile_id": "runtime-user"}
    analyze_mock.assert_not_awaited()


def test_main_keeps_email_not_verified_contract():
    context = _context({"text": "x" * 50})
    with patch("src.main._execute_request", new=MagicMock(side_effect=EmailNotVerifiedError())):
        response = main(context)
    assert response == ({"detail": "Подтвердите email перед запуском анализа.", "code": "email_not_verified"}, 403)


def test_main_never_returns_raw_exception_text():
    context = _context({"text": "x" * 50})
    with patch("src.main._execute_request", new=MagicMock(side_effect=RuntimeError("secret-token"))):
        response = main(context)
    assert response == ({"detail": "Внутренняя ошибка сервиса.", "code": "internal_error"}, 500)
