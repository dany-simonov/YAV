"""AI or Not synchronous text-detection adapter."""

from __future__ import annotations

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
            raise ExternalAPIError("aiornot", "request_error")
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
