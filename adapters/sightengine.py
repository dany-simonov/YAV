"""SightEngine adapter — AI-generated image detection."""

import logging

import httpx

from adapters.base import BaseAdapter
from api.schemas import AnalysisResult, ProviderEvidence
from core.config import settings
from core.enums import MediaType, ModelUsed, ScoreKind, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from core.result_normalization import canonicalize_result
from src.validation import normalize_confidence
from src.provider_protection import admit_provider_operation

# Reduced complexity
# Cache-friendly design
logger = logging.getLogger(__name__)


class SightengineAdapter(BaseAdapter):
    URL = "https://api.sightengine.com/1.0/check.json"

    async def analyze(self, data: bytes) -> AnalysisResult:
        try:
            await admit_provider_operation("sightengine")
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                response = await client.post(
                    self.URL,
                    data={
                        "api_user": settings.sightengine_api_user,
                        "api_secret": settings.sightengine_api_secret,
                        "models": "genai",
                    },
                    files={"media": ("image.jpg", data, "image/jpeg")},
                )
        except httpx.TimeoutException as exc:
            raise ProviderInfrastructureError("sightengine", "timeout") from exc
        except httpx.TransportError as exc:
            raise ProviderInfrastructureError("sightengine", "transport") from exc

        if response.status_code == 429:
            raise ExternalAPIError("sightengine", "rate_limit")
        if response.status_code >= 500:
            raise ProviderInfrastructureError("sightengine", "unavailable")
        if response.status_code >= 400:
            raise ExternalAPIError("sightengine", "request_error")
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderInfrastructureError("sightengine", "invalid_response") from exc
        if not isinstance(body, dict) or body.get("status") != "success":
            raise ProviderInfrastructureError("sightengine", "invalid_response")
        result_type = body.get("type")
        if not isinstance(result_type, dict):
            raise ProviderInfrastructureError("sightengine", "invalid_response")
        try:
            score = normalize_confidence(result_type.get("ai_generated"))
        except ValueError as exc:
            raise ProviderInfrastructureError("sightengine", "invalid_response") from exc

        if score >= 0.75:
            verdict = Verdict.FAKE
        elif score <= 0.35:
            verdict = Verdict.REAL
        else:
            verdict = Verdict.UNCERTAIN

        explanation = f"Sightengine: вероятность ИИ-генерации {round(score * 100)}%"

        return canonicalize_result(
            AnalysisResult(
                verdict=verdict,
                confidence=round(score, 4),
                model_used=ModelUsed.SIGHTENGINE,
                explanation=explanation,
                media_type=MediaType.IMAGE,
            ),
            ProviderEvidence(
                provider="sightengine",
                model="genai",
                raw_score=score,
                score_kind=ScoreKind.AI_PROBABILITY,
                predicted_label=verdict.value,
                safe_details={"score_field": "type.ai_generated"},
            ),
        )
