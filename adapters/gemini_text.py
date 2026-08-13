"""Gemini generateContent adapter for normal text verification."""

from __future__ import annotations

import asyncio
import json
import math
import re
from typing import Any

import httpx

from adapters.base import BaseAdapter
from api.schemas import AnalysisResult, ProviderEvidence
from core.config import settings
from core.enums import MediaType, ModelUsed, ScoreKind, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from core.result_normalization import canonicalize_result
from src.execution_deadline import bounded_timeout
from src.gemini_client import gemini_headers, safe_gemini_base_url, safe_gemini_model
from src.provider_protection import admit_provider_operation


class GeminiTextAdapter(BaseAdapter):
    """Analyze validated normal text without using Gemini Files API."""

    PROVIDER = "gemini"
    MODEL = "text_verification"
    TOTAL_TIMEOUT_SECONDS = 15.0
    REQUEST_TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=3.0)
    _SUMMARY_PREFIX = re.compile(
        r"^(?:gemini\s+text\s+verification\s*(?::|—)|gemini\s*:)\s*",
        re.IGNORECASE,
    )
    _RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["REAL", "FAKE", "UNCERTAIN"]},
            "authenticity_index": {"type": "integer", "minimum": 0, "maximum": 100},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reasoning_summary": {"type": "string"},
        },
        "required": ["verdict", "authenticity_index", "confidence", "reasoning_summary"],
        "additionalProperties": False,
    }
    _PROMPT = (
        "Assess this text only for probabilistic indicators of AI-generated or synthetic writing. "
        "Return JSON matching the supplied schema. authenticity_index means 0 is a very low "
        "estimated authenticity and 100 is high estimated authenticity. confidence is your "
        "classification confidence. Use UNCERTAIN whenever the evidence is insufficient; especially "
        "for a very short, generic, or context-free fragment, do not infer REAL or FAKE confidently. "
        "reasoning_summary must be natural Russian in one to three short sentences, with no Markdown, "
        "JSON field names, model, API, provider, or technical details. Do not begin it with Gemini Text "
        "Verification:, Gemini:, a model name, a provider name, or another technical prefix."
    )

    @classmethod
    def _request_timeout(cls) -> httpx.Timeout:
        remaining = bounded_timeout(cls.TOTAL_TIMEOUT_SECONDS)
        return httpx.Timeout(
            connect=min(cls.REQUEST_TIMEOUT.connect, remaining),
            read=min(cls.REQUEST_TIMEOUT.read, remaining),
            write=min(cls.REQUEST_TIMEOUT.write, remaining),
            pool=min(cls.REQUEST_TIMEOUT.pool, remaining),
        )

    @classmethod
    def _raise_for_status(cls, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderInfrastructureError(
                cls.PROVIDER, "unavailable", stage="request", status_code=response.status_code
            )
        raise ExternalAPIError(cls.PROVIDER, "request_error", status_code=response.status_code)

    @classmethod
    def _sanitize_summary(cls, summary: str) -> str:
        return cls._SUMMARY_PREFIX.sub("", summary, count=1).strip()

    @classmethod
    def _result(cls, response: httpx.Response, model: str) -> AnalysisResult:
        try:
            body = response.json()
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response", stage="response") from exc
        if not isinstance(parsed, dict) or set(parsed) != {
            "verdict", "authenticity_index", "confidence", "reasoning_summary"
        }:
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response", stage="response")

        verdict_value = parsed["verdict"]
        index, confidence, summary = (
            parsed["authenticity_index"], parsed["confidence"], parsed["reasoning_summary"]
        )
        if (
            not isinstance(verdict_value, str)
            or verdict_value not in {item.value for item in Verdict}
            or isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index <= 100
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
            or not isinstance(summary, str)
        ):
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response", stage="response")
        summary = cls._sanitize_summary(" ".join(summary.split()))
        if not summary or len(summary) > 300:
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response", stage="response")

        verdict = Verdict(verdict_value)
        return canonicalize_result(
            AnalysisResult(
                verdict=verdict,
                confidence=float(confidence),
                model_used=ModelUsed.GEMINI_TEXT,
                explanation=summary,
                media_type=MediaType.TEXT,
            ),
            ProviderEvidence(
                provider=cls.PROVIDER,
                model=model,
                raw_score=index / 100,
                score_kind=ScoreKind.AUTHENTICITY_SCORE,
                predicted_label=verdict.value,
                safe_details={"structured_response": True},
            ),
        )

    async def analyze(self, data: bytes) -> AnalysisResult:
        text = data.decode("utf-8", errors="replace").strip()
        if not text:
            raise ValueError("Gemini text input is empty")
        if not settings.gemini_api_key:
            raise ProviderInfrastructureError(self.PROVIDER, "missing_credentials", stage="config")
        model, base_url = safe_gemini_model(), safe_gemini_base_url()
        if model == "invalid-model" or base_url is None:
            raise ProviderInfrastructureError(self.PROVIDER, "invalid_configuration", stage="config")
        try:
            timeout = bounded_timeout(self.TOTAL_TIMEOUT_SECONDS)
            async with asyncio.timeout(timeout):
                await admit_provider_operation(self.PROVIDER)
                async with httpx.AsyncClient(timeout=self._request_timeout()) as client:
                    response = await client.post(
                        f"{base_url}/v1beta/models/{model}:generateContent",
                        headers={**gemini_headers(), "Content-Type": "application/json"},
                        json={
                            "contents": [{"parts": [{"text": f"{self._PROMPT}\n\nTEXT:\n{text}"}]}],
                            "generationConfig": {
                                "responseMimeType": "application/json",
                                "responseJsonSchema": self._RESPONSE_SCHEMA,
                            },
                        },
                    )
        except TimeoutError as exc:
            raise ProviderInfrastructureError(self.PROVIDER, "timeout", stage="request") from exc
        except httpx.TimeoutException as exc:
            raise ProviderInfrastructureError(self.PROVIDER, "timeout", stage="request") from exc
        except httpx.TransportError as exc:
            raise ProviderInfrastructureError(self.PROVIDER, "transport", stage="request") from exc
        self._raise_for_status(response)
        return self._result(response, model)
