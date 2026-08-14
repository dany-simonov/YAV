"""Gemini generateContent adapter for normal text verification."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from typing import Any, Callable

import httpx

from adapters.base import BaseAdapter
from api.schemas import AIOriginDetails, AIOriginSignal, AnalysisResult, ProviderEvidence
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
    TRANSPORT_SAFETY_SECONDS = 2.0
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
    COMPLEX_MAX_OUTPUT_TOKENS = 1000
    _COMPLEX_SIGNAL_TYPES = {
        "STRUCTURAL_UNIFORMITY", "LEXICAL_PREDICTABILITY", "SYNTACTIC_UNIFORMITY",
        "REPETITIVE_PATTERNS", "OVERLY_REGULAR_COMPOSITION", "GENERIC_FORMULATION",
        "STYLE_INCONSISTENCY",
    }
    _COMPLEX_RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {
            **_RESPONSE_SCHEMA["properties"],
            "signals": {"type": "array", "maxItems": 5, "items": {"type": "object", "properties": {
                "type": {"type": "string", "enum": sorted(_COMPLEX_SIGNAL_TYPES)},
                "severity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
                "title": {"type": "string"}, "explanation": {"type": "string"},
            }, "required": ["type", "severity", "title", "explanation"], "additionalProperties": False}},
            "human_signals": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
        },
        "required": ["verdict", "authenticity_index", "confidence", "reasoning_summary", "signals", "human_signals"],
        "additionalProperties": False,
    }
    _COMPLEX_PROMPT = _PROMPT + (
        " For this expanded mode, also return at most five significant stylistic or structural signals "
        "and at most three observations supporting human authorship. Do not fact-check or assess factual "
        "credibility. Do not invent evidence. Return Russian JSON only."
    )

    @classmethod
    def _transport_timeout_seconds(cls) -> float:
        """Keep a local reserve for quota finalization and the Appwrite response."""
        timeout = bounded_timeout(cls.TOTAL_TIMEOUT_SECONDS) - cls.TRANSPORT_SAFETY_SECONDS
        if timeout <= 0:
            raise ProviderInfrastructureError(cls.PROVIDER, "timeout", stage="request")
        return timeout

    @classmethod
    def _request_timeout(cls, timeout_seconds: float) -> httpx.Timeout:
        return httpx.Timeout(
            connect=min(cls.REQUEST_TIMEOUT.connect, timeout_seconds),
            read=min(cls.REQUEST_TIMEOUT.read, timeout_seconds),
            write=min(cls.REQUEST_TIMEOUT.write, timeout_seconds),
            pool=min(cls.REQUEST_TIMEOUT.pool, timeout_seconds),
        )

    @staticmethod
    def _diagnose(diagnostic_log: Callable[[str], None] | None, message: str) -> None:
        if diagnostic_log is None:
            return
        try:
            diagnostic_log(message)
        except Exception:
            pass

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
    def _result(cls, response: httpx.Response, model: str, *, complex_mode: bool = False) -> AnalysisResult:
        try:
            body = response.json()
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response", stage="response") from exc
        expected = {"verdict", "authenticity_index", "confidence", "reasoning_summary"}
        if complex_mode:
            expected |= {"signals", "human_signals"}
        if not isinstance(parsed, dict) or set(parsed) != expected:
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
        if not summary or len(summary) > (600 if complex_mode else 300):
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response", stage="response")

        ai_details = None
        if complex_mode:
            raw_signals, raw_human = parsed["signals"], parsed["human_signals"]
            if not isinstance(raw_signals, list) or len(raw_signals) > 5 or not isinstance(raw_human, list) or len(raw_human) > 3:
                raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response", stage="response")
            try:
                signals = [AIOriginSignal.model_validate(item) for item in raw_signals]
                human_signals = [" ".join(item.split()) for item in raw_human]
                ai_details = AIOriginDetails(signals=signals, human_signals=human_signals)
            except (TypeError, ValueError) as exc:
                raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response", stage="response") from exc

        verdict = Verdict(verdict_value)
        return canonicalize_result(
            AnalysisResult(
                verdict=verdict,
                confidence=float(confidence),
                model_used=ModelUsed.GEMINI_TEXT,
                explanation=summary,
                media_type=MediaType.TEXT,
                analysis_mode="complex" if complex_mode else None,
                ai_details=ai_details,
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

    async def analyze(
        self, data: bytes, *, diagnostic_log: Callable[[str], None] | None = None, complex_mode: bool = False,
    ) -> AnalysisResult:
        text = data.decode("utf-8", errors="replace").strip()
        if not text:
            raise ValueError("Gemini text input is empty")
        if not settings.gemini_api_key:
            raise ProviderInfrastructureError(self.PROVIDER, "missing_credentials", stage="config")
        model, base_url = safe_gemini_model(), safe_gemini_base_url()
        if model == "invalid-model" or base_url is None:
            raise ProviderInfrastructureError(self.PROVIDER, "invalid_configuration", stage="config")
        started = time.monotonic()
        try:
            timeout = self._transport_timeout_seconds()
            self._diagnose(
                diagnostic_log,
                "provider=gemini_text stage=request_start "
                f"transport_timeout_ms={round(timeout * 1000)}",
            )
            async with asyncio.timeout(timeout):
                await admit_provider_operation(self.PROVIDER)
                async with httpx.AsyncClient(timeout=self._request_timeout(timeout)) as client:
                    response = await client.post(
                        f"{base_url}/v1beta/models/{model}:generateContent",
                        headers={**gemini_headers(), "Content-Type": "application/json"},
                        json={
                            "contents": [{"parts": [{"text": f"{self._COMPLEX_PROMPT if complex_mode else self._PROMPT}\n\nTEXT:\n{text}"}]}],
                            "generationConfig": {
                                "responseMimeType": "application/json",
                                "responseJsonSchema": self._COMPLEX_RESPONSE_SCHEMA if complex_mode else self._RESPONSE_SCHEMA,
                                **({"maxOutputTokens": self.COMPLEX_MAX_OUTPUT_TOKENS} if complex_mode else {}),
                            },
                        },
                    )
        except TimeoutError as exc:
            self._diagnose(
                diagnostic_log,
                "provider=gemini_text stage=request_timeout "
                f"elapsed_ms={round((time.monotonic() - started) * 1000)}",
            )
            raise ProviderInfrastructureError(self.PROVIDER, "timeout", stage="request") from exc
        except httpx.TimeoutException as exc:
            self._diagnose(
                diagnostic_log,
                "provider=gemini_text stage=request_timeout "
                f"elapsed_ms={round((time.monotonic() - started) * 1000)}",
            )
            raise ProviderInfrastructureError(self.PROVIDER, "timeout", stage="request") from exc
        except httpx.TransportError as exc:
            self._diagnose(
                diagnostic_log,
                "provider=gemini_text stage=request_error "
                f"elapsed_ms={round((time.monotonic() - started) * 1000)}",
            )
            raise ProviderInfrastructureError(self.PROVIDER, "transport", stage="request") from exc
        if response.status_code >= 400:
            self._diagnose(
                diagnostic_log,
                "provider=gemini_text stage=request_error "
                f"elapsed_ms={round((time.monotonic() - started) * 1000)}",
            )
        self._raise_for_status(response)
        self._diagnose(
            diagnostic_log,
            "provider=gemini_text stage=request_success "
            f"elapsed_ms={round((time.monotonic() - started) * 1000)}",
        )
        normalize_started = time.monotonic()
        result = self._result(response, model, complex_mode=complex_mode)
        self._diagnose(
            diagnostic_log,
            "provider=gemini_text stage=normalize "
            f"elapsed_ms={round((time.monotonic() - normalize_started) * 1000)}",
        )
        return result
