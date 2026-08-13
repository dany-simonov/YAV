"""Single-call Gemini Google Search grounding for text credibility."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import re
import time
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from adapters.base import BaseAdapter
from api.schemas import CredibilityAssessment, CredibilityIssue, CredibilitySource
from core.config import settings
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from src.execution_deadline import bounded_timeout
from src.gemini_client import gemini_headers, safe_gemini_base_url, safe_gemini_model
from src.provider_protection import admit_provider_operation


class GeminiCredibilityAdapter(BaseAdapter):
    """Perform one bounded, grounded credibility assessment for normal text."""

    PROVIDER = "gemini"
    MODEL = "gemini_credibility_grounded"
    TOTAL_TIMEOUT_SECONDS = 13.0
    TRANSPORT_SAFETY_SECONDS = 2.0
    REQUEST_TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=3.0)
    MAX_OUTPUT_TOKENS = 900
    _ISSUE_TYPES = {
        "FACTUAL_CONTRADICTION",
        "UNSUPPORTED_CLAIM",
        "LOGICAL_INCONSISTENCY",
        "MISLEADING_INFERENCE",
        "OUTDATED_INFORMATION",
        "INSUFFICIENT_EVIDENCE",
    }
    _SEVERITIES = {"LOW", "MEDIUM", "HIGH"}
    _PROMPT = """Проверь текст на достоверность, а не на признаки AI-генерации.
Используй Google Search экономно и только для 1–5 ключевых проверяемых утверждений
(для короткого текста достаточно 1–2). Не проводи глубокое исследование, не делай
повторных поисковых проходов и не проверяй каждую мелкую деталь. Учитывай фактические
противоречия, неподтверждённые сильные утверждения, внутреннюю логику, ошибочные выводы
и устаревшие факты. Отсутствие найденного доказательства не означает ложность.
При наличии выбора отдавай приоритет официальным, научным, институциональным и крупным
авторитетным редакционным источникам.

Верни только JSON-объект без Markdown и без URL со строго следующими ключами:
{
  "credibility_index": integer 0..100,
  "confidence": number 0..1,
  "summary": "1–3 коротких предложения на русском",
  "issues": [{
    "type": "FACTUAL_CONTRADICTION|UNSUPPORTED_CLAIM|LOGICAL_INCONSISTENCY|MISLEADING_INFERENCE|OUTDATED_INFORMATION|INSUFFICIENT_EVIDENCE",
    "severity": "LOW|MEDIUM|HIGH",
    "claim": "краткое утверждение",
    "explanation": "краткое объяснение на русском",
    "source_refs": []
  }]
}
issues — максимум 5. Не придумывай проблемы ради количества. source_refs можно оставлять
пустым: ссылки для пользователя сервер получит только из metadata grounding.

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
    def _safe_source_url(cls, value: Any) -> str | None:
        if not isinstance(value, str) or len(value) > 768:
            return None
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname == "localhost" or hostname.endswith(".localhost"):
            return None
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            # Numeric host spellings (for example 2130706433 or 0x7f000001)
            # can resolve to loopback in browsers but are not parsed by
            # ipaddress.  They are never useful as public grounding sources.
            if re.fullmatch(r"[0-9.]+", hostname) or hostname.startswith("0x"):
                return None
            if hostname.isdecimal():
                try:
                    address = ipaddress.ip_address(int(hostname))
                except ValueError:
                    return None
                if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
                    return None
            return value.strip()
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            return None
        return value.strip()

    @classmethod
    def _sources_and_mapping(
        cls, candidate: dict[str, Any]
    ) -> tuple[list[CredibilitySource], dict[int, int]]:
        """Build final sources and the only trusted raw-chunk index mapping."""
        metadata = candidate.get("groundingMetadata")
        chunks = metadata.get("groundingChunks") if isinstance(metadata, dict) else None
        if not isinstance(chunks, list):
            return [], {}
        sources: list[CredibilitySource] = []
        final_by_url: dict[str, int] = {}
        raw_to_final: dict[int, int] = {}
        for raw_index, chunk in enumerate(chunks):
            web = chunk.get("web") if isinstance(chunk, dict) else None
            if not isinstance(web, dict):
                continue
            url = cls._safe_source_url(web.get("uri"))
            title = cls._clean_text(web.get("title"), 180)
            if url is not None and title is None:
                title = urlsplit(url).hostname or "Источник"
            if url is None or title is None:
                continue
            if url in final_by_url:
                raw_to_final[raw_index] = final_by_url[url]
                continue
            if len(sources) == 5:
                continue
            final_index = len(sources)
            final_by_url[url] = final_index
            raw_to_final[raw_index] = final_index
            sources.append(CredibilitySource(title=title, url=url))
        return sources, raw_to_final

    @classmethod
    def _support_refs(cls, candidate: dict[str, Any], raw_to_final: dict[int, int]) -> list[tuple[str, list[int]]]:
        """Read real grounding support segments; never use model-written source refs."""
        metadata = candidate.get("groundingMetadata")
        supports = metadata.get("groundingSupports") if isinstance(metadata, dict) else None
        if not isinstance(supports, list):
            return []
        result: list[tuple[str, list[int]]] = []
        for support in supports:
            if not isinstance(support, dict):
                continue
            segment = support.get("segment")
            segment_text = segment.get("text") if isinstance(segment, dict) else None
            normalized = cls._normalized_text(segment_text)
            raw_refs = support.get("groundingChunkIndices")
            if normalized is None or not isinstance(raw_refs, list):
                continue
            refs = sorted({
                raw_to_final[raw]
                for raw in raw_refs
                if not isinstance(raw, bool) and isinstance(raw, int) and raw in raw_to_final
            })
            if refs:
                result.append((normalized, refs))
        return result

    @staticmethod
    def _normalized_text(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.casefold().split())
        return normalized if normalized else None

    @classmethod
    def _issue_refs(
        cls, claim: str, explanation: str, supports: list[tuple[str, list[int]]]
    ) -> list[int]:
        """Associate only an exact normalized support segment with an issue."""
        claim_text = cls._normalized_text(claim)
        explanation_text = cls._normalized_text(explanation)
        refs: set[int] = set()
        for segment, support_refs in supports:
            if segment and (
                (claim_text is not None and (segment in claim_text or claim_text in segment))
                or (explanation_text is not None and (segment in explanation_text or explanation_text in segment))
            ):
                refs.update(support_refs)
        return sorted(refs)

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
            candidate = body["candidates"][0]
            text = candidate["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response", stage="response") from exc
        if not isinstance(candidate, dict) or not isinstance(text, str):
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
        sources, raw_to_final = cls._sources_and_mapping(candidate)
        supports = cls._support_refs(candidate, raw_to_final)
        issues: list[CredibilityIssue] = []
        try:
            for item in raw_issues:
                if not isinstance(item, dict) or not {"type", "severity", "claim", "explanation"}.issubset(item) \
                    or set(item) - {"type", "severity", "claim", "explanation", "source_refs"} \
                    or item.get("type") not in cls._ISSUE_TYPES or item.get("severity") not in cls._SEVERITIES:
                    raise ValueError("invalid issue")
                claim = cls._clean_text(item.get("claim"), 300)
                explanation = cls._clean_text(item.get("explanation"), 500)
                if claim is None or explanation is None:
                    raise ValueError("invalid issue fields")
                issues.append(CredibilityIssue(
                    type=item["type"], severity=item["severity"], claim=claim,
                    explanation=explanation, source_refs=cls._issue_refs(claim, explanation, supports),
                ))
        except (TypeError, ValueError) as exc:
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response", stage="response") from exc
        return CredibilityAssessment(
            status="completed", model=cls.MODEL, credibility_index=index,
            verdict=cls._verdict(index), confidence=float(confidence), summary=summary,
            issues=issues, sources=sources,
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
            self._diagnose(diagnostic_log, "branch=credibility provider=gemini stage=request_start "
                           f"transport_timeout_ms={round(timeout * 1000)}")
            async with asyncio.timeout(timeout):
                await admit_provider_operation(self.PROVIDER)
                async with httpx.AsyncClient(timeout=self._request_timeout(timeout)) as client:
                    response = await client.post(
                        f"{base_url}/v1beta/models/{model}:generateContent",
                        headers={**gemini_headers(), "Content-Type": "application/json"},
                        json={
                            "contents": [{"parts": [{"text": f"{self._PROMPT}{text}"}]}],
                            "tools": [{"google_search": {}}],
                            "generationConfig": {"temperature": 0.1, "maxOutputTokens": self.MAX_OUTPUT_TOKENS},
                        },
                    )
        except TimeoutError as exc:
            self._diagnose(diagnostic_log, "branch=credibility provider=gemini stage=request_timeout "
                           f"category=timeout status_code=none elapsed_ms={round((time.monotonic() - started) * 1000)}")
            raise ProviderInfrastructureError(self.PROVIDER, "timeout", stage="request") from exc
        except httpx.TimeoutException as exc:
            self._diagnose(diagnostic_log, "branch=credibility provider=gemini stage=request_timeout "
                           f"category=timeout status_code=none elapsed_ms={round((time.monotonic() - started) * 1000)}")
            raise ProviderInfrastructureError(self.PROVIDER, "timeout", stage="request") from exc
        except httpx.TransportError as exc:
            self._diagnose(diagnostic_log, "branch=credibility provider=gemini stage=request_error "
                           f"category=transport status_code=none elapsed_ms={round((time.monotonic() - started) * 1000)}")
            raise ProviderInfrastructureError(self.PROVIDER, "transport", stage="request") from exc
        if response.status_code >= 400:
            self._diagnose(diagnostic_log, "branch=credibility provider=gemini stage=request_error "
                           f"category={self._http_error_category(response.status_code)} "
                           f"status_code={response.status_code} elapsed_ms={round((time.monotonic() - started) * 1000)}")
        self._raise_for_status(response)
        self._diagnose(diagnostic_log, "branch=credibility provider=gemini stage=request_success "
                       f"elapsed_ms={round((time.monotonic() - started) * 1000)}")
        return self._result(response)
