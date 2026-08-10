"""AI or Not synchronous text-detection adapter."""

from __future__ import annotations

import re

import httpx

from adapters.base import BaseAdapter
from api.schemas import AnalysisResult, ProviderEvidence
from core.config import settings
from core.enums import MediaType, ModelUsed, ScoreKind, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from core.result_normalization import canonicalize_result
from src.provider_protection import admit_provider_operation
from src.validation import normalize_confidence


class AIOrNotTextAdapter(BaseAdapter):
    """Call AI or Not only for text accepted by its public API contract."""

    URL = "https://api.aiornot.com/v2/text/sync"
    MIN_CHARACTERS = 250
    MIN_WORDS = 64

    _SAFE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")

    @classmethod
    def _sanitize_error_message(cls, value: str, text: str) -> str | None:
        """Return a bounded provider reason only when it contains no request data."""
        normalized_text = " ".join(text.split()).casefold()
        normalized_value = " ".join(value.split()).casefold()
        words = normalized_text.split()
        markers = {normalized_text}
        if len(words) >= 6:
            markers.update(" ".join(words[index:index + 6]) for index in range(len(words) - 5))
        if any(len(marker) >= 24 and marker in normalized_value for marker in markers):
            return None

        sanitized = value.replace("\r", " ").replace("\n", " ").strip()
        if settings.aiornot_api_key:
            sanitized = sanitized.replace(settings.aiornot_api_key, "[REDACTED]")
        sanitized = re.sub(
            r"(?i)\b(authorization|x-appwrite(?:-[a-z0-9_-]+)?|api[-_ ]?key)\s*[:=]\s*"
            r"(?:bearer\s+)?[^\s,;]+",
            r"\1=[REDACTED]",
            sanitized,
        )
        sanitized = re.sub(r"(?i)\bbearer\s+[^\s,;]+", "Bearer [REDACTED]", sanitized)
        sanitized = re.sub(
            r"(?i)\b(?:sk|pk|api)[-_][a-z0-9_-]{12,}\b", "[REDACTED]", sanitized
        )

        # After explicit redactions, reject rather than risk logging an
        # unlabelled credential or a secret in an unfamiliar response format.
        if re.search(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", sanitized):
            return None
        if re.search(r"(?i)\b(?:token|secret|password)\s*[:=]\s*[^\s,;]{8,}", sanitized):
            return None
        if re.search(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{40,}(?![A-Za-z0-9_-])", sanitized):
            return None
        return sanitized[:300] or None

    @classmethod
    def _safe_error_diagnostics(
        cls, response: httpx.Response, text: str
    ) -> tuple[str | None, int, tuple[str, ...], tuple[str, ...], str | None]:
        """Extract one bounded diagnostic field without retaining response data.

        JSON diagnostics contain only conservative key/path names and one
        allowlisted primitive string. A short text/plain 4xx reason is accepted
        through the same sanitizer; every other non-JSON body is ignored.
        """
        raw_content_type = response.headers.get("content-type", "")
        base_content_type = raw_content_type.split(";", 1)[0].strip().lower()
        content_type = (
            base_content_type
            if re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", base_content_type)
            else "unknown"
        )
        response_length = len(response.content)
        is_json = content_type == "application/json" or content_type.endswith("+json")
        if content_type == "text/plain" and response_length <= 300:
            value = response.content.decode("utf-8", errors="replace")
            return content_type, response_length, (), (), cls._sanitize_error_message(value, text)
        if not is_json:
            return content_type, response_length, (), (), None

        try:
            body = response.json()
        except (TypeError, ValueError):
            return content_type, response_length, (), (), None
        if not isinstance(body, dict):
            return content_type, response_length, (), (), None

        response_keys = tuple(sorted(key for key in body if isinstance(key, str) and cls._SAFE_KEY.fullmatch(key)))
        error = body.get("error")
        errors = body.get("errors")
        first_error = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else None
        candidates = (
            ("message", body.get("message")),
            ("detail", body.get("detail")),
            ("error", error),
            ("error.message", error.get("message") if isinstance(error, dict) else None),
            ("error.detail", error.get("detail") if isinstance(error, dict) else None),
            ("error.code", error.get("code") if isinstance(error, dict) else None),
            ("errors[0].message", first_error.get("message") if first_error else None),
            ("errors[0].detail", first_error.get("detail") if first_error else None),
        )
        response_paths = tuple(path for path, value in candidates if isinstance(value, str))
        value = next((candidate for _, candidate in candidates if isinstance(candidate, str)), None)
        if value is None:
            return content_type, response_length, response_keys, response_paths, None

        return (
            content_type,
            response_length,
            response_keys,
            response_paths,
            cls._sanitize_error_message(value, text),
        )

    @classmethod
    def is_eligible(cls, data: bytes | str) -> bool:
        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
        normalized = text.strip()
        return len(normalized) >= cls.MIN_CHARACTERS and len(normalized.split()) >= cls.MIN_WORDS

    async def analyze(self, data: bytes) -> AnalysisResult:
        text = data.decode("utf-8", errors="replace").strip()
        if not self.is_eligible(text):
            raise ValueError("AI or Not text input is not eligible")
        if not settings.aiornot_api_key:
            raise ProviderInfrastructureError("aiornot", "unavailable")

        try:
            await admit_provider_operation("aiornot")
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                response = await client.post(
                    self.URL,
                    headers={"Authorization": f"Bearer {settings.aiornot_api_key}"},
                    data={"text": text},
                )
        except httpx.TimeoutException as exc:
            raise ProviderInfrastructureError("aiornot", "timeout") from exc
        except httpx.TransportError as exc:
            raise ProviderInfrastructureError("aiornot", "transport") from exc

        if response.status_code >= 500:
            raise ProviderInfrastructureError("aiornot", "unavailable")
        if response.status_code == 429:
            raise ProviderInfrastructureError("aiornot", "unavailable")
        if response.status_code >= 400:
            content_type, response_length, response_keys, response_paths, provider_message = (
                self._safe_error_diagnostics(response, text)
            )
            raise ExternalAPIError(
                "aiornot",
                "request_error",
                status_code=response.status_code,
                provider_message=provider_message,
                content_type=content_type,
                response_length=response_length,
                response_keys=response_keys,
                response_paths=response_paths,
            )
        try:
            body = response.json()
            report = body.get("report") if isinstance(body, dict) else None
            ai_text = report.get("ai_text") if isinstance(report, dict) else None
            detected = ai_text.get("is_detected") if isinstance(ai_text, dict) else None
            score = normalize_confidence(ai_text.get("confidence")) if isinstance(ai_text, dict) else None
        except (TypeError, ValueError, AttributeError) as exc:
            raise ProviderInfrastructureError("aiornot", "invalid_response") from exc
        if not isinstance(detected, bool) or score is None:
            raise ProviderInfrastructureError("aiornot", "invalid_response")

        if score >= 0.75:
            verdict = Verdict.FAKE
        elif score <= 0.25:
            verdict = Verdict.REAL
        else:
            verdict = Verdict.UNCERTAIN

        # Current integration contract treats `confidence` as the AI-text
        # likelihood. `is_detected` is retained as provider evidence; verdict
        # thresholds intentionally remain score-authoritative for compatibility.
        return canonicalize_result(
            AnalysisResult(
                verdict=verdict,
                confidence=round(score, 4),
                model_used=ModelUsed.AIORNOT_TEXT,
                explanation=f"AI or Not: вероятность написания ИИ {round(score * 100)}%.",
                media_type=MediaType.TEXT,
            ),
            ProviderEvidence(
                provider="aiornot",
                model="text_sync",
                raw_score=score,
                score_kind=ScoreKind.AI_PROBABILITY,
                predicted_label=verdict.value,
                safe_details={"is_detected": detected},
            ),
        )
