"""Appwrite Function entrypoint for media/text analysis.

This file is used by Appwrite Functions with entrypoint `src/main.py`.
It supports two payload shapes from frontend:
- {"text": "...", "mediaType": "text", ...}
- {"fileId": "...", "mediaType": "image|audio|video", ...}
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

# Ensure project root imports work when entrypoint is src/main.py
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.enums import MediaType  # noqa: E402
from core.analyzer import HybridTextAnalyzer  # noqa: E402
from core.exceptions import ProviderInfrastructureError  # noqa: E402
from router.media_router import MediaRouter  # noqa: E402
from src.appwrite_store import (  # noqa: E402
    ChecksPersistenceError,
    ensure_user_profile,
    get_authenticated_account,
    persist_check_result,
)
from src.media_validation import validate_media_bytes  # noqa: E402
from src.rate_limit import AppwriteTablesRateLimitStore, RateLimitError, enforce_admission  # noqa: E402
from src.provider_protection import begin_provider_budget, end_provider_budget  # noqa: E402
from src.validation import (  # noqa: E402
    FileAnalyzeRequest,
    SecurityValidationError,
    TextAnalyzeRequest,
    ValidatedRequest,
    MAX_FILE_BYTES,
    MAX_FILENAME_LENGTH,
    parse_json_object,
    validate_request_payload,
)


class EmailNotVerifiedError(PermissionError):
    """Raised when an authenticated account has not verified its email."""


_METADATA_MIME_TYPES = {
    "image/jpeg": MediaType.IMAGE,
    "image/png": MediaType.IMAGE,
    "image/webp": MediaType.IMAGE,
    "audio/mpeg": MediaType.AUDIO,
    "audio/wav": MediaType.AUDIO,
    "audio/ogg": MediaType.AUDIO,
    "audio/mp4": MediaType.AUDIO,
    "video/mp4": MediaType.VIDEO,
    "video/avi": MediaType.VIDEO,
    "video/quicktime": MediaType.VIDEO,
}
_METADATA_EXTENSIONS = {
    ".jpg": MediaType.IMAGE,
    ".jpeg": MediaType.IMAGE,
    ".png": MediaType.IMAGE,
    ".webp": MediaType.IMAGE,
    ".mp3": MediaType.AUDIO,
    ".wav": MediaType.AUDIO,
    ".ogg": MediaType.AUDIO,
    ".m4a": MediaType.AUDIO,
    ".mp4": MediaType.VIDEO,
    ".avi": MediaType.VIDEO,
    ".mov": MediaType.VIDEO,
}


def _extract_payload(req: Any) -> dict[str, Any]:
    """Read a bounded JSON object without silently coercing malformed input."""
    raw = getattr(req, "body", None)
    if callable(raw):
        raw = raw()
    if raw not in (None, "", b""):
        return parse_json_object(raw)

    # Runtime variants may expose only a parsed JSON object. It remains subject
    # to the same schema and serialized-size checks below.
    for attr in ("body_json", "bodyJson", "json"):
        value = getattr(req, attr, None)
        if callable(value):
            value = value()
        if value is not None:
            if not isinstance(value, dict):
                raise SecurityValidationError("invalid_json", "JSON должен быть объектом.")
            return value
    raise SecurityValidationError("invalid_json", "Некорректный JSON запроса.")


def _extract_request_header(req: Any, header_name: str) -> str:
    """Extract a request header without assuming a concrete mapping type."""
    headers = getattr(req, "headers", None)
    if callable(headers):
        headers = headers()
    if not headers:
        return ""

    try:
        for name, value in headers.items():
            if str(name).lower() == header_name.lower():
                return str(value).strip()
    except (AttributeError, TypeError):
        pass

    try:
        value = headers[header_name]
    except (KeyError, TypeError):
        return ""
    return str(value).strip()


def _extract_dynamic_api_key(req: Any) -> str:
    """Extract the per-execution Appwrite API key from request headers."""
    return _extract_request_header(req, "x-appwrite-key")


def _validate_storage_metadata(metadata: Any) -> dict[str, Any]:
    """Validate and retain only the Storage fields used by the security boundary.

    Appwrite Storage adds system fields to its File response.  They are trusted
    response-shape extras rather than request input, so deliberately ignore
    them after validating the narrow subset required for analysis.
    """
    if not isinstance(metadata, dict):
        raise SecurityValidationError("storage_unavailable", "Хранилище временно недоступно.", 502)

    filename = metadata.get("name")
    mime_type = metadata.get("mimeType")
    size = metadata.get("sizeOriginal")
    if (
        not isinstance(filename, str)
        or not filename
        or len(filename) > MAX_FILENAME_LENGTH
        or not isinstance(mime_type, str)
        or not mime_type
        or isinstance(size, bool)
        or not isinstance(size, int)
    ):
        raise SecurityValidationError("invalid_media", "Некорректные метаданные файла.", 422)
    if size <= 0:
        raise SecurityValidationError("invalid_media", "Файл пустой.", 422)
    if size > MAX_FILE_BYTES:
        raise SecurityValidationError("file_too_large", "Файл превышает лимит в 20 MiB.", 413)

    return {"name": filename, "mimeType": mime_type, "sizeOriginal": size}


def _metadata_media_type(metadata: dict[str, Any]) -> MediaType:
    """Use Storage MIME/name as corroborating signals, never as file truth."""
    mime = metadata.get("mimeType")
    filename = metadata.get("name")
    if not isinstance(mime, str) or not isinstance(filename, str):
        raise SecurityValidationError("invalid_media", "Некорректные метаданные файла.", 422)
    mime_type = _METADATA_MIME_TYPES.get(mime.split(";", 1)[0].strip().lower())
    suffix = Path(filename).suffix.lower()
    extension_type = _METADATA_EXTENSIONS.get(suffix)
    if not mime_type or not extension_type:
        raise SecurityValidationError("unsupported_media_type", "Неподдерживаемый формат файла.", 415)
    if mime_type != extension_type:
        raise SecurityValidationError(
            "media_type_mismatch", "Метаданные файла не соответствуют формату.", 415
        )
    return mime_type


def _response_json(context: Any, payload: dict[str, Any], status: int = 200):
    """Return JSON response compatible with Appwrite runtime variants."""
    safe_payload = json.loads(json.dumps(payload, default=str))
    try:
        return context.res.json(safe_payload, status)
    except TypeError:
        try:
            return context.res.json(safe_payload)
        except Exception:
            return safe_payload


def _log_analysis_result(context: Any, result: dict[str, Any], media_type: MediaType) -> None:
    """Log a compact result summary without request data or credentials."""
    log = getattr(context, "log", None)
    if not callable(log):
        return

    def _safe_value(value: Any) -> str:
        raw = getattr(value, "value", value)
        return str(raw if raw is not None else "unknown").replace("\r", " ").replace("\n", " ")[:64]

    message = (
        f"analysis_result media_type={_safe_value(result.get('media_type', media_type))} "
        f"verdict={_safe_value(result.get('verdict'))} "
        f"confidence={_safe_value(result.get('confidence'))} "
        f"model={_safe_value(result.get('model_used'))} "
        f"processing_ms={_safe_value(result.get('processing_ms'))}"
    )
    try:
        log(message)
    except Exception:
        pass


def _media_diagnostic_logger(context: Any):
    """Return the runtime's safe one-line diagnostic logging boundary."""
    log = getattr(context, "log", None)
    if not callable(log):
        return None

    def _log(message: str) -> None:
        try:
            log(message)
        except Exception:
            pass

    return _log


def _log_internal_error(context: Any, exc: BaseException) -> None:
    """Emit bounded runtime diagnostics without rendering exception text or request data."""
    log = getattr(context, "log", None)
    if not callable(log):
        return
    if isinstance(exc, ChecksPersistenceError):
        message = (
            f"internal_error operation={exc.operation} exception_class={type(exc).__name__} "
            f"status_code={exc.status_code} appwrite_type={exc.appwrite_type} "
            f"appwrite_code={exc.appwrite_code} appwrite_message={exc.appwrite_message} "
            f"data_keys={exc.data_keys} field_types={exc.field_types} "
            f"string_lengths={exc.string_lengths}"
        )
    else:
        message = f"internal_error operation=unclassified exception_class={type(exc).__name__}"
    try:
        log(message)
    except Exception:
        pass


async def _get_file_metadata(file_id: str, bucket_id: str, user_jwt: str) -> dict[str, Any]:
    """Read Storage metadata with the invoking user's JWT before download."""
    endpoint = os.getenv("APPWRITE_FUNCTION_API_ENDPOINT", "").rstrip("/")
    project_id = os.getenv("APPWRITE_FUNCTION_PROJECT_ID", "")
    if not user_jwt:
        raise SecurityValidationError("authentication_required", "Требуется авторизация.", 401)
    if not endpoint or not project_id:
        raise RuntimeError("Storage configuration is unavailable")
    url = f"{endpoint}/storage/buckets/{quote(bucket_id, safe='')}/files/{quote(file_id, safe='')}"
    headers = {"X-Appwrite-Project": project_id, "X-Appwrite-JWT": user_jwt}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, headers=headers)
    if response.status_code in (401, 403, 404):
        raise SecurityValidationError("file_not_accessible", "Файл недоступен.", 404)
    if response.status_code >= 400:
        raise SecurityValidationError("storage_unavailable", "Хранилище временно недоступно.", 502)
    try:
        metadata = response.json()
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SecurityValidationError("storage_unavailable", "Хранилище временно недоступно.", 502) from exc
    return _validate_storage_metadata(metadata)


async def _download_file_bytes(file_id: str, bucket_id: str, user_jwt: str) -> bytes:
    """Download a user-owned file while enforcing the invoking user's ACL."""
    endpoint = os.getenv("APPWRITE_FUNCTION_API_ENDPOINT", "").rstrip("/")
    project_id = os.getenv("APPWRITE_FUNCTION_PROJECT_ID", "")

    if not user_jwt:
        raise SecurityValidationError("authentication_required", "Требуется авторизация.", 401)
    if not endpoint or not project_id:
        raise RuntimeError("Missing APPWRITE_FUNCTION_API_ENDPOINT/PROJECT_ID")

    url = (
        f"{endpoint}/storage/buckets/{quote(bucket_id, safe='')}/files/"
        f"{quote(file_id, safe='')}/download"
    )
    headers = {
        "X-Appwrite-Project": project_id,
        "X-Appwrite-JWT": user_jwt,
    }

    chunks: list[bytes] = []
    total = 0
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("GET", url, headers=headers) as response:
            if response.status_code in (401, 403, 404):
                raise SecurityValidationError("file_not_accessible", "Файл недоступен.", 404)
            if response.status_code >= 400:
                raise SecurityValidationError("storage_unavailable", "Хранилище временно недоступно.", 502)
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise SecurityValidationError("file_too_large", "Файл превышает лимит в 20 MiB.", 413)
                chunks.append(chunk)
    if total == 0:
        raise SecurityValidationError("invalid_media", "Файл пустой.", 422)
    return b"".join(chunks)


hybrid_analyzer = HybridTextAnalyzer()


async def _analyze(
    request: TextAnalyzeRequest | FileAnalyzeRequest, user_jwt: str, diagnostic_log: Any = None,
    quota_store: AppwriteTablesRateLimitStore | None = None, user_id: str = ""
) -> dict[str, Any]:
    router = MediaRouter()
    started = time.perf_counter()

    async def _with_quota(operation: Any):
        if quota_store is None:
            return await operation()
        reservation = await quota_store.reserve_quota(user_id)
        budget_token = begin_provider_budget(quota_store.guard_provider)
        try:
            result = await operation()
        except ProviderInfrastructureError:
            await quota_store.transition_quota(reservation, "refunded")
            raise
        except Exception:
            # The provider request was admitted and reached a non-technical
            # terminal outcome (for example a provider 4xx).  Do not leave a
            # reservation indefinitely pending, and never refund it here.
            await quota_store.transition_quota(reservation, "consumed")
            raise
        finally:
            end_provider_budget(budget_token)
        await quota_store.transition_quota(reservation, "consumed")
        return result

    if isinstance(request, TextAnalyzeRequest):
        text = request.text
        mode = request.mode or request.analysis_type
        if mode:
            result = await _with_quota(lambda: hybrid_analyzer.analyze(text))
        else:
            result = await _with_quota(lambda: router.route(MediaType.TEXT, b"", text))
    else:
        bucket_id = (
            os.getenv("VITE_APPWRITE_UPLOADS_BUCKET_ID")
            or os.getenv("UPLOADS_BUCKET_ID")
            or "uploads"
        )
        metadata = await _get_file_metadata(request.file_id, bucket_id, user_jwt)
        if diagnostic_log:
            diagnostic_log("media_validation stage=metadata result=ok")
        file_bytes = await _download_file_bytes(request.file_id, bucket_id, user_jwt)
        if diagnostic_log:
            diagnostic_log("media_validation stage=download result=ok")
            diagnostic_log(
                "media_runtime "
                f"ffprobe={'present' if shutil.which('ffprobe') else 'missing'} "
                f"ffmpeg={'present' if shutil.which('ffmpeg') else 'missing'}"
            )
        expected = MediaType(request.media_type) if request.media_type else None
        media_info = await asyncio.to_thread(validate_media_bytes, file_bytes, expected, diagnostic_log)
        if _metadata_media_type(metadata) != media_info.media_type:
            raise SecurityValidationError(
                "media_type_mismatch", "Содержимое файла не соответствует метаданным.", 415
            )
        result = await _with_quota(lambda: router.route(media_info.media_type, file_bytes, ""))

    processing_ms = int((time.perf_counter() - started) * 1000)
    if isinstance(result, dict):
        result["processing_ms"] = processing_ms
        return result

    # Keep legacy Function responses byte-for-byte field-compatible until a
    # provider is migrated to BE-06 canonical semantics.
    body = result.model_dump(mode="json", exclude_none=True)
    body["processing_ms"] = processing_ms
    return body


async def _execute_request(
    payload: dict[str, Any] | ValidatedRequest, api_key: str, user_id: str, user_jwt: str,
    diagnostic_log: Any = None, client_ip: str = "",
) -> dict[str, Any]:
    """Authorize the execution, ensure its profile, and persist trusted results."""
    if not api_key:
        raise RuntimeError("Missing Appwrite Function API key")
    if not user_id or not user_jwt:
        raise SecurityValidationError("authentication_required", "Требуется авторизация.", 401)
    request = validate_request_payload(payload) if isinstance(payload, dict) else payload

    account = await get_authenticated_account(user_id, user_jwt)
    profile = await ensure_user_profile(account, api_key)

    if request.action == "ensure_profile":
        return {"profile_id": str(profile.get("$id") or user_id)}

    if account.get("emailVerification") is not True:
        raise EmailNotVerifiedError("Подтвердите email перед запуском анализа.")

    if not isinstance(request, (TextAnalyzeRequest, FileAnalyzeRequest)):
        raise SecurityValidationError("invalid_request", "Некорректные параметры запроса.")
    rate_store = AppwriteTablesRateLimitStore(api_key)
    await enforce_admission(rate_store, user_id, client_ip)
    result = await _analyze(request, user_jwt, diagnostic_log, rate_store, user_id)
    check_id = await persist_check_result(
        result,
        user_id,
        request.source_label or "",
        api_key,
    )
    result["check_id"] = check_id
    return result


def _run_coro_sync(coro: Any) -> Any:
    """Run coroutine from sync code even when an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result_holder: dict[str, Any] = {}
    error_holder: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result_holder["result"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover
            error_holder["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()

    if "error" in error_holder:
        raise error_holder["error"]
    return result_holder.get("result")


def main(context: Any):
    """Appwrite function handler."""
    try:
        payload = _extract_payload(context.req)
        request = validate_request_payload(payload)
        api_key = _extract_dynamic_api_key(context.req) or os.getenv(
            "APPWRITE_FUNCTION_API_KEY", ""
        )
        user_id = _extract_request_header(context.req, "x-appwrite-user-id")
        user_jwt = _extract_request_header(context.req, "x-appwrite-user-jwt")
        result = _run_coro_sync(
            _execute_request(request, api_key, user_id, user_jwt, _media_diagnostic_logger(context),
                             _extract_request_header(context.req, "x-appwrite-client-ip"))
        )
        if request.action != "ensure_profile":
            try:
                media_type = MediaType(str(result.get("media_type", "text")))
            except (TypeError, ValueError):
                media_type = MediaType.TEXT
            _log_analysis_result(context, result, media_type)
        return _response_json(context, result, 200)
    except EmailNotVerifiedError:
        return _response_json(
            context,
            {
                "detail": "Подтвердите email перед запуском анализа.",
                "code": "email_not_verified",
            },
            403,
        )
    except SecurityValidationError as exc:
        return _response_json(context, {"detail": exc.detail, "code": exc.code}, exc.status_code)
    except RateLimitError as exc:
        payload = {"detail": exc.detail, "code": exc.code}
        if exc.retry_after is not None:
            payload["retry_after"] = exc.retry_after
        return _response_json(context, payload, exc.status_code)
    except ProviderInfrastructureError:
        return _response_json(
            context,
            {
                "detail": "Сервис анализа временно недоступен. Попробуйте позже.",
                "code": "provider_temporarily_unavailable",
            },
            503,
        )
    except Exception as exc:
        _log_internal_error(context, exc)
        return _response_json(
            context,
            {"detail": "Внутренняя ошибка сервиса.", "code": "internal_error"},
            500,
        )
