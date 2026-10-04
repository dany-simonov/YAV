"""Unit tests for the Appwrite Function security boundary."""

import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from api.schemas import AnalysisResult, CredibilityAssessment
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from src.appwrite_store import CheckHistoryError, ChecksPersistenceError
from src.execution_deadline import ExecutionDeadline
from src.main import (
    EmailNotVerifiedError,
    _analyze,
    _download_file_bytes,
    _execute_request,
    _get_file_metadata,
    _metadata_media_type,
    main,
)
from src.provider_protection import admit_provider_operation
from src.rate_limit import RateLimitError
from src.source_ingestion import SourceDocument
from src.subscriptions import effective_quota_policy
from src.validation import SecurityValidationError, validate_request_payload
from src.workspaces import WorkspaceAccess, WorkspaceError


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


def test_main_handles_url_only_unified_complex_through_source_ingest_and_persistence():
    class UrlOnlyIngestor:
        async def ingest(self, url: str, *, diagnostic_log=None) -> SourceDocument:
            assert url == "https://example.com/article"
            assert callable(diagnostic_log)
            return SourceDocument(
                url=url,
                title="Публичная статья",
                description="",
                site_name="Example",
                text="Текст публичной статьи для комплексного анализа. " * 12,
                image_urls=(),
                video_urls=(),
                text_truncated=False,
            )

    context = _context(
        {"mode": "complex", "sourceUrl": "https://example.com/article", "fileIds": []},
        {
            "X-Appwrite-Key": "runtime-key",
            "X-Appwrite-User-Id": "runtime-user",
            "X-Appwrite-User-Jwt": "runtime-jwt",
        },
    )
    now = time.monotonic()
    deadline = ExecutionDeadline(now, now + 10, now + 8, now + 9)
    complex_result = AnalysisResult(
        verdict=Verdict.REAL,
        confidence=0.9,
        model_used=ModelUsed.GEMINI_TEXT,
        explanation="Текст проанализирован.",
        media_type=MediaType.TEXT,
        authenticity_index=95,
        analysis_mode="complex",
    )
    quota_store = MagicMock()
    quota_store.admit = AsyncMock()
    with patch("src.main.ExecutionDeadline.from_execution_timeout", return_value=deadline), patch(
        "src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True})
    ), patch("src.main.ensure_user_profile", new=AsyncMock(return_value={"$id": "runtime-user"})), patch(
        "src.main.AppwriteTablesRateLimitStore", return_value=quota_store,
    ), patch("src.main.SourceIngestor", return_value=UrlOnlyIngestor()) as ingestor, patch(
        "src.main._analyze_complex_text", new=AsyncMock(return_value=complex_result)
    ), patch("src.main.persist_check_result", new=AsyncMock(return_value="check-1")) as persist:
        payload, status = main(context)

    assert status == 200
    assert payload["check_id"] == "check-1"
    assert payload["analysis_mode"] == "complex"
    assert payload["source"]["url"] == "https://example.com/article"
    ingestor.assert_called_once_with()
    persist.assert_awaited_once()
    logs = [call.args[0] for call in context.log.call_args_list]
    assert "complex_stage=request_validated source_present=yes manual_text_present=no manual_file_count=0" in logs
    assert "complex_stage=source_start" in logs
    assert any(log.startswith("complex_stage=source_ingested text_present=yes") for log in logs)


@pytest.mark.asyncio
async def test_personal_history_dispatches_authenticated_actor_only():
    store = type(
        "Store",
        (),
        {
            "list_my_history": AsyncMock(
                return_value={"checks": [], "next_cursor": None, "page_size": 2}
            )
        },
    )()
    with (
        patch(
            "src.main.get_authenticated_account",
            new=AsyncMock(return_value={"$id": "user-a", "emailVerification": True}),
        ),
        patch("src.main.ensure_user_profile", new=AsyncMock(return_value={})),
        patch("src.main.AppwriteCheckHistoryStore", return_value=store),
    ):
        result = await _execute_request(
            {"action": "list_my_history", "pageSize": 2, "cursorAfter": "check-1"},
            "runtime-key",
            "user-a",
            "runtime-jwt",
        )

    assert result == {"checks": [], "next_cursor": None, "page_size": 2}
    store.list_my_history.assert_awaited_once_with(
        "user-a", page_size=2, cursor_after="check-1"
    )


@pytest.mark.asyncio
async def test_personal_history_store_failure_keeps_controlled_error_contract():
    store = type(
        "Store",
        (),
        {
            "get_my_check": AsyncMock(
                side_effect=CheckHistoryError("check_not_found", "Проверка не найдена.", 404)
            )
        },
    )()
    with (
        patch(
            "src.main.get_authenticated_account",
            new=AsyncMock(return_value={"$id": "user-a", "emailVerification": True}),
        ),
        patch("src.main.ensure_user_profile", new=AsyncMock(return_value={})),
        patch("src.main.AppwriteCheckHistoryStore", return_value=store),
        pytest.raises(SecurityValidationError) as raised,
    ):
        await _execute_request(
            {"action": "get_my_check", "checkId": "missing-check"},
            "runtime-key",
            "user-a",
            "runtime-jwt",
        )

    assert (raised.value.code, raised.value.status_code) == ("check_not_found", 404)


@pytest.mark.asyncio
async def test_complex_full_admission_is_committed_before_text_analysis():
    request = validate_request_payload({"mode": "complex", "text": "complex text " * 20})
    quota_store = MagicMock()
    quota_store.now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    quota_store.ip_subject.return_value = "ip-subject"
    quota_store.admit = AsyncMock()
    analysis_result = AnalysisResult(
        verdict=Verdict.REAL,
        confidence=0.9,
        model_used=ModelUsed.GEMINI_TEXT,
        explanation="Текст проанализирован.",
        media_type=MediaType.TEXT,
        authenticity_index=95,
        analysis_mode="complex",
    )

    async def text_analysis(*_args, **_kwargs):
        quota_store.admit.assert_awaited_once()
        plan = quota_store.admit.await_args.args[0]
        names = [item.dimension for item in plan.dimensions]
        assert names.count("subscription_checks_day") == 1
        assert {"ip_total_daily", "new_user_total_daily", "new_user_total_first7d", "new_user_hybrid_daily"} <= set(names)
        assert plan.provider_units == ()
        assert not any(name.startswith("global_") for name in names)
        return analysis_result

    with patch("src.main._analyze_complex_text", new=text_analysis):
        result = await _analyze(
            request,
            "runtime-jwt",
            quota_store=quota_store,
            user_id="runtime-user",
            account_created_at="2026-08-09T11:00:00+00:00",
            client_ip="192.0.2.1",
            effective_policy=effective_quota_policy("free"),
        )

    assert result["analysis_mode"] == "complex"


@pytest.mark.asyncio
async def test_complex_request_dynamically_admits_provider_operations_and_cleans_context():
    quota_store = MagicMock()
    quota_store.admit_provider_units = AsyncMock()

    async def complex_operation(*_args, **_kwargs):
        await admit_provider_operation("gemini")
        await admit_provider_operation("gemini")
        await admit_provider_operation("aiornot", 7)
        await admit_provider_operation("resemble")
        await admit_provider_operation("huggingface")
        return {"model_used": "gemini_text_verification"}

    with patch(
        "src.main.get_authenticated_account",
        new=AsyncMock(
            return_value={
                "$id": "runtime-user",
                "emailVerification": True,
                "$createdAt": "2026-08-09T12:00:00+00:00",
            }
        ),
    ), patch(
        "src.main.ensure_user_profile",
        new=AsyncMock(return_value={"$id": "runtime-user", "plan": "free"}),
    ), patch("src.main.AppwriteTablesRateLimitStore", return_value=quota_store), patch(
        "src.main._analyze", new=complex_operation
    ), patch("src.main.persist_check_result", new=AsyncMock(return_value="check-1")):
        result = await _execute_request(
            {"mode": "complex", "text": "x" * 200},
            "runtime-key",
            "runtime-user",
            "runtime-jwt",
        )

    assert result["check_id"] == "check-1"
    assert [call.args for call in quota_store.admit_provider_units.await_args_list] == [
        ("gemini", 1),
        ("gemini", 1),
        ("aiornot", 7),
        ("resemble", 1),
        ("huggingface", 1),
    ]
    await admit_provider_operation("gemini")
    assert quota_store.admit_provider_units.await_count == 5


@pytest.mark.asyncio
async def test_complex_provider_limit_is_typed_and_context_is_cleaned_after_exception():
    quota_store = MagicMock()
    quota_store.admit_provider_units = AsyncMock(
        side_effect=RateLimitError(
            "provider_temporarily_unavailable", "safe", 503
        )
    )

    async def complex_operation(*_args, **_kwargs):
        await admit_provider_operation("gemini")
        raise AssertionError("provider operation must not run after quota denial")

    with patch(
        "src.main.get_authenticated_account",
        new=AsyncMock(
            return_value={
                "$id": "runtime-user",
                "emailVerification": True,
                "$createdAt": "2026-08-09T12:00:00+00:00",
            }
        ),
    ), patch(
        "src.main.ensure_user_profile",
        new=AsyncMock(return_value={"$id": "runtime-user", "plan": "free"}),
    ), patch("src.main.AppwriteTablesRateLimitStore", return_value=quota_store), patch(
        "src.main._analyze", new=complex_operation
    ), pytest.raises(ProviderInfrastructureError) as raised:
        await _execute_request(
            {"mode": "complex", "text": "x" * 200},
            "runtime-key",
            "runtime-user",
            "runtime-jwt",
        )

    assert (raised.value.service, raised.value.kind) == ("gemini", "capacity")
    quota_store.admit_provider_units.assert_awaited_once_with(
        "gemini",
        1,
        user_id="runtime-user",
        provider_overrides={},
        workspace_id=None,
    )
    await admit_provider_operation("gemini")
    quota_store.admit_provider_units.assert_awaited_once()


@pytest.mark.asyncio
async def test_complex_context_is_cleaned_when_analysis_raises_before_provider_call():
    quota_store = MagicMock()
    quota_store.admit_provider_units = AsyncMock()

    async def complex_operation(*_args, **_kwargs):
        raise RuntimeError("analysis failed")

    with patch(
        "src.main.get_authenticated_account",
        new=AsyncMock(
            return_value={
                "$id": "runtime-user",
                "emailVerification": True,
                "$createdAt": "2026-08-09T12:00:00+00:00",
            }
        ),
    ), patch(
        "src.main.ensure_user_profile",
        new=AsyncMock(return_value={"$id": "runtime-user", "plan": "free"}),
    ), patch("src.main.AppwriteTablesRateLimitStore", return_value=quota_store), patch(
        "src.main._analyze", new=complex_operation
    ), pytest.raises(RuntimeError, match="analysis failed"):
        await _execute_request(
            {"mode": "complex", "text": "x" * 200},
            "runtime-key",
            "runtime-user",
            "runtime-jwt",
        )

    await admit_provider_operation("gemini")
    quota_store.admit_provider_units.assert_not_awaited()


def test_main_returns_distinct_safe_error_for_url_only_source_without_content():
    class EmptyIngestor:
        async def ingest(self, url: str, *, diagnostic_log=None) -> SourceDocument:
            assert url == "https://example.com/article"
            return SourceDocument(url, "", "", "", "", (), (), False)

    context = _context(
        {"mode": "complex", "sourceUrl": "https://example.com/article", "fileIds": []},
        {
            "X-Appwrite-Key": "runtime-key",
            "X-Appwrite-User-Id": "runtime-user",
            "X-Appwrite-User-Jwt": "runtime-jwt",
        },
    )
    now = time.monotonic()
    deadline = ExecutionDeadline(now, now + 10, now + 8, now + 9)
    quota_store = MagicMock()
    quota_store.admit = AsyncMock()
    with patch("src.main.ExecutionDeadline.from_execution_timeout", return_value=deadline), patch(
        "src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True})
    ), patch("src.main.ensure_user_profile", new=AsyncMock(return_value={"$id": "runtime-user"})), patch(
        "src.main.AppwriteTablesRateLimitStore", return_value=quota_store,
    ), patch("src.main.SourceIngestor", return_value=EmptyIngestor()):
        payload, status = main(context)

    assert status == 422
    assert payload == {
        "detail": "На странице не найден материал для анализа.",
        "code": "source_no_analyzable_content",
    }
    logs = [call.args[0] for call in context.log.call_args_list]
    assert "complex_stage=source_failed source_error_code=source_no_analyzable_content" in logs


def test_main_builds_analyze_response_with_the_same_root_deadline():
    context = _context(
        {"text": "x" * 50},
        {"X-Appwrite-Key": "runtime-key", "X-Appwrite-User-Id": "runtime-user", "X-Appwrite-User-Jwt": "runtime-jwt"},
    )
    now = time.monotonic()
    deadline = ExecutionDeadline(now, now + 1, now + 0.5, now + 0.8)
    result = {"media_type": "text", "verdict": "UNCERTAIN", "confidence": 0.5, "model_used": "sapling"}
    with patch("src.main.ExecutionDeadline.from_execution_timeout", return_value=deadline), patch(
        "src.main._execute_request", new=MagicMock(return_value=object())
    ) as execute_mock, patch("src.main._run_coro_sync", return_value=result):
        payload, status = main(context)

    assert (payload, status) == (result, 200)
    assert execute_mock.call_args.kwargs["execution_deadline"] is deadline
    assert context.log.called


def test_main_fails_closed_before_an_expired_final_response_stage():
    context = _context(
        {"text": "x" * 50},
        {"X-Appwrite-Key": "runtime-key", "X-Appwrite-User-Id": "runtime-user", "X-Appwrite-User-Jwt": "runtime-jwt"},
    )
    now = time.monotonic()
    deadline = ExecutionDeadline(now, now, now, now)
    result = {"media_type": "text", "verdict": "UNCERTAIN", "confidence": 0.5, "model_used": "sapling"}
    with patch("src.main.ExecutionDeadline.from_execution_timeout", return_value=deadline), patch(
        "src.main._execute_request", new=MagicMock(return_value=object())
    ) as execute_mock, patch("src.main._run_coro_sync", return_value=result):
        payload, status = main(context)

    assert (payload, status) == (
        {"detail": "Сервис анализа временно недоступен. Попробуйте позже.", "code": "provider_temporarily_unavailable"},
        503,
    )
    assert execute_mock.call_args.kwargs["execution_deadline"] is deadline
    context.log.assert_not_called()


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
    assert (payload, status) == (
        {
            "detail": "Сохранение результата временно недоступно. Попробуйте позже.",
            "code": "checks_persistence_unavailable",
        },
        503,
    )
    logged = context.log.call_args.args[0]
    assert "operation=checks.create" in logged
    assert "status_code=400" in logged
    assert "appwrite_type=row_invalid_structure" in logged
    for sensitive_value in ("runtime-user", "runtime-key", "runtime-jwt", "private input", "private details"):
        assert sensitive_value not in logged


@pytest.mark.parametrize("workspace_id", [None, "workspace-1"])
def test_main_maps_personal_and_workspace_check_persistence_errors_to_safe_503(
    workspace_id,
):
    payload = {"text": "x" * 50}
    if workspace_id is not None:
        payload["workspaceId"] = workspace_id
    context = _context(
        payload,
        {
            "X-Appwrite-Key": "runtime-key",
            "X-Appwrite-User-Id": "runtime-user",
            "X-Appwrite-User-Jwt": "runtime-jwt",
        },
    )
    error = ChecksPersistenceError(
        "profile.last_check_at.update",
        exc=httpx.ReadTimeout("private endpoint should not be public"),
        data={"details": "private details"},
        user_id="runtime-user",
        api_key="runtime-key",
    )
    with patch("src.main._execute_request", new=MagicMock(return_value=object())), patch(
        "src.main._run_coro_sync", side_effect=error
    ):
        response, status = main(context)

    assert (response, status) == (
        {
            "detail": "Сохранение результата временно недоступно. Попробуйте позже.",
            "code": "checks_persistence_unavailable",
        },
        503,
    )
    assert "private endpoint" not in context.log.call_args.args[0]


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
        "aiornot",
        "request_error",
        status_code=401,
        provider_message="invalid credentials",
        content_type="application/json",
        response_length=57,
        response_keys=("detail", "status"),
        response_paths=("detail",),
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
    assert "content_type=application/json" in logged
    assert "response_length=57" in logged
    assert "response_keys=detail,status" in logged
    assert "response_paths=detail" in logged
    assert "provider_message=invalid credentials" in logged
    for sensitive_value in ("runtime-user", "runtime-key", "runtime-jwt", "private analysis input"):
        assert sensitive_value not in logged


def test_main_logs_safe_gemini_generate_content_400_metadata():
    context = _context(
        {"text": "private analysis input " * 20},
        {
            "X-Appwrite-Key": "runtime-key",
            "X-Appwrite-User-Id": "runtime-user",
            "X-Appwrite-User-Jwt": "runtime-jwt",
        },
    )
    error = ExternalAPIError(
        "gemini",
        "request_rejected",
        status_code=400,
        provider_message="Invalid value at generationConfig.maxOutputTokens",
        operation="generate_content",
        upstream_status="INVALID_ARGUMENT",
        upstream_code=400,
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
    assert "provider=gemini" in logged
    assert "safe_error_code=request_rejected" in logged
    assert "gemini_operation=generate_content" in logged
    assert "google_status=INVALID_ARGUMENT google_code=400" in logged
    assert "provider_message=Invalid value at generationConfig.maxOutputTokens" in logged
    for sensitive_value in ("runtime-user", "runtime-key", "runtime-jwt", "private analysis input"):
        assert sensitive_value not in logged


def test_main_sanitizes_sapling_auth_error_without_logging_provider_data():
    context = _context(
        {"text": "Привет"},
        {
            "X-Appwrite-Key": "runtime-key",
            "X-Appwrite-User-Id": "runtime-user",
            "X-Appwrite-User-Jwt": "runtime-jwt",
        },
    )
    error = ExternalAPIError(
        "sapling",
        "request_error",
        status_code=401,
        provider_message="invalid key test_sapling_key",
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
    assert "provider=sapling" in logged
    assert "stage=request" in logged
    assert "category=auth_configuration" in logged
    assert "status_code=401" in logged
    assert "test_sapling_key" not in logged


def test_main_logs_safe_sapling_missing_key_diagnostic():
    context = _context(
        {"text": "Привет"},
        {
            "X-Appwrite-Key": "runtime-key",
            "X-Appwrite-User-Id": "runtime-user",
            "X-Appwrite-User-Jwt": "runtime-jwt",
        },
    )
    error = ProviderInfrastructureError(
        "sapling", "config", stage="config", reason="api_key_missing"
    )
    with patch("src.main._execute_request", new=MagicMock(return_value=object())), patch(
        "src.main._run_coro_sync", side_effect=error
    ):
        payload, status = main(context)

    assert (payload, status) == (
        {"detail": "Сервис анализа временно недоступен. Попробуйте позже.", "code": "provider_temporarily_unavailable"},
        503,
    )
    assert context.log.call_args.args[0] == (
        "provider_infrastructure_error operation=provider.infrastructure_error "
        "provider=sapling stage=config category=config reason=api_key_missing "
        "status_code=none exception_class=ProviderInfrastructureError"
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            ProviderInfrastructureError("sapling", "timeout", stage="request"),
            "provider=sapling stage=request category=timeout reason=none status_code=none",
        ),
        (
            ProviderInfrastructureError("sapling", "unavailable", stage="request", status_code=429),
            "provider=sapling stage=request category=unavailable reason=none status_code=429",
        ),
        (
            ProviderInfrastructureError("sapling", "invalid_response", stage="response"),
            "provider=sapling stage=response category=invalid_response reason=none status_code=none",
        ),
    ],
)
def test_main_logs_safe_sapling_infrastructure_categories(error, expected):
    context = _context({"text": "Привет"})
    with patch("src.main._execute_request", new=MagicMock(return_value=object())), patch(
        "src.main._run_coro_sync", side_effect=error
    ):
        _, status = main(context)

    assert status == 503
    assert expected in context.log.call_args.args[0]


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


def test_aiornot_short_plain_text_provider_message_is_logged_with_structure():
    context = _context({"text": "private analysis input " * 20})
    error = ExternalAPIError(
        "aiornot",
        "request_error",
        status_code=400,
        provider_message="some harmless provider error",
        content_type="text/plain",
        response_length=28,
    )
    with patch("src.main._execute_request", new=MagicMock(return_value=object())), patch(
        "src.main._run_coro_sync", side_effect=error
    ):
        _, status = main(context)
    assert status == 503
    logged = context.log.call_args.args[0]
    assert "status_code=400" in logged
    assert "content_type=text/plain" in logged
    assert "response_length=28" in logged
    assert "provider_message=some harmless provider error" in logged
    assert "private analysis input" not in logged


def test_external_api_error_log_omits_provider_message_for_another_provider():
    context = _context({"text": "x" * 50})
    error = ExternalAPIError(
        "sapling", "request_error", status_code=400, provider_message="arbitrary plain response"
    )
    with patch("src.main._execute_request", new=MagicMock(return_value=object())), patch(
        "src.main._run_coro_sync", side_effect=error
    ):
        _, status = main(context)
    assert status == 503
    logged = context.log.call_args.args[0]
    assert "provider=sapling" in logged
    assert "arbitrary plain response" not in logged
    assert "provider_message=" not in logged


def test_sightengine_json_4xx_diagnostic_is_logged_without_credentials_or_request_data():
    context = _context({"text": "private analysis input " * 20})
    error = ExternalAPIError(
        "sightengine",
        "request_error",
        status_code=422,
        provider_message="code=invalid_model message=Unknown model",
    )
    with patch("src.main._execute_request", new=MagicMock(return_value=object())), patch(
        "src.main._run_coro_sync", side_effect=error
    ):
        _, status = main(context)
    assert status == 503
    logged = context.log.call_args.args[0]
    assert "provider=sightengine" in logged
    assert "status_code=422" in logged
    assert "provider_message=code=invalid_model message=Unknown model" in logged
    assert "private analysis input" not in logged


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
async def test_execute_request_keeps_personal_quota_path_without_workspace_id():
    result = {
        "verdict": "REAL",
        "confidence": 0.8,
        "model_used": "sapling",
        "media_type": "text",
    }
    rate_store = MagicMock()
    with patch(
        "src.main.get_authenticated_account",
        new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True}),
    ), patch(
        "src.main.ensure_user_profile",
        new=AsyncMock(return_value={"$id": "runtime-user"}),
    ), patch("src.main.AppwriteWorkspaceStore") as workspace_store, patch(
        "src.main.AppwriteTablesRateLimitStore", return_value=rate_store
    ), patch("src.main._analyze", new=AsyncMock(return_value=result)) as analyze, patch(
        "src.main.persist_check_result", new=AsyncMock(return_value="check-1")
    ):
        await _execute_request(
            {"text": "Текст для персонального анализа."},
            "dynamic-key",
            "runtime-user",
            "runtime-jwt",
        )

    workspace_store.assert_not_called()
    assert analyze.await_args.args[3] is rate_store
    assert analyze.await_args.kwargs["workspace_access"] is None


@pytest.mark.asyncio
async def test_execute_request_resolves_and_passes_explicit_workspace_context():
    result = {
        "verdict": "REAL",
        "confidence": 0.8,
        "model_used": "sapling",
        "media_type": "text",
    }
    access = WorkspaceAccess(
        workspace_id="workspace-1",
        actor_user_id="runtime-user",
        role="member",
        workspace={"$id": "workspace-1", "quota_plan": "pro"},
    )
    workspace_store = MagicMock()
    workspace_store.resolve_workspace_access = AsyncMock(return_value=access)
    with patch(
        "src.main.get_authenticated_account",
        new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True}),
    ), patch(
        "src.main.ensure_user_profile",
        new=AsyncMock(
            return_value={"$id": "runtime-user", "subscription": "enterprise"}
        ),
    ), patch("src.main.AppwriteWorkspaceStore", return_value=workspace_store), patch(
        "src.main.AppwriteTablesRateLimitStore"
    ), patch("src.main._analyze", new=AsyncMock(return_value=result)) as analyze, patch(
        "src.main.persist_check_result", new=AsyncMock(return_value="check-1")
    ) as persist:
        await _execute_request(
            {"text": "Текст для workspace анализа.", "workspaceId": "workspace-1"},
            "dynamic-key",
            "runtime-user",
            "runtime-jwt",
        )

    workspace_store.resolve_workspace_access.assert_awaited_once_with(
        "runtime-user", "workspace-1"
    )
    assert analyze.await_args.kwargs["workspace_access"] is access
    assert analyze.await_args.kwargs["effective_policy"].subscription == "pro"
    assert persist.await_args.kwargs["workspace_id"] == "workspace-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        WorkspaceError("workspace_access_denied", "Нет доступа к workspace.", 403),
        WorkspaceError("workspace_not_found", "Workspace не найден.", 404),
    ],
)
async def test_execute_request_maps_workspace_access_errors_before_quota(error):
    workspace_store = MagicMock()
    workspace_store.resolve_workspace_access = AsyncMock(side_effect=error)
    with patch(
        "src.main.get_authenticated_account",
        new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True}),
    ), patch(
        "src.main.ensure_user_profile",
        new=AsyncMock(return_value={"$id": "runtime-user"}),
    ), patch("src.main.AppwriteWorkspaceStore", return_value=workspace_store), patch(
        "src.main.AppwriteTablesRateLimitStore"
    ) as rate_store, patch("src.main._analyze", new=AsyncMock()) as analyze:
        with pytest.raises(SecurityValidationError) as raised:
            await _execute_request(
                {"text": "Текст для workspace анализа.", "workspaceId": "workspace-1"},
                "dynamic-key",
                "runtime-user",
                "runtime-jwt",
            )

    assert raised.value.code == error.code
    assert raised.value.status_code == error.status_code
    rate_store.assert_not_called()
    analyze.assert_not_awaited()


def _exploding_diagnostic_callback(_message: str) -> None:
    raise RuntimeError("diagnostic failure")


@pytest.mark.asyncio
async def test_execute_request_ignores_throwing_diagnostic_callback_on_gemini_success():
    result = {
        "verdict": "REAL", "confidence": 0.8, "model_used": "gemini_text_verification",
        "media_type": "text",
    }
    with patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True})), patch(
        "src.main.ensure_user_profile", new=AsyncMock(return_value={"$id": "runtime-user"})
    ), patch("src.main._analyze", new=AsyncMock(return_value=result)), patch(
        "src.main.persist_check_result", new=AsyncMock(return_value="check-1")
    ) as persist_mock, patch("src.main.AppwriteTablesRateLimitStore"), patch(
        "src.main.enforce_admission", new=AsyncMock()
    ):
        response = await _execute_request(
            {"text": "Привет"}, "dynamic-key", "runtime-user", "runtime-jwt",
            diagnostic_log=_exploding_diagnostic_callback,
        )
    assert response["check_id"] == "check-1"
    persist_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_request_preserves_provider_error_when_diagnostic_callback_throws():
    provider_error = ProviderInfrastructureError("gemini", "timeout", stage="request")
    with patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True})), patch(
        "src.main.ensure_user_profile", new=AsyncMock(return_value={"$id": "runtime-user"})
    ), patch("src.main._analyze", new=AsyncMock(side_effect=provider_error)), patch(
        "src.main.persist_check_result", new=AsyncMock()
    ) as persist_mock, patch("src.main.AppwriteTablesRateLimitStore"), patch(
        "src.main.enforce_admission", new=AsyncMock()
    ):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await _execute_request(
                {"text": "Привет"}, "dynamic-key", "runtime-user", "runtime-jwt",
                diagnostic_log=_exploding_diagnostic_callback,
            )
    assert raised.value is provider_error
    persist_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_request_preserves_persistence_error_when_diagnostic_callback_throws():
    result = {
        "verdict": "REAL", "confidence": 0.8, "model_used": "gemini_text_verification",
        "media_type": "text",
    }
    persistence_error = RuntimeError("persistence failure")
    with patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "runtime-user", "emailVerification": True})), patch(
        "src.main.ensure_user_profile", new=AsyncMock(return_value={"$id": "runtime-user"})
    ), patch("src.main._analyze", new=AsyncMock(return_value=result)), patch(
        "src.main.persist_check_result", new=AsyncMock(side_effect=persistence_error)
    ), patch("src.main.AppwriteTablesRateLimitStore"), patch(
        "src.main.enforce_admission", new=AsyncMock()
    ):
        with pytest.raises(RuntimeError) as raised:
            await _execute_request(
                {"text": "Привет"}, "dynamic-key", "runtime-user", "runtime-jwt",
                diagnostic_log=_exploding_diagnostic_callback,
            )
    assert raised.value is persistence_error


@pytest.mark.asyncio
async def test_function_response_adds_short_report_after_canonical_analysis():
    canonical = AnalysisResult(
        verdict=Verdict.FAKE,
        confidence=0.8,
        model_used=ModelUsed.SAPLING,
        explanation="Provider explanation",
        media_type=MediaType.TEXT,
        semantics_version=2,
        ai_probability=0.8,
        authenticity_index=20,
    )
    router = MagicMock()
    router.route = AsyncMock(return_value=canonical)

    credibility = CredibilityAssessment(
        status="completed", credibility_index=80, verdict="MOSTLY_CREDIBLE", confidence=0.8,
        summary="Ключевые утверждения в целом подтверждаются доступными источниками.",
    )
    with patch("src.main.MediaRouter", return_value=router), patch(
        "src.main.GeminiCredibilityAdapter.analyze", new=AsyncMock(return_value=credibility)
    ):
        response = await _analyze(
            validate_request_payload({"text": "x" * 50}), "runtime-jwt"
        )

    assert response["credibility"]["credibility_index"] == 80
    assert "80/100" in response["short_report"]
    assert "вероятность составила 80%" in response["short_report"]
    assert response["verdict"] == "FAKE"


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
