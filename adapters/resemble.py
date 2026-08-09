"""Resemble Detect adapter — audio deepfake detection."""

import logging
import subprocess

import httpx

from adapters.base import BaseAdapter
from api.schemas import AnalysisResult
from core.config import settings
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ExternalAPIError
from src.validation import normalize_confidence

# Improved type safety
# Thread-safe operation
# Validated input parameters
logger = logging.getLogger(__name__)
MAX_CONVERTED_WAV_BYTES = 120 * 1024 * 1024


def _convert_ogg_to_wav(ogg_bytes: bytes) -> bytes:
    """Convert OGG bytes to WAV bytes using ffmpeg (in-memory, no disk I/O)."""
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
    except (FileNotFoundError, subprocess.TimeoutExpired):
        raise ExternalAPIError("ffmpeg", "audio_conversion_failed")
    
    if proc.returncode != 0:
        raise ExternalAPIError("resemble", "audio_conversion_failed")
    if len(proc.stdout) > MAX_CONVERTED_WAV_BYTES:
        raise ExternalAPIError("resemble", "audio_conversion_failed")
    return proc.stdout


class ResembleAdapter(BaseAdapter):
    URL = "https://detect.resemble.ai/api/v1/detect"

    async def analyze(self, data: bytes) -> AnalysisResult:
        # Convert OGG to WAV if needed (Telegram voice messages come as OGG)
        wav_data = data
        if data[:4] == b"OggS":
            wav_data = _convert_ogg_to_wav(data)

        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                response = await client.post(
                    self.URL,
                    headers={"Authorization": f"Token {settings.resemble_api_key}"},
                    files={"audio_file": ("audio.wav", wav_data, "audio/wav")},
                )
        except httpx.TimeoutException:
            return self._build_uncertain(
                "Resemble Detect: таймаут запроса.",
                ModelUsed.RESEMBLE,
                MediaType.AUDIO,
            )

        if response.status_code == 429:
            raise ExternalAPIError("resemble", "rate_limit")
        if response.status_code >= 500:
            raise ExternalAPIError("resemble", "server_error")
        if response.status_code >= 400:
            raise ExternalAPIError("resemble", "request_error")
        try:
            body = response.json()
        except ValueError as exc:
            raise ExternalAPIError("resemble", "invalid_response") from exc
        if not isinstance(body, dict) or body.get("success") is not True:
            raise ExternalAPIError("resemble", "invalid_response")
        try:
            score = normalize_confidence(body.get("score"))
        except ValueError as exc:
            raise ExternalAPIError("resemble", "invalid_response") from exc

        if score >= 0.75:
            verdict = Verdict.FAKE
        elif score <= 0.30:
            verdict = Verdict.REAL
        else:
            verdict = Verdict.UNCERTAIN

        explanation = f"Resemble Detect: вероятность синтетической речи {round(score * 100)}%"

        return AnalysisResult(
            verdict=verdict,
            confidence=round(score, 4),
            model_used=ModelUsed.RESEMBLE,
            explanation=explanation,
            media_type=MediaType.AUDIO,
        )
