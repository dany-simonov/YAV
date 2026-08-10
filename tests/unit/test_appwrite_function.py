"""Unit tests for the Appwrite Function security boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main import (
    EmailNotVerifiedError,
    _download_file_bytes,
    _execute_request,
    _get_file_metadata,
    _metadata_media_type,
    main,
)
from src.appwrite_store import ChecksPersistenceError
from core.exceptions import ExternalAPIError
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


def _appwrite_file_response(**overrides):
    """Realistic Appwrite Storage File response, including system fields."""
    response = {
        "$id": "file-id",
        "bucketId": "uploads",
        "$createdAt": "2026-08-09T12:00:00.000+00:00",
        "$updatedAt": "2026-08-09T12:00:00.000+00:00",
        "$permissions": ["read(\"user:user-id\")"],
        "name": "2026-08-09 15-28-02.mp4",
        "signature": "a" * 32,
        "mimeType": "video/mp4",
        "sizeOriginal": 4_500_000,
        "chunksTotal": 1,
        "chunksUploaded": 1,
        "encryption": False,
        "compression": "none",
    }
    response.update(overrides)
    return response


def _client_for_metadata(payload):
    response = MagicMock(status_code=200)
    response.json.return_value = payload
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
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

    request, api_key, user_id, user_jwt = execute_mock.call_args.args[:4]
    assert request.file_id == "file-id"
    assert api_key == "runtime-key"
    assert user_id == "runtime-user"
    assert user_jwt == "runtime-jwt"
    assert "analysis_result media_type=image" in context.log.call_args.args[0]


def test_main_uses_only_runtime_client_ip_for_admission():
    context = _context(
        {"text": "x" * 50},
        {
            "X-Appwrite-Key": "runtime-key",
            "X-Appwrite-User-Id": "runtime-user",
            "X-Appwrite-User-Jwt": "runtime-jwt",
            "X-Appwrite-Client-Ip": "2001:db8::1",
            "X-Forwarded-For": "198.51.100.99",
            "X-Real-IP": "198.51.100.98",
        },
    )
    result = {"media_type": "text", "verdict": "UNCERTAIN", "confidence": 0.5, "model_used": "sapling", "processing_ms": 10}
    with patch("src.main._execute_request", new=MagicMock(return_value=object())) as execute_mock, patch(
        "src.main._run_coro_sync", return_value=result
    ):
        main(context)

    assert execute_mock.call_args.args[5] == "2001:db8::1"


def test_main_internal_error_logs_safe_checks_operation_metadata():
    context = _context(
        {"text": "x" * 50},
        {"X-Appwrite-Key": "runtime-key", "X-Appwrite-User-Id": "runtime-user", "X-Appwrite-User-Jwt": "runtime-jwt"},
    )
    response = MagicMock(status_code=400)
    response.json.return_value = {
        "type": "row_invalid_structure", "code": 400,
        "message": "invalid runtime-user runtime-key Bearer runtime-jwt",
    }
    error = ChecksPersistenceError(
        "checks.create", response=response,
        data={"explanation": "private input", "details": "private details"},
        user_id="runtime-user", api_key="runtime-key",
    )
    with patch("src.main._execute_request", new=MagicMock(return_value=object())), patch(
        "src.main._run_coro_sync", side_effect=error
    ):
        payload, status = main(context)
    assert (payload, status) == ({"detail": "Внутренняя ошибка сервиса.", "code": "internal_error"}, 500)
    logged = context.log.call_args.args[0]
    assert "operation=checks.create" in logged
    assert "status_code=400" in logged
    assert "appwrite_type=row_invalid_structure" in logged
    for sensitive_value in ("runtime-user", "runtime-key", "runtime-jwt", "private input", "private details"):
        assert sensitive_value not in logged


def test_main_maps_external_api_error_to_existing_safe_provider_response_and_log():
    context = _context(
        {"text": "private analysis input " * 20},
        {
            "X-Appwrite-Key": "runtime-key",
            "X-Appwrite-User-Id": "runtime-user",
            "X-Appwrite-User-Jwt": "runtime-jwt",
        },
    )
    error = ExternalAPIError(
        "aiornot", "request_error", status_code=401, provider_message="invalid credentials"
    )
    with patch("src.main._execute_request", new=MagicMock(return_value=object())), patch(
        "src.main._run_coro_sync", side_effect=error
    ):
        payload, status = main(context)

    assert (payload, status) == (
        {"detail": "Сервис анализа временно недоступен.", "code": "provider_unavailable"},
        503,
    )
    logged = context.log.call_args.args[0]
    assert "operation=provider.external_api_error" in logged
    assert "provider=aiornot" in logged
    assert "safe_error_code=request_error" in logged
    assert "status_code=401" in logged
    assert "exception_class=ExternalAPIError" in logged
    assert "provider_message=invalid credentials" in logged
    for sensitive_value in ("runtime-user", "runtime-key", "runtime-jwt", "private analysis input"):
        assert sensitive_value not in logged


def test_external_api_error_log_never_renders_untrusted_service_or_detail():
    context = _context({"text": "x" * 50})
    error = ExternalAPIError(
        "aiornot\r\nBearer runtime-key",
        "request_error secret-body",
        provider_message="Authorization: Bearer runtime-key\nsecret-body",
    )
    with patch("src.main._execute_request", new=MagicMock(return_value=object())), patch(
        "src.main._run_coro_sync", side_effect=error
    ):
        _, status = main(context)

    assert status == 503
    logged = context.log.call_args.args[0]
    assert "provider=unknown" in logged
    assert "safe_error_code=unknown" in logged
    assert "runtime-key" not in logged
    assert "secret-body" not in logged
    assert "\n" not in logged
    assert "provider_message=" not in logged


def test_body_client_ip_is_rejected_before_execution():
    context = _context({"text": "x" * 50, "clientIp": "198.51.100.99"})
    with patch("src.main._execute_request", new=MagicMock()) as execute_mock:
        response = main(context)
    assert response[1] == 400
    assert response[0]["code"] == "invalid_request"
    execute_mock.assert_not_called()


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
async def test_metadata_accepts_realistic_appwrite_file_response(monkeypatch):
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project-id")
    client = _client_for_metadata(_appwrite_file_response())
    with patch("src.main.httpx.AsyncClient", return_value=client):
        metadata = await _get_file_metadata("file-id", "uploads", "runtime-jwt")
    assert metadata == {
        "name": "2026-08-09 15-28-02.mp4",
        "mimeType": "video/mp4",
        "sizeOriginal": 4_500_000,
    }
    assert _metadata_media_type(metadata).value == "video"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "expected_code"),
    [
        (_appwrite_file_response(name=None), "invalid_media"),
        (_appwrite_file_response(mimeType=None), "invalid_media"),
        (_appwrite_file_response(sizeOriginal="4500000"), "invalid_media"),
        (_appwrite_file_response(sizeOriginal=20 * 1024 * 1024 + 1), "file_too_large"),
        (_appwrite_file_response(sizeOriginal=0), "invalid_media"),
    ],
)
async def test_metadata_rejects_malformed_required_fields(monkeypatch, metadata, expected_code):
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project-id")
    client = _client_for_metadata(metadata)
    with patch("src.main.httpx.AsyncClient", return_value=client):
        with pytest.raises(SecurityValidationError) as raised:
            await _get_file_metadata("file-id", "uploads", "runtime-jwt")
    assert raised.value.code == expected_code


@pytest.mark.asyncio
async def test_execute_request_uses_runtime_identity_not_legacy_user_id():
    result = {"verdict": "REAL", "confidence": 0.8, "model_used": "sapling", "media_type": "text"}
    payload = {"text": "x" * 50, "userId": "foreign-user"}
    with patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True})), patch(
        "src.main.ensure_user_profile", new=AsyncMock(return_value={"$id": "runtime-user"})
    ), patch("src.main._analyze", new=AsyncMock(return_value=result)), patch(
        "src.main.persist_check_result", new=AsyncMock(return_value="check-1")
    ) as persist_mock:
        with patch("src.main.AppwriteTablesRateLimitStore"), patch(
            "src.main.enforce_admission", new=AsyncMock()
        ):
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
