"""One-request, authenticated Gemini connectivity diagnostic for Appwrite."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from core.config import settings


PROVIDER = "gemini"
PROMPT = "Reply with exactly: YAV_GEMINI_OK"
EXPECTED_RESULT = "YAV_GEMINI_OK"
TIMEOUT = httpx.Timeout(connect=3.0, read=8.0, write=8.0, pool=3.0)
TOTAL_TIMEOUT_SECONDS = 10.0
_MODEL = re.compile(r"[A-Za-z0-9._-]{1,128}")
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_SENSITIVE = re.compile(r"(?i)(?:x-goog-api-key|authorization|api[-_ ]?key|bearer)\s*[:= ]\s*\S+")


def _safe_model() -> str:
    model = settings.gemini_model.strip()
    return model if _MODEL.fullmatch(model) else "invalid-model"


def _safe_base_url() -> str | None:
    value = settings.gemini_api_url.rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    return value


def _safe_message(value: Any) -> str:
    if not isinstance(value, str):
        return "Gemini returned an undocumented error."
    message = " ".join(value.split())
    if not message or _SENSITIVE.search(message):
        return "Gemini returned an undocumented error."
    return message[:300]


def _error_payload(
    model: str,
    message: str,
    *,
    provider_status: int | None = None,
    provider_code: str | None = None,
) -> dict[str, str | int | bool]:
    payload: dict[str, str | int | bool] = {
        "ok": False,
        "provider": PROVIDER,
        "model": model,
        "message": message,
    }
    if provider_status is not None:
        payload["provider_status"] = provider_status
    if provider_code is not None:
        payload["provider_code"] = provider_code
    return payload


def _provider_error(response: httpx.Response, model: str) -> dict[str, str | int | bool]:
    status = response.status_code
    code: str | None = None
    message: Any = None
    try:
        body = response.json()
    except (TypeError, ValueError):
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        status_name = error.get("status")
        raw_code = status_name if isinstance(status_name, str) else error.get("code")
        if isinstance(raw_code, str) and _CODE.fullmatch(raw_code):
            code = raw_code
        message = error.get("message")
    return _error_payload(model, _safe_message(message), provider_status=status, provider_code=code)


def _response_text(response: httpx.Response) -> str | None:
    try:
        body = response.json()
        candidates = body.get("candidates") if isinstance(body, dict) else None
        candidate = candidates[0] if isinstance(candidates, list) and candidates else None
        content = candidate.get("content") if isinstance(candidate, dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        part = parts[0] if isinstance(parts, list) and parts else None
        text = part.get("text") if isinstance(part, dict) else None
    except (AttributeError, TypeError, ValueError):
        return None
    return text.strip() if isinstance(text, str) else None


def _log(diagnostic_log: Callable[[str], None] | None, *, model: str, duration_ms: int, status: int | str, code: str | None = None) -> None:
    if diagnostic_log is None:
        return
    code_value = code if code and _CODE.fullmatch(code) else "none"
    diagnostic_log(
        f"provider=gemini stage=smoke_test model={model} http_status={status} "
        f"provider_code={code_value} duration_ms={duration_ms}"
    )


async def run_gemini_smoke_test(
    diagnostic_log: Callable[[str], None] | None = None,
) -> dict[str, str | int | bool]:
    """Send exactly one minimal request and return curated provider diagnostics."""
    model = _safe_model()
    started = time.perf_counter()
    if not settings.gemini_api_key:
        result = _error_payload(model, "Gemini API key is not configured.", provider_code="MISSING_API_KEY")
        _log(diagnostic_log, model=model, duration_ms=0, status="none", code="MISSING_API_KEY")
        return result
    base_url = _safe_base_url()
    if base_url is None or model == "invalid-model":
        result = _error_payload(model, "Gemini diagnostic configuration is invalid.", provider_code="INVALID_CONFIGURATION")
        _log(diagnostic_log, model=model, duration_ms=0, status="none", code="INVALID_CONFIGURATION")
        return result

    url = f"{base_url}/v1/models/{model}:generateContent"
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.post(
                    url,
                    headers={"x-goog-api-key": settings.gemini_api_key},
                    json={"contents": [{"parts": [{"text": PROMPT}]}]},
                )
    except httpx.ConnectTimeout:
        result = _error_payload(model, "Connection to Gemini timed out.", provider_code="CONNECT_TIMEOUT")
        status, code = "timeout", "CONNECT_TIMEOUT"
    except httpx.ReadTimeout:
        result = _error_payload(model, "Gemini response timed out.", provider_code="READ_TIMEOUT")
        status, code = "timeout", "READ_TIMEOUT"
    except httpx.TimeoutException:
        result = _error_payload(model, "Gemini request timed out.", provider_code="TIMEOUT")
        status, code = "timeout", "TIMEOUT"
    except httpx.TransportError:
        result = _error_payload(model, "Network request to Gemini failed.", provider_code="NETWORK_ERROR")
        status, code = "network", "NETWORK_ERROR"
    except TimeoutError:
        result = _error_payload(model, "Gemini request timed out.", provider_code="TIMEOUT")
        status, code = "timeout", "TIMEOUT"
    else:
        if response.status_code != 200:
            result = _provider_error(response, model)
            status, code = response.status_code, result.get("provider_code")
        else:
            text = _response_text(response)
            if text != EXPECTED_RESULT:
                result = _error_payload(model, "Gemini returned an unexpected response.", provider_status=200, provider_code="INVALID_RESPONSE")
                status, code = 200, "INVALID_RESPONSE"
            else:
                result = {"ok": True, "provider": PROVIDER, "model": model, "result": EXPECTED_RESULT}
                status, code = 200, None
    _log(
        diagnostic_log,
        model=model,
        duration_ms=int((time.perf_counter() - started) * 1000),
        status=status,
        code=code if isinstance(code, str) else None,
    )
    return result
