"""AI or Not synchronous voice-detection adapter."""

from __future__ import annotations

import httpx

from adapters.base import BaseAdapter
from api.schemas import AnalysisResult
from core.config import settings
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from src.provider_protection import admit_provider_operation
from src.validation import normalize_confidence


class AIOrNotAudioAdapter(BaseAdapter):
    """Analyze voice audio without deciding whether production should route music here."""

    URL = "https://api.aiornot.com/v1/reports/voice"
    TIMEOUT = 120.0

    async def analyze(self, data: bytes) -> AnalysisResult:
        if not settings.aiornot_api_key:
            raise ProviderInfrastructureError("aiornot", "unavailable")

        try:
            await admit_provider_operation("aiornot")
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                response = await client.post(
                    self.URL,
                    headers={"Authorization": f"Bearer {settings.aiornot_api_key}"},
                    files={"file": ("audio.mp3", data, "audio/mpeg")},
                )
        except httpx.TimeoutException as exc:
            raise ProviderInfrastructureError("aiornot", "timeout") from exc
        except httpx.TransportError as exc:
            raise ProviderInfrastructureError("aiornot", "transport") from exc

        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderInfrastructureError("aiornot", "unavailable")
        if response.status_code >= 400:
            raise ExternalAPIError("aiornot", "request_error")

        try:
            body = response.json()
            report = body.get("report") if isinstance(body, dict) else None
            provider_verdict = report.get("verdict") if isinstance(report, dict) else None
            score = normalize_confidence(report.get("confidence")) if isinstance(report, dict) else None
        except (TypeError, ValueError, AttributeError) as exc:
            raise ProviderInfrastructureError("aiornot", "invalid_response") from exc
        if not isinstance(provider_verdict, str) or score is None:
            raise ProviderInfrastructureError("aiornot", "invalid_response")

        match provider_verdict.lower():
            case "ai":
                verdict = Verdict.FAKE
            case "human":
                verdict = Verdict.REAL
            case "uncertain":
                verdict = Verdict.UNCERTAIN
            case _:
                raise ProviderInfrastructureError("aiornot", "invalid_response")

        return AnalysisResult(
            verdict=verdict,
            confidence=round(score, 4),
            model_used=ModelUsed.AIORNOT_AUDIO,
            explanation=f"AI or Not Voice: вероятность синтетической речи {round(score * 100)}%.",
            media_type=MediaType.AUDIO,
        )
