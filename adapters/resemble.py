"""Resemble Detect v2 adapter with bounded polling for terminal results."""

from __future__ import annotations

import asyncio
import subprocess
from typing import Any, Mapping

import httpx

from adapters.base import BaseAdapter
from api.schemas import AnalysisResult, ProviderEvidence
from core.config import settings
from core.enums import MediaType, ModelUsed, ScoreKind, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from core.result_normalization import canonicalize_result
from src.provider_protection import admit_provider_operation
from src.validation import normalize_confidence

MAX_CONVERTED_WAV_BYTES = 120 * 1024 * 1024


def _convert_ogg_to_wav(ogg_bytes: bytes) -> bytes:
    """Convert OGG bytes to WAV bytes in-memory for direct-file Detect upload."""
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-nostdin", "-t", "300", "-i", "pipe:0",
                "-ac", "2", "-ar", "96000", "-f", "wav", "-acodec", "pcm_s16le",
                "-fs", str(MAX_CONVERTED_WAV_BYTES), "pipe:1",
            ],
            input=ogg_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ExternalAPIError("resemble", "audio_conversion_failed") from exc
    if proc.returncode != 0 or len(proc.stdout) > MAX_CONVERTED_WAV_BYTES:
        raise ExternalAPIError("resemble", "audio_conversion_failed")
    return proc.stdout


class ResembleAdapter(BaseAdapter):
    """Submit audio to Detect v2 and never treat `processing` as final."""

    URL = "https://app.resemble.ai/api/v2/detect"
    POLL_INTERVAL_SECONDS = 1.0
    MAX_POLLS = 30

    @staticmethod
    def _headers() -> dict[str, str]:
        return {"Authorization": f"Bearer {settings.resemble_api_key}"}

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code in (401, 403):
            raise ExternalAPIError("resemble", "auth_error", status_code=response.status_code)
        if response.status_code == 429:
            raise ExternalAPIError("resemble", "rate_limit", status_code=429)
        if response.status_code >= 500:
            raise ProviderInfrastructureError(
                "resemble", "unavailable", stage="request", status_code=response.status_code
            )
        if response.status_code >= 400:
            raise ExternalAPIError("resemble", "request_error", status_code=response.status_code)

    @staticmethod
    def _item(response: httpx.Response) -> Mapping[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderInfrastructureError("resemble", "invalid_response", stage="response") from exc
        item = body.get("item") if isinstance(body, dict) and body.get("success") is True else None
        if not isinstance(item, Mapping):
            raise ProviderInfrastructureError("resemble", "invalid_response", stage="response")
        return item

    @staticmethod
    def _metric(item: Mapping[str, Any]) -> tuple[float, str, str]:
        media_type = item.get("media_type")
        if media_type == "image":
            section, field, prefix = item.get("image_metrics"), "score", "image_metrics"
        elif media_type == "video":
            section, field, prefix = item.get("video_metrics"), "score", "video_metrics"
        else:
            section, field, prefix = item.get("metrics"), "aggregated_score", "metrics"
        if not isinstance(section, Mapping):
            raise ProviderInfrastructureError("resemble", "invalid_response", stage="response")
        try:
            score = normalize_confidence(section.get(field))
        except ValueError as exc:
            raise ProviderInfrastructureError("resemble", "invalid_response", stage="response") from exc
        label = section.get("label")
        if not isinstance(label, str) or label.strip().lower() not in {"fake", "real", "uncertain"}:
            raise ProviderInfrastructureError("resemble", "invalid_response", stage="response")
        return score, label.strip().lower(), f"{prefix}.{field}"

    async def _wait_for_terminal(self, client: httpx.AsyncClient, item: Mapping[str, Any]) -> Mapping[str, Any]:
        detection_id = item.get("uuid")
        if not isinstance(detection_id, str) or not detection_id:
            raise ProviderInfrastructureError("resemble", "invalid_response", stage="response")
        for attempt in range(self.MAX_POLLS + 1):
            status = item.get("status")
            if status == "completed":
                return item
            if status in {"failed", "error", "cancelled"}:
                raise ProviderInfrastructureError("resemble", "unavailable", stage="response")
            if status != "processing":
                raise ProviderInfrastructureError("resemble", "invalid_response", stage="response")
            if attempt == self.MAX_POLLS:
                break
            await asyncio.sleep(self.POLL_INTERVAL_SECONDS)
            try:
                response = await client.get(f"{self.URL}/{detection_id}", headers=self._headers())
            except httpx.TimeoutException as exc:
                raise ProviderInfrastructureError("resemble", "timeout", stage="request") from exc
            except httpx.TransportError as exc:
                raise ProviderInfrastructureError("resemble", "transport", stage="request") from exc
            self._raise_for_status(response)
            item = self._item(response)
        raise ProviderInfrastructureError("resemble", "processing_timeout", stage="response")

    async def analyze(self, data: bytes) -> AnalysisResult:
        if not settings.resemble_api_key:
            raise ProviderInfrastructureError(
                "resemble", "config", stage="config", reason="api_key_missing"
            )
        wav_data = _convert_ogg_to_wav(data) if data[:4] == b"OggS" else data
        try:
            await admit_provider_operation("resemble")
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                response = await client.post(
                    self.URL,
                    headers=self._headers(),
                    files={"file": ("audio.wav", wav_data, "audio/wav")},
                )
                self._raise_for_status(response)
                item = await self._wait_for_terminal(client, self._item(response))
        except httpx.TimeoutException as exc:
            raise ProviderInfrastructureError("resemble", "timeout", stage="request") from exc
        except httpx.TransportError as exc:
            raise ProviderInfrastructureError("resemble", "transport", stage="request") from exc

        score, label, score_field = self._metric(item)
        verdict = {"fake": Verdict.FAKE, "real": Verdict.REAL, "uncertain": Verdict.UNCERTAIN}[label]
        return canonicalize_result(
            AnalysisResult(
                verdict=verdict,
                confidence=round(score, 4),
                model_used=ModelUsed.RESEMBLE,
                explanation=f"Resemble Detect: {label}, score {round(score * 100)}%.",
                media_type=MediaType.AUDIO,
            ),
            ProviderEvidence(
                provider="resemble",
                model="detect_v2",
                raw_score=score,
                score_kind=ScoreKind.AGGREGATED_SIGNAL,
                predicted_label=label,
                safe_details={"score_field": score_field, "detect_status": "completed"},
            ),
        )
