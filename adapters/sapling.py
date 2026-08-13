"""Sapling AI text detection adapter."""

import logging

import httpx

from adapters.base import BaseAdapter
from api.schemas import AnalysisResult, ProviderEvidence
from core.config import settings
from core.enums import MediaType, ModelUsed, ScoreKind, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from core.result_normalization import canonicalize_result
from src.validation import bounded_provider_string, normalize_confidence
from src.provider_protection import admit_provider_operation

# Validated input parameters
# Following best practices
logger = logging.getLogger(__name__)

MIN_TEXT_LENGTH = 1
MAX_TEXT_LENGTH = 10_000


class SaplingAdapter(BaseAdapter):
    URL = "https://api.sapling.ai/api/v1/aidetect"

    async def analyze(self, data: bytes) -> AnalysisResult:
        text = data.decode("utf-8", errors="replace").strip()

        if len(text) < MIN_TEXT_LENGTH:
            return AnalysisResult(
                verdict=Verdict.UNCERTAIN,
                confidence=0.0,
                model_used=ModelUsed.SAPLING,
                explanation="Текст слишком короткий для анализа.",
                media_type=MediaType.TEXT,
            )

        if len(text) > MAX_TEXT_LENGTH:
            return AnalysisResult(
                verdict=Verdict.UNCERTAIN,
                confidence=0.0,
                model_used=ModelUsed.SAPLING,
                explanation="Текст превышает лимит в 10 000 символов.",
                media_type=MediaType.TEXT,
            )

        if not settings.sapling_api_key:
            raise ProviderInfrastructureError("sapling", "unavailable")

        payload = {"key": settings.sapling_api_key, "text": text}

        try:
            await admit_provider_operation("sapling")
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                response = await client.post(
                    self.URL,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ProviderInfrastructureError("sapling", "timeout") from exc
        except httpx.TransportError as exc:
            raise ProviderInfrastructureError("sapling", "transport") from exc

        if response.status_code == 429:
            raise ProviderInfrastructureError("sapling", "unavailable")
        if response.status_code >= 500:
            raise ProviderInfrastructureError("sapling", "unavailable")
        if response.status_code >= 400:
            raise ExternalAPIError("sapling", "request_error", status_code=response.status_code)
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderInfrastructureError("sapling", "invalid_response") from exc
        if not isinstance(body, dict):
            raise ProviderInfrastructureError("sapling", "invalid_response")
        try:
            score = normalize_confidence(body.get("score"))
        except ValueError as exc:
            raise ProviderInfrastructureError("sapling", "invalid_response") from exc
        sentence_scores = body.get("sentence_scores", [])
        if not isinstance(sentence_scores, list):
            sentence_scores = []

        if score >= 0.80:
            verdict = Verdict.FAKE
        elif score <= 0.25:
            verdict = Verdict.REAL
        else:
            verdict = Verdict.UNCERTAIN

        # Find most suspicious sentence
        top_sentence = ""
        top_score = 0.0
        for item in sentence_scores[:100]:
            if isinstance(item, dict):
                raw_sentence, raw_score = item.get("sentence"), item.get("score")
            elif isinstance(item, list) and len(item) >= 2:
                raw_sentence, raw_score = item[0], item[1]
            else:
                continue
            try:
                item_score = normalize_confidence(raw_score)
            except ValueError:
                continue
            sentence = bounded_provider_string(raw_sentence, 100)
            if sentence and item_score > top_score:
                top_sentence = sentence
                top_score = item_score

        explanation = f"Sapling AI: вероятность написан ИИ {round(score * 100)}%."
        if top_sentence:
            explanation += f" Наиболее подозрительное предложение: «{top_sentence[:100]}» ({round(top_score * 100)}%)"
        return canonicalize_result(
            AnalysisResult(
                verdict=verdict,
                confidence=round(score, 4),
                model_used=ModelUsed.SAPLING,
                explanation=explanation,
                media_type=MediaType.TEXT,
            ),
            ProviderEvidence(
                provider="sapling",
                model="aidetect",
                raw_score=score,
                score_kind=ScoreKind.AI_PROBABILITY,
                predicted_label=verdict.value,
                safe_details={"score_field": "score"},
            ),
        )
