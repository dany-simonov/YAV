"""Unit tests for Appwrite Function runtime integration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main import EmailNotVerifiedError, _download_file_bytes, _execute_request, main


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
async def test_download_uses_runtime_user_jwt_and_not_dynamic_key(monkeypatch):
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project-id")

    response = MagicMock(status_code=200, content=b"file-bytes")
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.main.httpx.AsyncClient", return_value=client):
        result = await _download_file_bytes("file-id", "bucket-id", "runtime-jwt")

    assert result == b"file-bytes"
    headers = client.get.await_args.kwargs["headers"]
    assert headers["X-Appwrite-JWT"] == "runtime-jwt"
    assert "X-Appwrite-Key" not in headers


@pytest.mark.asyncio
async def test_download_hides_forbidden_file_details(monkeypatch):
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project-id")

    response = MagicMock(status_code=403)
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.main.httpx.AsyncClient", return_value=client):
        with pytest.raises(PermissionError) as exc_info:
            await _download_file_bytes("foreign-file", "uploads", "runtime-jwt")

    assert "foreign-file" not in str(exc_info.value)
    assert "uploads" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_download_rejects_missing_runtime_user_jwt(monkeypatch):
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project-id")

    with pytest.raises(PermissionError, match="Authenticated Appwrite user context"):
        await _download_file_bytes("file-id", "bucket-id", "")


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
        new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True}),
    ), patch(
        "src.main.ensure_user_profile",
        new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True}),
    ), patch(
        "src.main._analyze", new=AsyncMock(return_value=result)
    ), patch(
        "src.main.persist_check_result", new=AsyncMock(return_value="check-1")
    ) as persist_mock:
        response = await _execute_request(
            {
                "text": "content",
                "userId": "foreign-user",
                "userJwt": "payload-controlled-jwt",
            },
            "dynamic-key",
            "runtime-user",
            "runtime-jwt",
        )

    assert response["check_id"] == "check-1"
    assert persist_mock.await_args.args[1] == "runtime-user"
    assert persist_mock.await_args.args[3] == "dynamic-key"


@pytest.mark.asyncio
async def test_file_payload_passes_runtime_jwt_to_download_and_keeps_db_key():
    result = {
        "verdict": "REAL",
        "confidence": 0.8,
        "model_used": "sightengine",
        "media_type": "image",
    }
    with patch(
        "src.main.get_authenticated_account",
        new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True}),
    ), patch(
        "src.main.ensure_user_profile",
        new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True}),
    ) as profile_mock, patch(
        "src.main._download_file_bytes", new=AsyncMock(return_value=b"own-file")
    ) as download_mock, patch(
        "src.main.MediaRouter.route", new=AsyncMock(return_value=result)
    ), patch(
        "src.main.persist_check_result", new=AsyncMock(return_value="check-1")
    ) as persist_mock:
        response = await _execute_request(
            {"fileId": "file-id", "mediaType": "image"},
            "dynamic-key",
            "runtime-user",
            "runtime-jwt",
        )

    assert response["check_id"] == "check-1"
    download_mock.assert_awaited_once_with("file-id", "uploads", "runtime-jwt")
    assert profile_mock.await_args.args[1] == "dynamic-key"
    assert persist_mock.await_args.args[3] == "dynamic-key"


@pytest.mark.asyncio
async def test_forbidden_file_stops_analysis_and_persistence():
    with patch(
        "src.main.get_authenticated_account",
        new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True}),
    ), patch(
        "src.main.ensure_user_profile",
        new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True}),
    ), patch(
        "src.main._download_file_bytes",
        new=AsyncMock(side_effect=PermissionError("File is not accessible")),
    ), patch("src.main.MediaRouter.route", new=AsyncMock()) as route_mock, patch(
        "src.main.persist_check_result", new=AsyncMock()
    ) as persist_mock:
        with pytest.raises(PermissionError, match="not accessible"):
            await _execute_request(
                {"fileId": "foreign-file", "mediaType": "image"},
                "dynamic-key",
                "runtime-user",
                "runtime-jwt",
            )

    route_mock.assert_not_awaited()
    persist_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_analysis_does_not_attempt_storage_download():
    result = {
        "verdict": "REAL",
        "confidence": 0.8,
        "model_used": "sapling",
        "media_type": "text",
    }
    with patch(
        "src.main.get_authenticated_account",
        new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True}),
    ), patch(
        "src.main.ensure_user_profile",
        new=AsyncMock(return_value={"$id": "runtime-user"}),
    ), patch(
        "src.main._download_file_bytes", new=AsyncMock()
    ) as download_mock, patch(
        "src.main.MediaRouter.route", new=AsyncMock(return_value=result)
    ), patch(
        "src.main.persist_check_result", new=AsyncMock(return_value="check-1")
    ):
        response = await _execute_request(
            {"text": "content", "mediaType": "text"},
            "dynamic-key",
            "runtime-user",
            "runtime-jwt",
        )

    assert response["check_id"] == "check-1"
    download_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_unverified_analyze_is_rejected_before_analysis_or_persistence():
    with patch(
        "src.main.get_authenticated_account",
        new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": False}),
    ), patch(
        "src.main.ensure_user_profile",
        new=AsyncMock(return_value={"$id": "runtime-user"}),
    ), patch("src.main._analyze", new=AsyncMock()) as analyze_mock, patch(
        "src.main._download_file_bytes", new=AsyncMock()
    ) as download_mock, patch(
        "src.main.persist_check_result", new=AsyncMock()
    ) as persist_mock:
        with pytest.raises(EmailNotVerifiedError):
            await _execute_request(
                {
                    "fileId": "file-id",
                    "mediaType": "image",
                    "emailVerification": True,
                    "email_verified": True,
                },
                "dynamic-key",
                "runtime-user",
                "runtime-jwt",
            )

    analyze_mock.assert_not_awaited()
    download_mock.assert_not_awaited()
    persist_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_profile_remains_available_to_unverified_user():
    with patch(
        "src.main.get_authenticated_account",
        new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": False}),
    ), patch(
        "src.main.ensure_user_profile",
        new=AsyncMock(return_value={"$id": "runtime-user"}),
    ), patch("src.main._analyze", new=AsyncMock()) as analyze_mock:
        response = await _execute_request(
            {"action": "ensure_profile"},
            "dynamic-key",
            "runtime-user",
            "runtime-jwt",
        )

    assert response == {"profile_id": "runtime-user"}
    analyze_mock.assert_not_awaited()


def test_main_returns_stable_403_for_unverified_account():
    context = SimpleNamespace(
        req=SimpleNamespace(body_json={"text": "content"}, headers={}),
        res=SimpleNamespace(json=lambda payload, status=200: (payload, status)),
    )
    with patch(
        "src.main._execute_request",
        new=MagicMock(
            side_effect=EmailNotVerifiedError("internal message must not leak"),
        ),
    ):
        response = main(context)

    assert response == (
        {
            "detail": "Подтвердите email перед запуском анализа.",
            "code": "email_not_verified",
        },
        403,
    )


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
