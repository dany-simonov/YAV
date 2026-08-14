"""Appwrite Function entrypoint for media/text analysis.

This file is used by Appwrite Functions with entrypoint `src/main.py`.
It supports two payload shapes from frontend:
- {"text": "...", "mediaType": "text", ...}
- {"fileId": "...", "mediaType": "image|audio|video", ...}
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
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

from core.enums import MediaType, ModelUsed, Verdict  # noqa: E402
from core.config import settings  # noqa: E402
from core.exceptions import (  # noqa: E402
    ExternalAPIError,
    ProviderInfrastructureError,
)
from core.short_report import build_combined_text_report, build_short_report  # noqa: E402
from adapters.gemini_credibility import GeminiCredibilityAdapter  # noqa: E402
from adapters.gemini_text import GeminiTextAdapter  # noqa: E402
from api.schemas import AnalysisResult, CredibilityAssessment, SourceDetails, SourceMediaResult  # noqa: E402
from router.media_router import MediaRouter  # noqa: E402
from src.appwrite_store import (  # noqa: E402
    ChecksPersistenceError,
    ensure_user_profile,
    get_authenticated_account,
    persist_check_result,
)
from src.media_validation import validate_media_bytes  # noqa: E402
from src.gemini_smoke import run_gemini_list_models, run_gemini_smoke_test  # noqa: E402
from src.rate_limit import (  # noqa: E402
    AppwriteTablesRateLimitStore, RateLimitError, build_admission_plan, build_source_media_admission_plan,
    enforce_admission,  # retained as a test-compatibility import; production uses AdmissionPlan.
)
from src.provider_protection import begin_provider_budget, end_provider_budget  # noqa: E402
from src.execution_deadline import (  # noqa: E402
    ExecutionDeadline,
    ExecutionDeadlineExceeded,
    bounded_timeout,
    current_execution_deadline,
    reset_execution_deadline,
    set_execution_deadline,
)
from src.validation import (  # noqa: E402
    FileAnalyzeRequest,
    ComplexAnalyzeRequest, SourceAnalyzeRequest,
    SecurityValidationError,
    TextAnalyzeRequest,
    ValidatedRequest,
    MAX_FILE_BYTES,
    MAX_TEXT_LENGTH,
    MAX_FILENAME_LENGTH,
    parse_json_object,
    validate_request_payload,
)
from src.source_ingestion import SourceIngestor, validate_source_url  # noqa: E402


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


def _safe_diagnostic_log(diagnostic_log: Any, message: str) -> None:
    """Emit optional observability metadata without affecting request handling."""
    if not callable(diagnostic_log):
        return
    try:
        diagnostic_log(message)
    except Exception:
        pass


def _log_internal_error(context: Any, exc: BaseException, *, operation: str = "unclassified") -> None:
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
        safe_operation = operation if operation in {"complex_url_only", "unclassified"} else "unclassified"
        message = f"internal_error operation={safe_operation} exception_class={type(exc).__name__}"
    try:
        log(message)
    except Exception:
        pass


def _log_provider_external_api_error(context: Any, exc: ExternalAPIError) -> None:
    """Log a stable provider-error classification without exception text.

    ``ExternalAPIError`` deliberately carries only adapter-selected service and
    error-code values.  Keep the logging boundary defensive nevertheless: an
    unexpected value is represented as ``unknown`` instead of being rendered
    into Function logs, where it could otherwise contain provider response or
    request data.
    """
    log = getattr(context, "log", None)
    if not callable(log):
        return

    known_providers = {"aiornot", "sapling", "sightengine", "gemini", "resemble", "huggingface"}
    known_codes = {"request_error", "request_rejected", "auth_configuration", "rate_limit"}
    provider = exc.service if exc.service in known_providers else "unknown"
    error_code = exc.detail if exc.detail in known_codes else "unknown"
    status = getattr(exc, "status_code", None)
    safe_status = status if isinstance(status, int) and 100 <= status <= 599 else "none"
    provider_message = getattr(exc, "provider_message", None)
    allows_safe_provider_message = (
        (provider in {"aiornot", "sightengine"} and error_code == "request_error")
        or (provider == "gemini" and error_code in {"request_error", "request_rejected", "auth_configuration"})
    )
    if not allows_safe_provider_message:
        provider_message = None
    if isinstance(provider_message, str):
        provider_message = provider_message.replace("\r", " ").replace("\n", " ").strip()
        provider_message = re.sub(
            r"(?i)\b(authorization|x-appwrite(?:-[a-z0-9_-]+)?|api[-_ ]?(?:key|secret|user))\s*[:=]\s*"
            r"(?:bearer\s+)?[^\s,;]+",
            r"\1=[REDACTED]",
            provider_message,
        )
        provider_message = re.sub(
            r"(?i)\bbearer\s+[^\s,;]+", "Bearer [REDACTED]", provider_message
        )[:300]
    else:
        provider_message = ""
    category = "request_error"
    if provider == "sapling":
        if safe_status in {401, 403}:
            category = "auth_configuration"
        elif safe_status == 400:
            category = "request_rejected"
    operation = "unknown"
    if provider == "gemini":
        candidate = getattr(exc, "operation", None)
        if candidate in {"files_start", "upload_finalize", "files_poll", "generate_content"}:
            operation = candidate
    google_status = getattr(exc, "upstream_status", None)
    google_status = google_status if isinstance(google_status, str) and re.fullmatch(r"[A-Z_]{1,64}", google_status) else "none"
    google_code = getattr(exc, "upstream_code", None)
    google_code = google_code if isinstance(google_code, int) and 100 <= google_code <= 599 else "none"
    message = (
        "provider_external_api_error operation=provider.external_api_error "
        f"provider={provider} safe_error_code={error_code} "
        f"stage=request category={category} gemini_operation={operation} google_status={google_status} google_code={google_code} status_code={safe_status} "
        f"exception_class={type(exc).__name__}"
    )
    if provider == "aiornot" and error_code == "request_error":
        content_type = getattr(exc, "content_type", None)
        if not isinstance(content_type, str) or not re.fullmatch(
            r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+|unknown", content_type
        ):
            content_type = "unknown"
        response_length = getattr(exc, "response_length", None)
        if not isinstance(response_length, int) or response_length < 0:
            response_length = "unknown"
        message += f" content_type={content_type} response_length={response_length}"
        for field_name in ("response_keys", "response_paths"):
            values = getattr(exc, field_name, ())
            if isinstance(values, tuple) and values and all(
                isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,80}", value)
                for value in values
            ):
                message += f" {field_name}={','.join(values)}"
    if provider_message:
        message += f" provider_message={provider_message}"
    try:
        log(message)
    except Exception:
        pass


def _log_provider_infrastructure_error(context: Any, exc: ProviderInfrastructureError) -> None:
    """Log a bounded failure classification without provider or user data."""
    log = getattr(context, "log", None)
    if not callable(log):
        return

    known_providers = {"aiornot", "sapling", "sightengine", "gemini", "resemble", "huggingface"}
    known_kinds = {
        "capacity", "config", "invalid_configuration", "invalid_response", "missing_credentials", "model_loading", "processing_timeout",
        "rate_limited", "timeout", "transport", "unavailable",
    }
    known_stages = {"admission", "config", "request", "response"}
    known_reasons = {"api_key_missing"}
    provider = exc.service if exc.service in known_providers else "unknown"
    kind = exc.kind if exc.kind in known_kinds else "unknown"
    stage = exc.stage if exc.stage in known_stages else (
        "admission" if kind == "capacity" else "unknown"
    )
    reason = exc.reason if exc.reason in known_reasons else "none"
    status = exc.status_code
    safe_status = status if isinstance(status, int) and 100 <= status <= 599 else "none"
    message = (
        "provider_infrastructure_error operation=provider.infrastructure_error "
        f"provider={provider} stage={stage} category={kind} reason={reason} "
        f"status_code={safe_status} exception_class={type(exc).__name__}"
    )
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
    async with httpx.AsyncClient(timeout=bounded_timeout(15.0)) as client:
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
    async with httpx.AsyncClient(timeout=bounded_timeout(60.0)) as client:
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


def _branch_diagnostic(diagnostic_log: Any, branch: str):
    """Attach an allowlisted branch label without exposing request data."""
    def _log(message: str) -> None:
        _safe_diagnostic_log(diagnostic_log, f"branch={branch} {message}")
    return _log


def _unavailable_credibility() -> CredibilityAssessment:
    return CredibilityAssessment(
        status="unavailable",
        summary="Проверка достоверности временно недоступна.",
    )


def _unavailable_ai_result(*, complex_mode: bool = False) -> AnalysisResult:
    """Represent an independently unavailable AI-origin branch without inventing a score."""
    return AnalysisResult(
        verdict=Verdict.UNCERTAIN,
        confidence=0.5,
        model_used=ModelUsed.FALLBACK_UNCERTAIN,
        explanation="Проверка признаков AI-генерации временно недоступна.",
        media_type=MediaType.TEXT,
        semantics_version=2,
        ai_status="unavailable",
        analysis_mode="complex" if complex_mode else None,
    )


async def _analyze_combined_normal_text(
    router: MediaRouter, text: str, diagnostic_log: Any,
) -> AnalysisResult:
    """Run independent normal-text branches concurrently and join their safe output."""
    text_bytes = text.encode("utf-8")
    ai_started = time.monotonic()
    _safe_diagnostic_log(diagnostic_log, "branch=ai_origin stage=branch_start")
    ai_diagnostic = _branch_diagnostic(diagnostic_log, "ai_origin") if diagnostic_log is not None else None
    ai_task = router.route(MediaType.TEXT, b"", text, diagnostic_log=ai_diagnostic)

    async def _run_credibility_branch() -> CredibilityAssessment:
        credibility_started = time.monotonic()
        credibility = await GeminiCredibilityAdapter().analyze(text_bytes, diagnostic_log=diagnostic_log)
        # This is deliberately only the credibility coroutine's elapsed time:
        # it excludes waiting for the parallel AI-origin branch and persistence.
        elapsed_ms = max(0, min(60_000, round((time.monotonic() - credibility_started) * 1000)))
        return credibility.model_copy(update={"processing_ms": elapsed_ms})

    credibility_task = _run_credibility_branch()
    ai_outcome, credibility_outcome = await asyncio.gather(
        ai_task, credibility_task, return_exceptions=True,
    )
    _safe_diagnostic_log(
        diagnostic_log,
        "branch=ai_origin stage=branch_" + ("error" if isinstance(ai_outcome, BaseException) else "success")
        + f" elapsed_ms={round((time.monotonic() - ai_started) * 1000)}",
    )
    _safe_diagnostic_log(
        diagnostic_log,
        "branch=credibility stage=branch_"
        + ("error" if isinstance(credibility_outcome, BaseException) else "success")
        + (f" elapsed_ms={credibility_outcome.processing_ms}" if not isinstance(credibility_outcome, BaseException) else ""),
    )

    def _branch_error(value: Any) -> bool:
        return isinstance(value, (ProviderInfrastructureError, ExternalAPIError))

    if isinstance(ai_outcome, BaseException) and not _branch_error(ai_outcome):
        raise ai_outcome
    if isinstance(credibility_outcome, BaseException) and not _branch_error(credibility_outcome):
        raise credibility_outcome
    if isinstance(ai_outcome, BaseException) and isinstance(credibility_outcome, BaseException):
        # Preserve an existing controlled provider classification when neither
        # independent branch could provide a user-facing report.
        raise ai_outcome

    ai_result = _unavailable_ai_result() if isinstance(ai_outcome, BaseException) else ai_outcome
    credibility = (
        _unavailable_credibility()
        if isinstance(credibility_outcome, BaseException)
        else credibility_outcome
    )
    return ai_result.model_copy(update={
        "credibility": credibility,
        "short_report": build_combined_text_report(ai_result, credibility),
    })


async def _analyze_complex_text(text: str, diagnostic_log: Any) -> AnalysisResult:
    """Run the two independent expanded Gemini branches, with no legacy Hybrid providers."""
    text_bytes = text.encode("utf-8")
    ai_started = time.monotonic()
    ai_diagnostic = _branch_diagnostic(diagnostic_log, "ai_origin") if diagnostic_log is not None else None

    async def _run_ai_branch() -> AnalysisResult:
        return await GeminiTextAdapter().analyze(text_bytes, diagnostic_log=ai_diagnostic, complex_mode=True)

    async def _run_credibility_branch() -> CredibilityAssessment:
        started = time.monotonic()
        assessment = await GeminiCredibilityAdapter().analyze(
            text_bytes, diagnostic_log=diagnostic_log, complex_mode=True,
        )
        return assessment.model_copy(update={
            "processing_ms": max(0, min(60_000, round((time.monotonic() - started) * 1000))),
        })

    ai_outcome, credibility_outcome = await asyncio.gather(
        _run_ai_branch(), _run_credibility_branch(), return_exceptions=True,
    )
    _safe_diagnostic_log(
        diagnostic_log, "branch=ai_origin stage=branch_"
        + ("error" if isinstance(ai_outcome, BaseException) else "success")
        + f" elapsed_ms={round((time.monotonic() - ai_started) * 1000)}",
    )
    _safe_diagnostic_log(
        diagnostic_log, "branch=credibility stage=branch_"
        + ("error" if isinstance(credibility_outcome, BaseException) else "success"),
    )
    provider_errors = (ProviderInfrastructureError, ExternalAPIError)
    if isinstance(ai_outcome, BaseException) and not isinstance(ai_outcome, provider_errors):
        raise ai_outcome
    if isinstance(credibility_outcome, BaseException) and not isinstance(credibility_outcome, provider_errors):
        raise credibility_outcome
    if isinstance(ai_outcome, BaseException) and isinstance(credibility_outcome, BaseException):
        raise ai_outcome
    ai_result = _unavailable_ai_result(complex_mode=True) if isinstance(ai_outcome, BaseException) else ai_outcome
    credibility = _unavailable_credibility() if isinstance(credibility_outcome, BaseException) else credibility_outcome
    return ai_result.model_copy(update={
        "analysis_mode": "complex",
        "credibility": credibility,
        "short_report": build_combined_text_report(ai_result, credibility),
    })


async def _analyze_complex_source(
    source_url: str, diagnostic_log: Any, *, quota_store: AppwriteTablesRateLimitStore | None = None,
    user_id: str = "", client_ip: str = "", account_created_at: Any = None, additional_text: str = "",
) -> AnalysisResult:
    """Ingest one public source and deterministically combine existing analyzers."""
    ingestor = SourceIngestor()
    _safe_diagnostic_log(diagnostic_log, "complex_stage=source_start")
    document = await ingestor.ingest(source_url)
    _safe_diagnostic_log(
        diagnostic_log,
        "complex_stage=source_ingested "
        f"text_present={'yes' if bool(document.text.strip()) else 'no'} "
        f"images_present={'yes' if bool(document.image_urls) else 'no'} "
        f"video_present={'yes' if bool(document.video_urls) else 'no'}",
    )
    router = MediaRouter()

    def _child_timeout() -> float | None:
        deadline = current_execution_deadline()
        if deadline is None:
            return None
        # ``analysis_deadline`` already reserves persistence and response time.
        # Retain a final aggregation/cancellation window inside that budget.
        return max(0.01, deadline.remaining_analysis_time() - 0.5)

    async def _branch_outcome(operation: Any, *, timeout_seconds: float | None = None) -> Any:
        """Finish source branches before the shared analysis deadline expires."""
        deadline = current_execution_deadline()
        if deadline is None:
            return await operation
        # Keep a small window for cancellation/normalization so completed
        # sibling branches can still become a partial response.
        try:
            timeout = timeout_seconds if timeout_seconds is not None else _child_timeout()
            assert timeout is not None
        except ExecutionDeadlineExceeded:
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            return None
        try:
            async with asyncio.timeout(timeout):
                return await operation
        except TimeoutError:
            return None

    async def _media_result(kind: MediaType, ordinal: int, url: str) -> SourceMediaResult:
        try:
            child_timeout = _child_timeout()
            acquired = await _branch_outcome(
                ingestor.download_media(url, timeout_seconds=child_timeout), timeout_seconds=child_timeout,
            )
            if not isinstance(acquired, tuple):
                return SourceMediaResult(kind=kind.value, ordinal=ordinal, status="unavailable", model=("gemini_video_verification" if kind == MediaType.VIDEO else "sightengine"))
            data, mime_type = acquired
            info = validate_media_bytes(data)
            if info.media_type != kind:
                return SourceMediaResult(kind=kind.value, ordinal=ordinal, status="unavailable", model="unsupported_media")
            provider_timeout = _child_timeout()
            result = await _branch_outcome(router.route(info.media_type, data, mime_type=mime_type, diagnostic_log=diagnostic_log), timeout_seconds=provider_timeout)
            if not isinstance(result, AnalysisResult):
                return SourceMediaResult(kind=kind.value, ordinal=ordinal, status="unavailable", model=("gemini_video_verification" if kind == MediaType.VIDEO else "sightengine"))
            return SourceMediaResult(kind=kind.value, ordinal=ordinal, status="completed", authenticity_index=result.authenticity_index,
                verdict=result.verdict, confidence=result.confidence, model=result.model_used.value, explanation=result.explanation,
                processing_ms=max(0, min(60_000, result.processing_ms)))
        except asyncio.CancelledError:
            raise
        except (SecurityValidationError, ExternalAPIError, ProviderInfrastructureError, ExecutionDeadlineExceeded):
            return SourceMediaResult(kind=kind.value, ordinal=ordinal, status="unavailable", model=("gemini_video_verification" if kind == MediaType.VIDEO else "sightengine"))

    has_images, has_video = bool(document.image_urls), bool(document.video_urls)
    if quota_store is not None and account_created_at is not None and (has_images or has_video):
        plan = build_source_media_admission_plan(quota_store, user_id=user_id, client_ip=client_ip,
            account_created_at=account_created_at, has_image=has_images, has_video=has_video)
        deadline = current_execution_deadline()
        if deadline is None: await quota_store.admit(plan)
        else: await deadline.run(quota_store.admit(plan))
    media_task = asyncio.gather(*(
        [_media_result(MediaType.IMAGE, index + 1, url) for index, url in enumerate(document.image_urls)]
        + [_media_result(MediaType.VIDEO, 1, url) for url in document.video_urls]
    ), return_exceptions=True)
    source_text, manual_text = document.text.strip(), additional_text.strip()
    if source_text and manual_text:
        # Preserve provenance and a bounded part of each input instead of
        # silently dropping the later manual text behind a long article.
        half = (MAX_TEXT_LENGTH - 80) // 2
        combined_text = f"[Текст публикации]\n{source_text[:half]}\n\n[Дополнительный текст пользователя]\n{manual_text[:half]}"
    elif source_text:
        combined_text = "[Текст публикации]\n" + source_text[:MAX_TEXT_LENGTH]
    elif manual_text:
        combined_text = "[Дополнительный текст пользователя]\n" + manual_text[:MAX_TEXT_LENGTH]
    else:
        combined_text = ""
    _safe_diagnostic_log(
        diagnostic_log,
        "complex_text_corpus "
        f"manual_text_present={'yes' if manual_text else 'no'} "
        f"source_text_present={'yes' if source_text else 'no'} "
        f"combined_corpus_length={len(combined_text)} "
        f"combined_corpus_empty={'yes' if not combined_text else 'no'} "
        "combined_corpus_type=str "
        f"truncated={'yes' if len(source_text) > MAX_TEXT_LENGTH or len(manual_text) > MAX_TEXT_LENGTH else 'no'}",
    )
    has_text = len(combined_text) >= 200
    text_task = _analyze_complex_text(combined_text, diagnostic_log) if has_text else None
    if text_task is None:
        media_outcomes = await media_task
        text_result = None
    else:
        # Media's child timeout belongs exclusively to media acquisition.  A
        # completed text branch must not be downgraded when a sibling media
        # download exhausts its own child budget; the outer execution deadline
        # remains the root cancellation boundary for the whole request.
        text_outcome, media_outcomes = await asyncio.gather(text_task, media_task, return_exceptions=True)
        text_result = text_outcome if isinstance(text_outcome, AnalysisResult) else None
    media_results = [item for item in media_outcomes if isinstance(item, SourceMediaResult)]
    completed_media = [item for item in media_results if item.status == "completed"]
    if text_result is None and not completed_media:
        raise SecurityValidationError("source_unavailable", "Источник не содержит пригодного текста или медиа.", 422)

    source = SourceDetails(
        url=document.url, title=document.title[:300], description=document.description[:600],
        site_name=document.site_name[:160], text_found=has_text, text_truncated=document.text_truncated,
        images_discovered=len(document.image_urls), video_discovered=has_video,
        images_analyzed=sum(1 for item in completed_media if item.kind == "image"),
        video_analyzed=any(item.kind == "video" for item in completed_media), media=media_results,
    )
    if text_result is not None:
        return text_result.model_copy(update={"source": source})
    # A source with no usable text still exposes a real media result; no score
    # is fabricated or blended with unavailable text branches.
    primary = next(item for item in completed_media if item.authenticity_index is not None)
    return AnalysisResult(verdict=primary.verdict, confidence=primary.confidence or 0.5,
        model_used=next((model for model in ModelUsed if model.value == primary.model), ModelUsed.SIGHTENGINE),
        explanation=primary.explanation or "Доступен частичный результат медиаанализа.", media_type=MediaType.TEXT,
        authenticity_index=primary.authenticity_index, analysis_mode="complex", source=source,
        short_report="Текст источника недостаточен для расширенного анализа; показаны результаты доступного медиа.")


async def _analyze(
    request: TextAnalyzeRequest | FileAnalyzeRequest | SourceAnalyzeRequest | ComplexAnalyzeRequest, user_jwt: str, diagnostic_log: Any = None,
    quota_store: AppwriteTablesRateLimitStore | None = None, user_id: str = "",
    account_created_at: Any = None, client_ip: str = "",
) -> dict[str, Any]:
    router = MediaRouter()
    started = time.perf_counter()

    async def _with_quota(operation: Any, admission_plan: Any = None):
        if quota_store is None:
            return await operation()
        if admission_plan is not None:
            deadline = current_execution_deadline()

            async def _within_deadline(awaitable: Any) -> Any:
                return await deadline.run(awaitable) if deadline is not None else await awaitable

            # Admission is deliberately final: a provider 429, timeout or an
            # invalid response never refunds any of the committed counters.
            await _within_deadline(quota_store.admit(admission_plan))
            prepaid = {provider: admission_plan.units_for(provider) for provider, _ in admission_plan.provider_units}

            async def _admit_unplanned_provider(provider: str, units: int) -> None:
                await _within_deadline(quota_store.admit_provider_units(provider, units))

            budget_token = begin_provider_budget(_admit_unplanned_provider, prepaid)
            try:
                operation_result = operation()
                return await _within_deadline(operation_result)
            finally:
                end_provider_budget(budget_token)
        deadline = current_execution_deadline()

        async def _within_deadline(awaitable: Any) -> Any:
            return await deadline.run(awaitable) if deadline is not None else await awaitable

        async def _within_persistence_deadline(awaitable: Any) -> Any:
            return await deadline.run_persistence(awaitable) if deadline is not None else await awaitable

        reservation = await _within_deadline(quota_store.reserve_quota(user_id))
        reservations_finalized = False
        # Compatibility path for direct unit tests of the retired reservation
        # lifecycle.  Production requests always pass ``admission_plan``.
        budget_token = begin_provider_budget(quota_store.guard_provider)

        async def _finalize(target: str) -> None:
            """Terminally settle both reservations exactly once."""
            nonlocal reservations_finalized
            if reservations_finalized:
                return
            await _within_persistence_deadline(quota_store.transition_quota(reservation, target))
            reservations_finalized = True

        async def _run_operation():
            deadline = current_execution_deadline()
            operation_result = operation()
            return await deadline.run(operation_result) if deadline is not None else await operation_result

        try:
            result = await _run_operation()
        except (ProviderInfrastructureError, ExternalAPIError) as exc:
            if isinstance(exc, ProviderInfrastructureError):
                await _finalize("refunded")
            else:
                await _finalize("consumed")
            raise
        except ExecutionDeadlineExceeded:
            await _finalize("refunded")
            raise
        except RateLimitError:
            await _finalize("refunded")
            raise
        except Exception:
            # The provider request was admitted and reached a non-technical
            # terminal outcome (for example a provider 4xx).  Do not leave a
            # reservation indefinitely pending, and never refund it here.
            await _finalize("consumed")
            raise
        finally:
            end_provider_budget(budget_token)
        await _finalize("consumed")
        return result

    if isinstance(request, ComplexAnalyzeRequest):
        # The unified request keeps the old source-only path intact while
        # allowing trusted Storage files and manual text to run alongside it.
        source_result = None
        if request.source_url:
            source_result = await _analyze_complex_source(request.source_url, diagnostic_log, quota_store=quota_store,
                user_id=user_id, client_ip=client_ip, account_created_at=account_created_at, additional_text=request.text or "")
        text_result = None
        if request.text and not request.source_url:
            _safe_diagnostic_log(
                diagnostic_log,
                "complex_text_corpus manual_text_present=yes source_text_present=no "
                f"combined_corpus_length={len(request.text)} combined_corpus_empty=no "
                "combined_corpus_type=str truncated=no",
            )
            text_result = await _analyze_complex_text(request.text, diagnostic_log)
        manual_results: list[AnalysisResult] = []
        manual_media: list[SourceMediaResult] = []
        bucket_id = os.getenv("VITE_APPWRITE_UPLOADS_BUCKET_ID") or os.getenv("UPLOADS_BUCKET_ID") or "uploads"
        for file_id in request.file_ids:
            metadata = await _get_file_metadata(file_id, bucket_id, user_jwt)
            file_bytes = await _download_file_bytes(file_id, bucket_id, user_jwt)
            info = validate_media_bytes(file_bytes)
            if _metadata_media_type(metadata) != info.media_type:
                raise SecurityValidationError("media_type_mismatch", "Содержимое файла не соответствует метаданным.", 415)
            try:
                item = await router.route(info.media_type, file_bytes, mime_type=metadata["mimeType"].split(";", 1)[0].lower())
                manual_results.append(item)
                manual_media.append(SourceMediaResult(kind=info.media_type.value, origin="manual", ordinal=len(manual_media) + 1, status="completed",
                    authenticity_index=item.authenticity_index, verdict=item.verdict, confidence=item.confidence,
                    model=item.model_used.value, explanation=item.explanation, processing_ms=item.processing_ms))
            except (ExternalAPIError, ProviderInfrastructureError, ExecutionDeadlineExceeded):
                manual_media.append(SourceMediaResult(kind=info.media_type.value, origin="manual", ordinal=len(manual_media) + 1, status="unavailable",
                    model="gemini_video_verification" if info.media_type == MediaType.VIDEO else info.media_type.value))
        result = source_result or text_result or (manual_results[0] if manual_results else None)
        if result is None:
            raise SecurityValidationError("source_unavailable", "Нет пригодного материала для анализа.", 422)
        if manual_media:
            result = result.model_copy(update={"analysis_mode": "complex", "complex_media": manual_media})
    elif isinstance(request, SourceAnalyzeRequest):
        source_url = await validate_source_url(request.source_url)
        admission_plan = (
            build_admission_plan(
                quota_store, user_id=user_id, client_ip=client_ip, account_created_at=account_created_at,
                media_type=MediaType.TEXT.value, input_size=len(source_url), text="", hybrid=True,
            ) if quota_store is not None and account_created_at is not None else None
        )
        result = await _with_quota(lambda: _analyze_complex_source(source_url, diagnostic_log, quota_store=quota_store,
            user_id=user_id, client_ip=client_ip, account_created_at=account_created_at), admission_plan)
    elif isinstance(request, TextAnalyzeRequest):
        text = request.text
        mode = request.mode or request.analysis_type
        admission_plan = (
            build_admission_plan(
                quota_store, user_id=user_id, client_ip=client_ip, account_created_at=account_created_at,
                media_type=MediaType.TEXT.value, input_size=len(text), text=text, hybrid=bool(mode),
            ) if quota_store is not None and account_created_at is not None else None
        )
        if mode:
            result = await _with_quota(lambda: _analyze_complex_text(text, diagnostic_log), admission_plan)
        else:
            result = await _with_quota(lambda: _analyze_combined_normal_text(router, text, diagnostic_log), admission_plan)
    else:
        bucket_id = (
            os.getenv("VITE_APPWRITE_UPLOADS_BUCKET_ID")
            or os.getenv("UPLOADS_BUCKET_ID")
            or "uploads"
        )
        deadline = current_execution_deadline()
        metadata_operation = _get_file_metadata(request.file_id, bucket_id, user_jwt)
        metadata = await deadline.run(metadata_operation) if deadline is not None else await metadata_operation
        if diagnostic_log:
            diagnostic_log("media_validation stage=metadata result=ok")
        download_operation = _download_file_bytes(request.file_id, bucket_id, user_jwt)
        file_bytes = await deadline.run(download_operation) if deadline is not None else await download_operation
        if diagnostic_log:
            diagnostic_log("media_validation stage=download result=ok")
            diagnostic_log(
                "media_runtime "
                f"ffprobe={'present' if shutil.which('ffprobe') else 'missing'} "
                f"ffmpeg={'present' if shutil.which('ffmpeg') else 'missing'}"
            )
        expected = MediaType(request.media_type) if request.media_type else None
        validation_operation = asyncio.to_thread(validate_media_bytes, file_bytes, expected, diagnostic_log)
        media_info = await deadline.run(validation_operation) if deadline is not None else await validation_operation
        if _metadata_media_type(metadata) != media_info.media_type:
            raise SecurityValidationError(
                "media_type_mismatch", "Содержимое файла не соответствует метаданным.", 415
            )
        admission_plan = (
            build_admission_plan(
                quota_store, user_id=user_id, client_ip=client_ip, account_created_at=account_created_at,
                media_type=media_info.media_type.value, input_size=len(file_bytes),
            ) if quota_store is not None and account_created_at is not None else None
        )
        result = await _with_quota(
            lambda: router.route(
                media_info.media_type,
                file_bytes,
                "",
                mime_type=metadata["mimeType"].split(";", 1)[0].strip().lower(),
            ), admission_plan,
        )

    processing_ms = int((time.perf_counter() - started) * 1000)
    if isinstance(result, dict):
        result["processing_ms"] = processing_ms
        return result

    # Build the user-facing summary only after routing and normalization have
    # produced the canonical result. Hybrid text has its own response contract.
    if result.credibility is None:
        result = result.model_copy(update={"short_report": build_short_report(result)})

    # Preserve existing Function fields; short_report is an additive field.
    body = result.model_dump(mode="json", exclude_none=True)
    body["processing_ms"] = processing_ms
    return body


async def _execute_request(
    payload: dict[str, Any] | ValidatedRequest, api_key: str, user_id: str, user_jwt: str,
    diagnostic_log: Any = None, client_ip: str = "", *,
    execution_deadline: ExecutionDeadline | None = None,
    request_started_at: float | None = None,
    diagnostic_authorization: str = "",
) -> dict[str, Any]:
    """Authorize the execution, ensure its profile, and persist trusted results."""
    if not user_id or not user_jwt:
        raise SecurityValidationError("authentication_required", "Требуется авторизация.", 401)
    request = validate_request_payload(payload) if isinstance(payload, dict) else payload
    is_diagnostic = request.action in {"gemini_smoke_test", "gemini_list_models"}
    if is_diagnostic:
        _safe_diagnostic_log(diagnostic_log, f"diagnostic={request.action} stage=start")
    # Diagnostics authenticate through the invoking user's JWT and do not use
    # the Function API key.  Keep analysis and profile operations fail-closed.
    if not api_key and not is_diagnostic:
        raise RuntimeError("Missing Appwrite Function API key")
    is_analyze = isinstance(request, (TextAnalyzeRequest, FileAnalyzeRequest, SourceAnalyzeRequest, ComplexAnalyzeRequest))
    if execution_deadline is None and request_started_at is not None and is_analyze:
        try:
            execution_deadline = ExecutionDeadline.from_execution_timeout(
                settings.synchronous_analyze_execution_timeout_seconds,
                settings.synchronous_analyze_safety_margin_seconds,
                settings.synchronous_analyze_response_safety_margin_seconds,
                request_start=request_started_at,
            )
        except ValueError as exc:
            raise RuntimeError("Synchronous analysis deadline is not configured") from exc

    async def _within_deadline(awaitable: Any) -> Any:
        return await execution_deadline.run(awaitable) if execution_deadline is not None else await awaitable

    deadline_token = set_execution_deadline(execution_deadline) if execution_deadline is not None else None
    try:
        try:
            account = await _within_deadline(get_authenticated_account(user_id, user_jwt))
        except RuntimeError:
            if is_diagnostic:
                _safe_diagnostic_log(diagnostic_log, f"diagnostic={request.action} stage=auth_error category=unavailable")
                return {
                    "ok": False, "provider": "appwrite", "operation": request.action,
                    "provider_code": "AUTHENTICATION_UNAVAILABLE",
                }
            raise

        if request.action == "ensure_profile":
            profile = await _within_deadline(ensure_user_profile(account, api_key))
            return {"profile_id": str(profile.get("$id") or user_id)}

        if account.get("emailVerification") is not True:
            raise EmailNotVerifiedError("Подтвердите email перед запуском анализа.")

        if request.action in {"gemini_smoke_test", "gemini_list_models"}:
            configured_secret = settings.gemini_smoke_diagnostic_secret
            if (
                not settings.gemini_smoke_enabled
                or not configured_secret
                or not hmac.compare_digest(diagnostic_authorization, configured_secret)
            ):
                raise SecurityValidationError("diagnostic_access_denied", "Доступ к диагностике запрещён.", 403)
            if request.action == "gemini_list_models":
                return await run_gemini_list_models(diagnostic_log)
            return await run_gemini_smoke_test(diagnostic_log)

        profile = await _within_deadline(ensure_user_profile(account, api_key))

        if not is_analyze:
            raise SecurityValidationError("invalid_request", "Некорректные параметры запроса.")
        rate_store = AppwriteTablesRateLimitStore(api_key)
        result = await _analyze(
            request, user_jwt, diagnostic_log, rate_store, user_id,
            account_created_at=account.get("$createdAt"), client_ip=client_ip,
        )
        is_gemini_text = result.get("model_used") == "gemini_text_verification"
        if isinstance(request, (SourceAnalyzeRequest, ComplexAnalyzeRequest)):
            # Source metadata is optional for unified Complex.  In particular,
            # text-only Complex has no source and must persist normally.
            source = result.get("source")
            source_label = source.get("url", "") if isinstance(source, dict) else ""
        else:
            source_label = request.source_label or ""
        persistence_started = time.monotonic()
        if is_gemini_text:
            _safe_diagnostic_log(diagnostic_log, "provider=gemini_text stage=persistence_start")
        try:
            check_id = await (
                execution_deadline.run_persistence(
                    persist_check_result(
                        result,
                        user_id,
                        source_label,
                        api_key,
                    )
                )
                if execution_deadline is not None
                else persist_check_result(result, user_id, source_label, api_key)
            )
        except Exception:
            if is_gemini_text:
                _safe_diagnostic_log(
                    diagnostic_log,
                    "provider=gemini_text stage=persistence_error "
                    f"elapsed_ms={round((time.monotonic() - persistence_started) * 1000)}"
                )
            raise
        if is_gemini_text:
            _safe_diagnostic_log(
                diagnostic_log,
                "provider=gemini_text stage=persistence_success "
                f"elapsed_ms={round((time.monotonic() - persistence_started) * 1000)}"
            )
        result["check_id"] = check_id
        if execution_deadline is not None:
            execution_deadline.remaining_root_time()
        return result
    finally:
        if deadline_token is not None:
            reset_execution_deadline(deadline_token)


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
    request: ValidatedRequest | None = None
    try:
        request_start = time.monotonic()
        payload = _extract_payload(context.req)
        request = validate_request_payload(payload)
        if isinstance(request, ComplexAnalyzeRequest):
            _safe_diagnostic_log(
                _media_diagnostic_logger(context),
                "complex_stage=request_validated "
                f"source_present={'yes' if bool(request.source_url) else 'no'} "
                f"manual_text_present={'yes' if bool(request.text) else 'no'} "
                f"manual_file_count={len(request.file_ids)}",
            )
        execution_deadline = None
        if request.action == "analyze":
            try:
                execution_deadline = ExecutionDeadline.from_execution_timeout(
                    settings.synchronous_analyze_execution_timeout_seconds,
                    settings.synchronous_analyze_safety_margin_seconds,
                    settings.synchronous_analyze_response_safety_margin_seconds,
                    request_start=request_start,
                )
            except ValueError:
                # _execute_request owns fail-closed configuration validation;
                # leave its existing boundary intact when no outer deadline can
                # be constructed here.
                pass
        api_key = _extract_dynamic_api_key(context.req) or os.getenv(
            "APPWRITE_FUNCTION_API_KEY", ""
        )
        user_id = _extract_request_header(context.req, "x-appwrite-user-id")
        user_jwt = _extract_request_header(context.req, "x-appwrite-user-jwt")
        result = _run_coro_sync(
            _execute_request(request, api_key, user_id, user_jwt, _media_diagnostic_logger(context),
                             _extract_request_header(context.req, "x-appwrite-client-ip"),
                             execution_deadline=execution_deadline,
                             request_started_at=request_start,
                             diagnostic_authorization=_extract_request_header(
                                 context.req, "x-yav-diagnostic-authorization"
                             ))
        )
        def _build_success_response():
            if request.action == "analyze":
                try:
                    media_type = MediaType(str(result.get("media_type", "text")))
                except (TypeError, ValueError):
                    media_type = MediaType.TEXT
                _log_analysis_result(context, result, media_type)
            return _response_json(context, result, 200)

        if execution_deadline is not None:
            return execution_deadline.run_final_stage(_build_success_response)
        return _build_success_response()
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
    except ExecutionDeadlineExceeded:
        return _response_json(
            context,
            {
                "detail": "Сервис анализа временно недоступен. Попробуйте позже.",
                "code": "provider_temporarily_unavailable",
            },
            503,
        )
    except ProviderInfrastructureError as exc:
        _log_provider_infrastructure_error(context, exc)
        return _response_json(
            context,
            {
                "detail": "Сервис анализа временно недоступен. Попробуйте позже.",
                "code": "provider_temporarily_unavailable",
            },
            503,
        )
    except ExternalAPIError as exc:
        _log_provider_external_api_error(context, exc)
        return _response_json(
            context,
            {
                "detail": "Сервис анализа временно недоступен.",
                "code": "provider_unavailable",
            },
            503,
        )
    except Exception as exc:
        is_url_only_complex = (
            isinstance(request, ComplexAnalyzeRequest)
            and request.source_url is not None
            and request.text is None
            and not request.file_ids
        )
        _log_internal_error(
            context,
            exc,
            operation="complex_url_only" if is_url_only_complex else "unclassified",
        )
        return _response_json(
            context,
            {"detail": "Внутренняя ошибка сервиса.", "code": "internal_error"},
            500,
        )
