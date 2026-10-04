"""AI or Not v2 synchronous image-detection adapter."""

from __future__ import annotations

import re
from typing import Any

import httpx

from adapters.base import BaseAdapter
from api.schemas import AnalysisResult, ProviderEvidence
from core.config import settings
from core.enums import MediaType, ModelUsed, ScoreKind, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from core.result_normalization import canonicalize_result
from src.provider_protection import admit_provider_operation
from src.validation import normalize_confidence


class AIOrNotImageAdapter(BaseAdapter):
    """Use the provider's image verdict and confidence without boolean inference."""

    URL = "https://api.aiornot.com/v2/image/sync"
    _VERDICTS = {
        "ai": Verdict.FAKE,
        "fake": Verdict.FAKE,
        "human": Verdict.REAL,
        "real": Verdict.REAL,
        "uncertain": Verdict.UNCERTAIN,
        "unknown": Verdict.UNCERTAIN,
    }
    _SAFE_LABEL = re.compile(r"[a-z_]{1,64}")

    @classmethod
    def _result_fields(cls, body: Any) -> tuple[str, float, Any]:
        """Read the documented verdict/confidence pair without boolean inference."""
        if not isinstance(body, dict):
            raise ProviderInfrastructureError("aiornot", "invalid_response", stage="response")
        verdict = body.get("verdict")
        confidence = body.get("confidence")
        report = body.get("report")
        if (verdict is None or confidence is None) and isinstance(report, dict):
            image = report.get("ai_image")
            if isinstance(image, dict):
                verdict = image.get("verdict")
                confidence = image.get("confidence")
        if not isinstance(verdict, str):
            raise ProviderInfrastructureError("aiornot", "invalid_response", stage="response")
        normalized_verdict = verdict.strip().lower()
        if normalized_verdict not in cls._VERDICTS:
            raise ProviderInfrastructureError("aiornot", "invalid_response", stage="response")
        try:
            normalized_confidence = normalize_confidence(confidence)
        except ValueError as exc:
            raise ProviderInfrastructureError("aiornot", "invalid_response", stage="response") from exc
        return normalized_verdict, normalized_confidence, report

    async def analyze(self, data: bytes) -> AnalysisResult:
        if not settings.aiornot_api_key:
            raise ProviderInfrastructureError(
                "aiornot", "config", stage="config", reason="api_key_missing"
            )
        try:
            await admit_provider_operation("aiornot_image")
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                response = await client.post(
                    self.URL,
                    headers={"Authorization": f"Bearer {settings.aiornot_api_key}"},
                    files={"image": ("image.bin", data, "application/octet-stream")},
                )
        except httpx.TimeoutException as exc:
            raise ProviderInfrastructureError("aiornot", "timeout", stage="request") from exc
        except httpx.TransportError as exc:
            raise ProviderInfrastructureError("aiornot", "transport", stage="request") from exc

        if response.status_code in (401, 403):
            raise ExternalAPIError("aiornot", "auth_error", status_code=response.status_code)
        if response.status_code == 429:
            raise ExternalAPIError("aiornot", "rate_limit", status_code=429)
        if response.status_code >= 500:
            raise ProviderInfrastructureError(
                "aiornot", "unavailable", stage="request", status_code=response.status_code
            )
        if response.status_code >= 400:
            raise ExternalAPIError("aiornot", "request_error", status_code=response.status_code)
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderInfrastructureError("aiornot", "invalid_response", stage="response") from exc
        raw_verdict, confidence, report = self._result_fields(body)
        verdict = self._VERDICTS[raw_verdict]

        safe_details: dict[str, str | float | bool | None] = {
            "provider_verdict": raw_verdict,
            "provider_confidence": confidence,
        }
        if isinstance(report, dict):
            for key in ("ai_generated", "deepfake"):
                value = report.get(key)
                if isinstance(value, dict) and value.get("confidence") is not None:
                    try:
                        safe_details[f"{key}_confidence"] = normalize_confidence(value["confidence"])
                    except ValueError:
                        pass

        return canonicalize_result(
            AnalysisResult(
                verdict=verdict,
                confidence=round(confidence, 4),
                model_used=ModelUsed.AIORNOT_IMAGE,
                explanation=f"AI or Not: verdict {raw_verdict}, confidence {round(confidence * 100)}%.",
                media_type=MediaType.IMAGE,
            ),
            ProviderEvidence(
                provider="aiornot",
                model="image_sync",
                raw_score=confidence,
                score_kind=ScoreKind.CLASS_CONFIDENCE,
                predicted_label=raw_verdict if self._SAFE_LABEL.fullmatch(raw_verdict) else None,
                safe_details=safe_details,
            ),
            use_decision_based_authenticity_index=True,
        )
