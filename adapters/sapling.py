"""Sapling AI text detection adapter."""

import logging

import httpx

from adapters.base import BaseAdapter
from api.schemas import AnalysisResult
from core.config import settings
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ExternalAPIError
from src.validation import bounded_provider_string, normalize_confidence

# Validated input parameters
# Following best practices
logger = logging.getLogger(__name__)

MIN_TEXT_LENGTH = 50
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
                explanation=f"Текст слишком короткий для анализа (минимум {MIN_TEXT_LENGTH} символов).",
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

        payload = {"key": settings.sapling_api_key, "text": text}

        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                response = await client.post(self.URL, json=payload)
        except httpx.TimeoutException:
            return self._build_uncertain(
                "Sapling AI: таймаут запроса.",
                ModelUsed.SAPLING,
                MediaType.TEXT,
            )

        if response.status_code == 429:
            raise ExternalAPIError("sapling", "rate_limit")
        if response.status_code >= 500:
            raise ExternalAPIError("sapling", "server_error")
        if response.status_code >= 400:
            raise ExternalAPIError("sapling", "request_error")
        try:
            body = response.json()
        except ValueError as exc:
            raise ExternalAPIError("sapling", "invalid_response") from exc
        if not isinstance(body, dict):
            raise ExternalAPIError("sapling", "invalid_response")
        try:
            score = normalize_confidence(body.get("score"))
        except ValueError as exc:
            raise ExternalAPIError("sapling", "invalid_response") from exc
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
            if not isinstance(item, list) or len(item) < 2:
                continue
            try:
                item_score = normalize_confidence(item[1])
            except ValueError:
                continue
            sentence = bounded_provider_string(item[0], 100)
            if sentence and item_score > top_score:
                top_sentence = sentence
                top_score = item_score

        explanation = f"Sapling AI: вероятность написан ИИ {round(score * 100)}%."
        if top_sentence:
            explanation += f" Наиболее подозрительное предложение: «{top_sentence[:100]}» ({round(top_score * 100)}%)"
        return AnalysisResult(
            verdict=verdict,
            confidence=round(score, 4),
            model_used=ModelUsed.SAPLING,
            explanation=explanation,
            media_type=MediaType.TEXT,
        )
