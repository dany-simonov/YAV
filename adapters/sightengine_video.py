"""Sightengine synchronous Deepfake Video adapter for prevalidated short videos."""

from __future__ import annotations

import httpx

from adapters.base import BaseAdapter
from api.schemas import AnalysisResult
from core.config import settings
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from src.provider_protection import admit_provider_operation
from src.validation import normalize_confidence


class SightengineVideoAdapter(BaseAdapter):
    """Detect face manipulation in videos already bounded by the media boundary."""

    URL = "https://api.sightengine.com/1.0/video/check-sync.json"
    MODEL = "deepfake"
    TIMEOUT = 60.0

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
            raise ExternalAPIError("sightengine", "request_error")

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
                if not isinstance(frame_type, dict) or "deepfake" not in frame_type:
                    continue
                scores.append(normalize_confidence(frame_type["deepfake"]))
        except ValueError as exc:
            raise ProviderInfrastructureError("sightengine", "invalid_response") from exc
        if not scores:
            raise ProviderInfrastructureError("sightengine", "invalid_response")

        # The result answers whether any sampled segment is likely face-manipulated.
        score = max(scores)
        if score >= 0.75:
            verdict = Verdict.FAKE
        elif score <= 0.35:
            verdict = Verdict.REAL
        else:
            verdict = Verdict.UNCERTAIN

        return AnalysisResult(
            verdict=verdict,
            confidence=round(score, 4),
            model_used=ModelUsed.SIGHTENGINE_VIDEO_DIRECT,
            explanation=f"Sightengine Video: вероятность deepfake {round(score * 100)}%.",
            media_type=MediaType.VIDEO,
        )
