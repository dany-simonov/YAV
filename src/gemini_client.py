"""Shared server-side Gemini configuration helpers."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from core.config import settings


_MODEL = re.compile(r"[A-Za-z0-9._-]{1,128}")


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
