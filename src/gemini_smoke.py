"""One-request, authenticated Gemini connectivity diagnostic for Appwrite."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Callable

import httpx

from core.config import settings
from src.gemini_client import gemini_headers, safe_gemini_base_url, safe_gemini_model


PROVIDER = "gemini"
PROMPT = "Reply with exactly: YAV_GEMINI_OK"
EXPECTED_RESULT = "YAV_GEMINI_OK"
TIMEOUT = httpx.Timeout(connect=3.0, read=8.0, write=8.0, pool=3.0)
TOTAL_TIMEOUT_SECONDS = 10.0
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_SENSITIVE = re.compile(r"(?i)(?:x-goog-api-key|authorization|api[-_ ]?key|bearer)\s*[:= ]\s*\S+")
_MODEL_FIELD = re.compile(r"[A-Za-z0-9 ._/-]{1,160}")
_METHOD = re.compile(r"[A-Za-z][A-Za-z0-9]{0,63}")
_TARGET_MODELS = (
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
)


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


def _safe_model_field(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if _MODEL_FIELD.fullmatch(value) else None


def _safe_generation_methods(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value[:32] if isinstance(item, str) and _METHOD.fullmatch(item)]


def _safe_flash_models(body: Any) -> list[dict[str, str | list[str]]] | None:
    """Return only curated model metadata needed for a generateContent probe."""
    models = body.get("models") if isinstance(body, dict) else None
    if not isinstance(models, list):
        return None
    result: list[dict[str, str | list[str]]] = []
    for item in models[:1000]:
        if not isinstance(item, dict):
            continue
        name = _safe_model_field(item.get("name"))
        base_model_id = _safe_model_field(item.get("baseModelId"))
        searchable = " ".join(value.casefold() for value in (name, base_model_id) if value)
        if "gemini" not in searchable or "flash" not in searchable:
            continue
        if name is None:
            continue
        model: dict[str, str | list[str]] = {
            "name": name,
            "supportedGenerationMethods": _safe_generation_methods(item.get("supportedGenerationMethods")),
        }
        if base_model_id is not None:
            model["baseModelId"] = base_model_id
        display_name = _safe_model_field(item.get("displayName"))
        if display_name is not None:
            model["displayName"] = display_name
        result.append(model)
    return result


def _model_capability(models: list[dict[str, str | list[str]]], model_id: str) -> dict[str, bool]:
    accepted = {model_id, f"models/{model_id}"}
    matched = next(
        (
            item for item in models
            if item.get("name") in accepted or item.get("baseModelId") == model_id
        ),
        None,
    )
    methods = matched.get("supportedGenerationMethods", []) if matched else []
    return {
        "present": matched is not None,
        "generateContent": isinstance(methods, list) and "generateContent" in methods,
    }


def _log(diagnostic_log: Callable[[str], None] | None, *, model: str, duration_ms: int, status: int | str, code: str | None = None) -> None:
    if diagnostic_log is None:
        return
    code_value = code if code and _CODE.fullmatch(code) else "none"
    diagnostic_log(
        f"provider=gemini stage=smoke_test model={model} http_status={status} "
        f"provider_code={code_value} duration_ms={duration_ms}"
    )


def _list_log(diagnostic_log: Callable[[str], None] | None, stage: str, **fields: str | int) -> None:
    """Emit only fixed, bounded ListModels observability fields."""
    if diagnostic_log is None:
        return
    allowed = {"category", "elapsed_ms", "model_count", "status_code"}
    suffix = " ".join(f"{name}={value}" for name, value in fields.items() if name in allowed)
    try:
        diagnostic_log(f"diagnostic=gemini_list_models stage={stage}" + (f" {suffix}" if suffix else ""))
    except Exception:
        pass


def _list_error_category(status_code: int) -> str:
    if status_code in {401, 403}:
        return "auth_configuration"
    if status_code == 404:
        return "endpoint_not_found"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "unavailable"
    return "request_rejected"


async def run_gemini_smoke_test(
    diagnostic_log: Callable[[str], None] | None = None,
) -> dict[str, str | int | bool]:
    """Send exactly one minimal request and return curated provider diagnostics."""
    model = safe_gemini_model()
    started = time.perf_counter()
    if not settings.gemini_api_key:
        result = _error_payload(model, "Gemini API key is not configured.", provider_code="MISSING_API_KEY")
        _log(diagnostic_log, model=model, duration_ms=0, status="none", code="MISSING_API_KEY")
        return result
    base_url = safe_gemini_base_url()
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
                    headers=gemini_headers(),
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


async def run_gemini_list_models(
    diagnostic_log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """List only allowlisted Gemini Flash model metadata for an authorized diagnostic."""
    started = time.perf_counter()
    _list_log(diagnostic_log, "start")
    if not settings.gemini_api_key:
        result = {
            "ok": False, "provider": PROVIDER, "operation": "list_models",
            "provider_code": "MISSING_API_KEY",
        }
        _list_log(diagnostic_log, "config_error", category="missing_api_key")
        _list_log(diagnostic_log, "response")
        return result
    base_url = safe_gemini_base_url()
    if base_url is None:
        result = {
            "ok": False, "provider": PROVIDER, "operation": "list_models",
            "provider_code": "INVALID_CONFIGURATION",
        }
        _list_log(diagnostic_log, "config_error", category="invalid_configuration")
        _list_log(diagnostic_log, "response")
        return result
    status: int | str = "network"
    code: str | None = None
    _list_log(diagnostic_log, "config_ok")
    try:
        _list_log(diagnostic_log, "request_start")
        async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.get(
                    f"{base_url}/v1beta/models?pageSize=1000", headers=gemini_headers(),
                )
    except (TimeoutError, httpx.TimeoutException):
        code, status = "TIMEOUT", "timeout"
        result: dict[str, Any] = {
            "ok": False, "provider": PROVIDER, "operation": "list_models", "provider_code": code,
        }
        _list_log(
            diagnostic_log, "request_error", category="timeout", status_code="none",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
    except httpx.TransportError:
        code = "NETWORK_ERROR"
        result = {"ok": False, "provider": PROVIDER, "operation": "list_models", "provider_code": code}
        _list_log(
            diagnostic_log, "request_error", category="transport", status_code="none",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
    except RuntimeError:
        code = "CLIENT_RUNTIME_ERROR"
        result = {"ok": False, "provider": PROVIDER, "operation": "list_models", "provider_code": code}
        _list_log(
            diagnostic_log, "request_error", category="transport", status_code="none",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
    else:
        if response.status_code != 200:
            status = response.status_code
            provider = _provider_error(response, "models")
            code = provider.get("provider_code") if isinstance(provider.get("provider_code"), str) else None
            result = {
                "ok": False, "provider": PROVIDER, "operation": "list_models",
                "provider_status": response.status_code,
                **({"provider_code": code} if code is not None else {}),
            }
            _list_log(
                diagnostic_log, "request_error", category=_list_error_category(response.status_code),
                status_code=response.status_code, elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
        else:
            status = 200
            _list_log(
                diagnostic_log, "request_success", status_code=200,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
            try:
                all_flash_models = _safe_flash_models(response.json())
            except (TypeError, ValueError, RuntimeError):
                code = "INVALID_RESPONSE"
                result = {
                    "ok": False, "provider": PROVIDER, "operation": "list_models",
                    "provider_status": 200, "provider_code": code,
                }
                _list_log(diagnostic_log, "parse_error", category="invalid_response", status_code=200)
            else:
                if all_flash_models is None:
                    code = "INVALID_RESPONSE"
                    result = {
                        "ok": False, "provider": PROVIDER, "operation": "list_models",
                        "provider_status": 200, "provider_code": code,
                    }
                    _list_log(diagnostic_log, "parse_error", category="invalid_response", status_code=200)
                    _list_log(diagnostic_log, "response")
                    return result
                models = [
                    item for item in all_flash_models
                    if "generateContent" in item["supportedGenerationMethods"]
                ]
                result = {
                    "ok": True,
                    "provider": PROVIDER,
                    "operation": "list_models",
                    "models": models,
                    "requested_models": {
                        model: _model_capability(all_flash_models, model) for model in _TARGET_MODELS
                    },
                    "generate_content_models": [item["name"] for item in models],
                }
                _list_log(diagnostic_log, "parse_success", model_count=len(models))
    _list_log(diagnostic_log, "response")
    return result
