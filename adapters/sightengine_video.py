"""Sightengine synchronous AI-generated video adapter for prevalidated videos."""

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


class SightengineVideoAdapter(BaseAdapter):
    """Detect AI-generated video in files already bounded by the media boundary."""

    URL = "https://api.sightengine.com/1.0/video/check-sync.json"
    MODEL = "genai"
    TIMEOUT = 60.0
    _SAFE_ERROR_CODE = re.compile(r"[A-Za-z0-9_.-]{1,80}")
    _SECRET_SHAPED = re.compile(
        r"(?i)(?:bearer\s+\S+|(?:api[_ -]?(?:key|secret|user)|authorization|x-appwrite)\s*[:=]|"
        r"data:[^\s]{1,}|[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
    )

    @classmethod
    def _safe_error_diagnostic(cls, response: httpx.Response) -> str | None:
        """Return selected JSON error fields only; never render the provider body."""
        try:
            body = response.json()
        except (TypeError, ValueError):
            return None
        if not isinstance(body, dict):
            return None

        error = body.get("error")
        error_data = error if isinstance(error, dict) else {}
        code = error_data.get("code", body.get("code", body.get("error_code")))
        message = error_data.get("message", body.get("message"))
        if message is None and isinstance(error, str):
            message = error

        parts: list[str] = []
        if isinstance(code, str):
            normalized_code = " ".join(code.split())
            if cls._SAFE_ERROR_CODE.fullmatch(normalized_code):
                parts.append(f"code={normalized_code}")
        if isinstance(message, str):
            normalized_message = " ".join(message.split())[:200]
            if normalized_message and not cls._SECRET_SHAPED.search(normalized_message):
                parts.append(f"message={normalized_message}")
        return " ".join(parts) or None

    async def analyze(self, data: bytes) -> AnalysisResult:
        try:
            await admit_provider_operation("sightengine")
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                response = await client.post(
                    self.URL,
                    data={
                        "api_user": settings.sightengine_api_user,
                        "api_secret": settings.sightengine_api_secret,
                        "models": self.MODEL,
                    },
                    files={"media": ("video.mp4", data, "video/mp4")},
                )
        except httpx.TimeoutException as exc:
            raise ProviderInfrastructureError("sightengine", "timeout") from exc
        except httpx.TransportError as exc:
            raise ProviderInfrastructureError("sightengine", "transport") from exc

        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderInfrastructureError("sightengine", "unavailable")
        if response.status_code >= 400:
            raise ExternalAPIError(
                "sightengine",
                "request_error",
                status_code=response.status_code,
                provider_message=self._safe_error_diagnostic(response),
            )

        try:
            body = response.json()
            data_section = body.get("data") if isinstance(body, dict) else None
            frames = data_section.get("frames") if isinstance(data_section, dict) else None
        except (TypeError, ValueError, AttributeError) as exc:
            raise ProviderInfrastructureError("sightengine", "invalid_response") from exc
        if not isinstance(body, dict) or body.get("status") != "success" or not isinstance(frames, list):
            raise ProviderInfrastructureError("sightengine", "invalid_response")

        scores: list[float] = []
        try:
            for frame in frames:
                frame_type = frame.get("type") if isinstance(frame, dict) else None
                if not isinstance(frame_type, dict) or "ai_generated" not in frame_type:
                    continue
                scores.append(normalize_confidence(frame_type["ai_generated"]))
        except ValueError as exc:
            raise ProviderInfrastructureError("sightengine", "invalid_response") from exc
        if not scores:
            raise ProviderInfrastructureError("sightengine", "invalid_response")

        # The result answers whether any sampled segment is likely AI-generated.
        score = max(scores)
        if score >= 0.75:
            verdict = Verdict.FAKE
        elif score <= 0.35:
            verdict = Verdict.REAL
        else:
            verdict = Verdict.UNCERTAIN

        return canonicalize_result(
            AnalysisResult(
                verdict=verdict,
                confidence=round(score, 4),
                model_used=ModelUsed.SIGHTENGINE_VIDEO_DIRECT,
                explanation=f"Sightengine Video: вероятность ИИ-генерации {round(score * 100)}%.",
                media_type=MediaType.VIDEO,
            ),
            ProviderEvidence(
                provider="sightengine",
                model=self.MODEL,
                raw_score=score,
                score_kind=ScoreKind.AI_PROBABILITY,
                predicted_label=verdict.value,
                safe_details={
                    "aggregation": "max_frame_probability",
                    "frames_scored": len(scores),
                },
            ),
        )
