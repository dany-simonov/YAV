"""Gemini File API adapter for prevalidated YAV video files."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from typing import Any

import httpx

from adapters.base import BaseAdapter
from api.schemas import AnalysisResult, ProviderEvidence
from core.config import settings
from core.enums import MediaType, ModelUsed, ScoreKind, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from core.result_normalization import canonicalize_result
from src.execution_deadline import ExecutionDeadlineExceeded, bounded_timeout
from src.gemini_client import gemini_headers, safe_gemini_base_url, safe_gemini_model
from src.provider_protection import admit_provider_operation


class GeminiVideoAdapter(BaseAdapter):
    """Analyze a validated video with Gemini's temporary File API resource."""

    PROVIDER = "gemini"
    MODEL = "video_verification"
    TOTAL_TIMEOUT_SECONDS = 20.0
    POLL_INTERVAL_SECONDS = 0.75
    MAX_POLL_REQUESTS = 6
    REQUEST_TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=3.0)
    _FILE_NAME = re.compile(r"files/[a-z0-9-]{1,40}")
    _SUMMARY_PREFIX = re.compile(
        r"^(?:gemini\s+video\s+verification\s*(?::|—)|gemini\s*:)\s*",
        re.IGNORECASE,
    )
    _VIDEO_MIME_TYPES = {"video/mp4", "video/avi", "video/quicktime"}
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
        "Assess only visible and audible indicators of synthetic generation or manipulation in "
        "this video. Return JSON matching the supplied schema. authenticity_index means 0 is "
        "very low estimated authenticity and 100 is high estimated authenticity. confidence is "
        "your classification confidence. Use UNCERTAIN whenever the evidence is insufficient. "
        "reasoning_summary must be clear, natural Russian in one to three concise sentences, "
        "with no Markdown, JSON field names, or technical API/provider details. Keep it factual, "
        "safe, and under 300 characters. Do not begin reasoning_summary with Gemini Video "
        "Verification:, Gemini:, a model name, a provider name, or any technical prefix."
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

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except (TypeError, ValueError) as exc:
            raise ProviderInfrastructureError("gemini", "invalid_response") from exc
        if not isinstance(body, dict):
            raise ProviderInfrastructureError("gemini", "invalid_response")
        return body

    @classmethod
    def _raise_for_status(cls, response: httpx.Response, operation: str) -> None:
        if response.status_code < 400:
            return
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderInfrastructureError(cls.PROVIDER, "unavailable")
        raise ExternalAPIError(cls.PROVIDER, "request_error", status_code=response.status_code, operation=operation)

    async def _request(self, operation: str, method: str, client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        try:
            await admit_provider_operation(self.PROVIDER)
            response = await getattr(client, method)(url, timeout=self._request_timeout(), **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderInfrastructureError(self.PROVIDER, "timeout") from exc
        except httpx.TransportError as exc:
            raise ProviderInfrastructureError(self.PROVIDER, "transport") from exc
        self._raise_for_status(response, operation)
        return response

    @classmethod
    def _file_details(cls, body: dict[str, Any], mime_type: str) -> tuple[str, str, str]:
        # Upload responses wrap the resource in ``file``; files.get returns
        # the File resource itself.  Both are server responses from Gemini.
        file = body.get("file", body)
        if not isinstance(file, dict):
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response")
        name, uri, state = file.get("name"), file.get("uri"), file.get("state")
        returned_mime = file.get("mimeType")
        if (
            not isinstance(name, str)
            or not cls._FILE_NAME.fullmatch(name)
            or not isinstance(uri, str)
            or not cls._is_https_url(uri)
            or not isinstance(state, str)
            or returned_mime != mime_type
        ):
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response")
        return name, uri, state

    @staticmethod
    def _is_https_url(value: str) -> bool:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        return bool(
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        )

    async def _wait_until_active(
        self, client: httpx.AsyncClient, base_url: str, file_name: str, mime_type: str, deadline: float
    ) -> str:
        for _ in range(self.MAX_POLL_REQUESTS):
            if time.monotonic() >= deadline:
                raise ProviderInfrastructureError(self.PROVIDER, "processing_timeout")
            response = await self._request(
                "files_poll", "get", client, f"{base_url}/v1beta/{file_name}", headers=gemini_headers()
            )
            name, uri, state = self._file_details(self._json_object(response), mime_type)
            if name != file_name:
                raise ProviderInfrastructureError(self.PROVIDER, "invalid_response")
            if state == "ACTIVE":
                return uri
            if state != "PROCESSING":
                raise ProviderInfrastructureError(self.PROVIDER, "invalid_response")
            if time.monotonic() + self.POLL_INTERVAL_SECONDS >= deadline:
                raise ProviderInfrastructureError(self.PROVIDER, "processing_timeout")
            await asyncio.sleep(min(self.POLL_INTERVAL_SECONDS, deadline - time.monotonic()))
        raise ProviderInfrastructureError(self.PROVIDER, "processing_timeout")

    @classmethod
    def _sanitize_summary(cls, summary: str) -> str:
        """Remove only known accidental Gemini labels at the beginning."""
        return cls._SUMMARY_PREFIX.sub("", summary, count=1).strip()

    @classmethod
    def _result(cls, response: httpx.Response, model: str) -> AnalysisResult:
        body = cls._json_object(response)
        try:
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response") from exc
        if not isinstance(parsed, dict) or set(parsed) != {
            "verdict", "authenticity_index", "confidence", "reasoning_summary"
        }:
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response")
        verdict_value = parsed["verdict"]
        index, confidence, summary = (
            parsed["authenticity_index"], parsed["confidence"], parsed["reasoning_summary"]
        )
        if (
            verdict_value not in {item.value for item in Verdict}
            or isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index <= 100
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
            or not isinstance(summary, str)
        ):
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response")
        summary = cls._sanitize_summary(" ".join(summary.split()))
        if not summary or len(summary) > 300:
            raise ProviderInfrastructureError(cls.PROVIDER, "invalid_response")
        verdict = Verdict(verdict_value)
        return canonicalize_result(
            AnalysisResult(
                verdict=verdict,
                confidence=float(confidence),
                model_used=ModelUsed.GEMINI_VIDEO,
                explanation=summary,
                media_type=MediaType.VIDEO,
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

    async def _cleanup(self, client: httpx.AsyncClient, base_url: str, file_name: str) -> None:
        try:
            await client.delete(
                f"{base_url}/v1beta/{file_name}", headers=gemini_headers(), timeout=self._request_timeout()
            )
        except (ExecutionDeadlineExceeded, httpx.HTTPError, ProviderInfrastructureError):
            pass

    async def analyze(self, data: bytes, *, mime_type: str = "video/mp4") -> AnalysisResult:
        if not settings.gemini_api_key:
            raise ProviderInfrastructureError(self.PROVIDER, "missing_credentials")
        model, base_url = safe_gemini_model(), safe_gemini_base_url()
        if model == "invalid-model" or base_url is None or mime_type not in self._VIDEO_MIME_TYPES:
            raise ProviderInfrastructureError(self.PROVIDER, "invalid_configuration")
        try:
            timeout = bounded_timeout(self.TOTAL_TIMEOUT_SECONDS)
            async with asyncio.timeout(timeout):
                deadline = time.monotonic() + timeout
                async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT) as client:
                    start = await self._request(
                        "files_start", "post",
                        client,
                        f"{base_url}/upload/v1beta/files",
                        headers={
                            **gemini_headers(),
                            "X-Goog-Upload-Protocol": "resumable",
                            "X-Goog-Upload-Command": "start",
                            "X-Goog-Upload-Header-Content-Length": str(len(data)),
                            "X-Goog-Upload-Header-Content-Type": mime_type,
                            "Content-Type": "application/json",
                        },
                        json={"file": {"display_name": "yav-video"}},
                    )
                    upload_url = start.headers.get("x-goog-upload-url")
                    if not isinstance(upload_url, str) or not self._is_https_url(upload_url):
                        raise ProviderInfrastructureError(self.PROVIDER, "invalid_response")
                    uploaded = await self._request(
                        "upload_finalize", "post",
                        client,
                        upload_url,
                        headers={
                            **gemini_headers(),
                            "Content-Length": str(len(data)),
                            "X-Goog-Upload-Offset": "0",
                            "X-Goog-Upload-Command": "upload, finalize",
                        },
                        content=data,
                    )
                    file_name, file_uri, state = self._file_details(self._json_object(uploaded), mime_type)
                    try:
                        if state == "PROCESSING":
                            file_uri = await self._wait_until_active(client, base_url, file_name, mime_type, deadline)
                        elif state != "ACTIVE":
                            raise ProviderInfrastructureError(self.PROVIDER, "invalid_response")
                        generated = await self._request(
                            "generate_content", "post",
                            client,
                            f"{base_url}/v1beta/models/{model}:generateContent",
                            headers={**gemini_headers(), "Content-Type": "application/json"},
                            json={
                                "contents": [{"parts": [
                                    {"fileData": {"mimeType": mime_type, "fileUri": file_uri}},
                                    {"text": self._PROMPT},
                                ]}],
                                "generationConfig": {
                                    "responseMimeType": "application/json",
                                    "responseJsonSchema": self._RESPONSE_SCHEMA,
                                },
                            },
                        )
                        return self._result(generated, model)
                    finally:
                        await self._cleanup(client, base_url, file_name)
        except TimeoutError as exc:
            raise ProviderInfrastructureError(self.PROVIDER, "processing_timeout") from exc
