"""Single-call Gemini credibility assessment without external search grounding."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from typing import Any, Callable

import httpx

from adapters.base import BaseAdapter
from api.schemas import CredibilityAssessment, CredibilityIssue
from core.config import settings
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from src.execution_deadline import bounded_timeout
from src.gemini_client import gemini_headers, safe_gemini_base_url, safe_gemini_model
from src.provider_protection import admit_provider_operation


class GeminiCredibilityAdapter(BaseAdapter):
    """Assess a text's general credibility with one ordinary Gemini request."""

    PROVIDER = "gemini"
    MODEL = "gemini_credibility"
    TOTAL_TIMEOUT_SECONDS = 13.0
    TRANSPORT_SAFETY_SECONDS = 2.0
    REQUEST_TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=3.0)
    MAX_OUTPUT_TOKENS = 700
    _ISSUE_TYPES = {
        "FACTUAL_CONTRADICTION",
        "UNSUPPORTED_CLAIM",
        "LOGICAL_INCONSISTENCY",
        "MISLEADING_INFERENCE",
        "OUTDATED_INFORMATION",
        "INSUFFICIENT_EVIDENCE",
    }
    _SEVERITIES = {"LOW", "MEDIUM", "HIGH"}
    _PROMPT = """Оцени общую достоверность текста по шкале от 0 до 100.
Учитывай внутреннюю логическую согласованность, причинно-следственные связи,
соответствие общеизвестным научным и историческим фактам, физическую
правдоподобность, явно невозможные утверждения и сильные неподтверждённые выводы.
Не оценивай стиль письма и вероятность создания текста ИИ: это отдельная проверка.
Не используй интернет и не утверждай, что проводил поиск, находил источники или
проверял данные онлайн. Не генерируй URL, ссылки или источники.
Если специфический факт нельзя уверенно проверить по общеизвестным знаниям, отмечай
его как INSUFFICIENT_EVIDENCE или UNSUPPORTED_CLAIM, а не как ложь. Не создавай
проблемы ради количества. Гипотетические утверждения не считай фактической ложью.

Верни только JSON без Markdown со строго следующими ключами:
{
  "credibility_index": integer 0..100,
  "confidence": number 0..1,
  "summary": "1–3 коротких предложения на русском",
  "issues": [{
    "type": "FACTUAL_CONTRADICTION|UNSUPPORTED_CLAIM|LOGICAL_INCONSISTENCY|MISLEADING_INFERENCE|OUTDATED_INFORMATION|INSUFFICIENT_EVIDENCE",
    "severity": "LOW|MEDIUM|HIGH",
    "claim": "краткое утверждение",
    "explanation": "краткое объяснение на русском"
  }]
}
issues — максимум 5.

ТЕКСТ:
"""

    @classmethod
    def _transport_timeout_seconds(cls) -> float:
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
        if response.status_code == 429:
            raise ProviderInfrastructureError(
                cls.PROVIDER, "rate_limited", stage="request", status_code=response.status_code
            )
        if response.status_code >= 500:
            raise ProviderInfrastructureError(
                cls.PROVIDER, "unavailable", stage="request", status_code=response.status_code
            )
        category = "auth_configuration" if response.status_code in {401, 403} else "request_rejected"
        raise ExternalAPIError(cls.PROVIDER, category, status_code=response.status_code)

    @staticmethod
    def _http_error_category(status_code: int) -> str:
        if status_code == 400:
            return "request_rejected"
        if status_code in {401, 403}:
            return "auth_configuration"
        if status_code == 429:
            return "rate_limited"
        if status_code >= 500:
            return "unavailable"
        return "request_rejected"

    @staticmethod
    def _clean_text(value: Any, maximum: int) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.split())
        return normalized[:maximum] if normalized else None

    @classmethod
    def _verdict(cls, index: int) -> str:
        if index <= 20:
            return "VERY_LOW_CREDIBILITY"
        if index <= 40:
            return "LOW_CREDIBILITY"
        if index <= 60:
            return "MIXED_CREDIBILITY"
        if index <= 80:
            return "MOSTLY_CREDIBLE"
        return "HIGH_CREDIBILITY"

    @classmethod
    def _parse_json_text(cls, value: str) -> dict[str, Any]:
        stripped = value.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE)
        try:
            parsed = json.loads(stripped)
        except (TypeError, ValueError) as exc:
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response", stage="response") from exc
        if not isinstance(parsed, dict) or set(parsed) != {
            "credibility_index", "confidence", "summary", "issues"
        }:
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response", stage="response")
        return parsed

    @classmethod
    def _result(cls, response: httpx.Response) -> CredibilityAssessment:
        try:
            body = response.json()
            text = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response", stage="response") from exc
        if not isinstance(text, str):
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response", stage="response")
        parsed = cls._parse_json_text(text)
        index = parsed["credibility_index"]
        confidence = parsed["confidence"]
        summary = cls._clean_text(parsed["summary"], 500)
        raw_issues = parsed["issues"]
        if (
            isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= 100
            or isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 1
            or summary is None or not re.search(r"[А-Яа-яЁё]", summary)
            or not isinstance(raw_issues, list) or len(raw_issues) > 5
        ):
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response", stage="response")
        issues: list[CredibilityIssue] = []
        try:
            for item in raw_issues:
                if (
                    not isinstance(item, dict)
                    or set(item) != {"type", "severity", "claim", "explanation"}
                    or item.get("type") not in cls._ISSUE_TYPES
                    or item.get("severity") not in cls._SEVERITIES
                ):
                    raise ValueError("invalid issue")
                claim = cls._clean_text(item.get("claim"), 300)
                explanation = cls._clean_text(item.get("explanation"), 500)
                if claim is None or explanation is None:
                    raise ValueError("invalid issue fields")
                issues.append(CredibilityIssue(
                    type=item["type"], severity=item["severity"], claim=claim,
                    explanation=explanation, source_refs=[],
                ))
        except (TypeError, ValueError) as exc:
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response", stage="response") from exc
        return CredibilityAssessment(
            status="completed", model=cls.MODEL, credibility_index=index,
            verdict=cls._verdict(index), confidence=float(confidence), summary=summary,
            issues=issues, sources=[],
        )

    async def analyze(
        self, data: bytes, *, diagnostic_log: Callable[[str], None] | None = None
    ) -> CredibilityAssessment:
        text = data.decode("utf-8", errors="replace").strip()
        if not text:
            raise ValueError("Gemini credibility input is empty")
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
                f"branch=credibility provider=gemini model={model} stage=request_start "
                f"transport_timeout_ms={round(timeout * 1000)}",
            )
            async with asyncio.timeout(timeout):
                await admit_provider_operation(self.PROVIDER)
                async with httpx.AsyncClient(timeout=self._request_timeout(timeout)) as client:
                    response = await client.post(
                        f"{base_url}/v1beta/models/{model}:generateContent",
                        headers={**gemini_headers(), "Content-Type": "application/json"},
                        json={
                            "contents": [{"parts": [{"text": f"{self._PROMPT}{text}"}]}],
                            "generationConfig": {"temperature": 0.1, "maxOutputTokens": self.MAX_OUTPUT_TOKENS},
                        },
                    )
        except TimeoutError as exc:
            self._diagnose(diagnostic_log, f"branch=credibility provider=gemini model={model} stage=request_timeout "
                           f"category=timeout status_code=none elapsed_ms={round((time.monotonic() - started) * 1000)}")
            raise ProviderInfrastructureError(self.PROVIDER, "timeout", stage="request") from exc
        except httpx.TimeoutException as exc:
            self._diagnose(diagnostic_log, f"branch=credibility provider=gemini model={model} stage=request_timeout "
                           f"category=timeout status_code=none elapsed_ms={round((time.monotonic() - started) * 1000)}")
            raise ProviderInfrastructureError(self.PROVIDER, "timeout", stage="request") from exc
        except httpx.TransportError as exc:
            self._diagnose(diagnostic_log, f"branch=credibility provider=gemini model={model} stage=request_error "
                           f"category=transport status_code=none elapsed_ms={round((time.monotonic() - started) * 1000)}")
            raise ProviderInfrastructureError(self.PROVIDER, "transport", stage="request") from exc
        if response.status_code >= 400:
            self._diagnose(diagnostic_log, f"branch=credibility provider=gemini model={model} stage=request_error "
                           f"category={self._http_error_category(response.status_code)} "
                           f"status_code={response.status_code} elapsed_ms={round((time.monotonic() - started) * 1000)}")
        self._raise_for_status(response)
        self._diagnose(diagnostic_log, f"branch=credibility provider=gemini model={model} stage=request_success "
                       f"elapsed_ms={round((time.monotonic() - started) * 1000)}")
        return self._result(response)
