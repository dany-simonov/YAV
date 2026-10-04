"""HuggingFace image deepfake detection — fallback adapter."""

import asyncio
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

# Type hints added
# Logging improved
# Better exception handling
logger = logging.getLogger(__name__)

MODEL_ID = "dima806/deepfake_vs_real_image_detection"
MODEL_URL = f"https://router.huggingface.co/hf-inference/models/{MODEL_ID}"
MAX_RETRIES = 2
COLD_START_DELAY = 10


class HFImageAdapter(BaseAdapter):
    async def analyze(self, data: bytes) -> AnalysisResult:
        if not settings.hf_api_token:
            raise ProviderInfrastructureError(
                "huggingface", "config", stage="config", reason="api_key_missing"
            )
        headers = {"Authorization": f"Bearer {settings.hf_api_token}"}

        for attempt in range(MAX_RETRIES + 1):
            try:
                await admit_provider_operation("huggingface")
                async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                    response = await client.post(MODEL_URL, headers=headers, content=data)
            except httpx.TimeoutException as exc:
                raise ProviderInfrastructureError("huggingface", "timeout") from exc
            except httpx.TransportError as exc:
                raise ProviderInfrastructureError("huggingface", "transport") from exc

            if response.status_code in (401, 403):
                raise ExternalAPIError("huggingface", "auth_error", status_code=response.status_code)
            if response.status_code >= 500:
                raise ProviderInfrastructureError("huggingface", "unavailable")
            if response.status_code == 429:
                raise ExternalAPIError("huggingface", "rate_limit", status_code=429)
            if response.status_code >= 400:
                raise ExternalAPIError("huggingface", "request_error")

            try:
                body = response.json()
            except ValueError as exc:
                raise ProviderInfrastructureError("huggingface", "invalid_response") from exc

            # Handle cold start
            if isinstance(body, dict) and body.get("error", "").startswith("Model"):
                if attempt < MAX_RETRIES:
                    logger.info("HF Image model loading, retry in %ds...", COLD_START_DELAY)
                    await asyncio.sleep(COLD_START_DELAY)
                    continue
                raise ProviderInfrastructureError("huggingface", "model_loading")
            break

        if not isinstance(body, list) or not body or len(body) > 100:
            raise ProviderInfrastructureError("huggingface", "invalid_response")

        # Find best prediction
        candidates: list[tuple[str, float]] = []
        for item in body:
            if not isinstance(item, dict) or not isinstance(item.get("label"), str):
                continue
            try:
                candidates.append((item["label"].upper(), normalize_confidence(item.get("score"))))
            except ValueError:
                continue
        if not candidates:
            raise ProviderInfrastructureError("huggingface", "invalid_response")
        label, score = max(candidates, key=lambda item: item[1])

        # This exact model declares only REAL (id 0) and FAKE (id 1).  Do not
        # infer semantics for an arbitrary label returned by another model.
        if label not in {"FAKE", "REAL"}:
            raise ProviderInfrastructureError("huggingface", "invalid_response")
        verdict = Verdict.FAKE if label == "FAKE" else Verdict.REAL

        explanation = f"HuggingFace Image: {label} с уверенностью {round(score * 100)}%"

        return canonicalize_result(
            AnalysisResult(
                verdict=verdict,
                confidence=round(score, 4),
                model_used=ModelUsed.HF_IMAGE,
                explanation=explanation,
                media_type=MediaType.IMAGE,
            ),
            ProviderEvidence(
                provider="huggingface",
                model=MODEL_ID,
                raw_score=score,
                score_kind=ScoreKind.CLASS_CONFIDENCE,
                predicted_label=label,
                safe_details={
                    "score_field": "top_label_score",
                    "raw_label": label,
                    "raw_label_score": score,
                },
            ),
            use_decision_based_authenticity_index=True,
        )
