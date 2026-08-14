"""Shared server-side Gemini configuration helpers."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from core.config import settings


_MODEL = re.compile(r"[A-Za-z0-9._-]{1,128}")
_GOOGLE_STATUS = re.compile(r"[A-Z_]{1,64}")


def safe_gemini_error_details(response: Any, *, analyzed_text: str) -> tuple[str | None, str | None, int | None]:
    """Return bounded Google 4xx metadata without retaining input or secrets."""
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
    except (AttributeError, TypeError, ValueError):
        error = None
    if not isinstance(error, dict):
        return None, None, None

    status = error.get("status")
    code = error.get("code")
    google_status = status if isinstance(status, str) and _GOOGLE_STATUS.fullmatch(status) else None
    google_code = code if isinstance(code, int) and 100 <= code <= 599 else None
    message = error.get("message")
    if not isinstance(message, str):
        return None, google_status, google_code

    # Google normally returns a technical field violation here.  Protect the
    # diagnostic boundary if an upstream error ever echoes the prompt/input.
    normalized_input = " ".join(analyzed_text.split())
    normalized = " ".join(message.split())
    for candidate in (analyzed_text, normalized_input):
        if candidate:
            normalized = normalized.replace(candidate, "[REDACTED_INPUT]")
    normalized = re.sub(r"https?://\S+", "[REDACTED_URL]", normalized)
    normalized = re.sub(r"(?i)(?:api[-_ ]?key|key|token|secret)\s*[=:]\s*\S+", "[REDACTED_SECRET]", normalized)
    return normalized[:240] or None, google_status, google_code


def safe_gemini_model() -> str:
    """Return the configured Gemini model only when it is URL-safe."""
    model = settings.gemini_model.strip()
    return model if _MODEL.fullmatch(model) else "invalid-model"


def safe_gemini_credibility_model() -> str:
    """Return the dedicated grounded model, falling back to the shared model."""
    configured = settings.gemini_credibility_model.strip()
    model = configured or settings.gemini_model.strip()
    return model if _MODEL.fullmatch(model) else "invalid-model"


def safe_gemini_base_url() -> str | None:
    """Allow only an HTTPS server-side Gemini API origin."""
    value = settings.gemini_api_url.rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return None
    return value


def gemini_headers() -> dict[str, str]:
    return {"x-goog-api-key": settings.gemini_api_key}
