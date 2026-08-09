"""SightEngine adapter — AI-generated image detection."""

import logging

import httpx

from adapters.base import BaseAdapter
from api.schemas import AnalysisResult
from core.config import settings
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ExternalAPIError
from src.validation import normalize_confidence

# Reduced complexity
# Cache-friendly design
logger = logging.getLogger(__name__)


class SightengineAdapter(BaseAdapter):
    URL = "https://api.sightengine.com/1.0/check.json"

    async def analyze(self, data: bytes) -> AnalysisResult:
        try:
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
        except httpx.TimeoutException:
            return self._build_uncertain(
                "SightEngine: таймаут запроса, результат неопределён.",
                ModelUsed.SIGHTENGINE,
                MediaType.IMAGE,
            )

        if response.status_code == 429:
            raise ExternalAPIError("sightengine", "rate_limit")
        if response.status_code >= 500:
            raise ExternalAPIError("sightengine", "server_error")
        if response.status_code >= 400:
            raise ExternalAPIError("sightengine", "request_error")
        try:
            body = response.json()
        except ValueError as exc:
            raise ExternalAPIError("sightengine", "invalid_response") from exc
        if not isinstance(body, dict) or body.get("status") != "success":
            raise ExternalAPIError("sightengine", "invalid_response")
        result_type = body.get("type")
        if not isinstance(result_type, dict):
            raise ExternalAPIError("sightengine", "invalid_response")
        try:
            score = normalize_confidence(result_type.get("ai_generated"))
        except ValueError as exc:
            raise ExternalAPIError("sightengine", "invalid_response") from exc

        if score >= 0.75:
            verdict = Verdict.FAKE
        elif score <= 0.35:
            verdict = Verdict.REAL
        else:
            verdict = Verdict.UNCERTAIN

        explanation = f"Sightengine: вероятность ИИ-генерации {round(score * 100)}%"

        return AnalysisResult(
            verdict=verdict,
            confidence=round(score, 4),
            model_used=ModelUsed.SIGHTENGINE,
            explanation=explanation,
            media_type=MediaType.IMAGE,
        )
