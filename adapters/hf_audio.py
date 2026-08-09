"""HuggingFace audio deepfake detection — fallback adapter."""

import asyncio
import logging
import subprocess

import httpx

from adapters.base import BaseAdapter
from api.schemas import AnalysisResult
from core.config import settings
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ExternalAPIError
from src.validation import normalize_confidence

logger = logging.getLogger(__name__)
MAX_CONVERTED_WAV_BYTES = 120 * 1024 * 1024

MODEL_URL = "https://api-inference.huggingface.co/models/mo-gg/wav2vec2-large-xlsr-deepfake-detection"
MAX_RETRIES = 2
COLD_START_DELAY = 10


class HFAudioAdapter(BaseAdapter):
    async def analyze(self, data: bytes) -> AnalysisResult:
        # Ensure WAV format (convert OGG if needed)
        wav_data = data
        if data[:4] == b"OggS":
            try:
                proc = subprocess.run(
                    [
                        "ffmpeg", "-v", "error", "-nostdin", "-t", "300", "-i", "pipe:0",
                        "-ac", "2", "-ar", "96000", "-f", "wav", "-acodec", "pcm_s16le",
                        "-fs", str(MAX_CONVERTED_WAV_BYTES), "pipe:1",
                    ],
                    input=data,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                    check=False,
                )
                if proc.returncode == 0 and len(proc.stdout) <= MAX_CONVERTED_WAV_BYTES:
                    wav_data = proc.stdout
                else:
                    logger.warning("ffmpeg conversion failed, sending raw data")
            except (FileNotFoundError, subprocess.TimeoutExpired):
                raise ExternalAPIError("ffmpeg", "FFmpeg не установлен")

        headers = {"Authorization": f"Bearer {settings.hf_api_token}"}

        for attempt in range(MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                    response = await client.post(MODEL_URL, headers=headers, content=wav_data)
            except httpx.TimeoutException:
                return self._build_uncertain(
                    "HuggingFace Audio: таймаут запроса.",
                    ModelUsed.HF_AUDIO,
                    MediaType.AUDIO,
                )

            try:
                body = response.json()
            except ValueError:
                return self._build_uncertain(
                    "HuggingFace Audio: неожиданный формат ответа.", ModelUsed.HF_AUDIO, MediaType.AUDIO
                )

            if isinstance(body, dict) and body.get("error", "").startswith("Model"):
                if attempt < MAX_RETRIES:
                    logger.info("HF Audio model loading, retry in %ds...", COLD_START_DELAY)
                    await asyncio.sleep(COLD_START_DELAY)
                    continue
                return self._build_uncertain(
                    "HuggingFace Audio: модель загружается, попробуйте позже.",
                    ModelUsed.HF_AUDIO,
                    MediaType.AUDIO,
                )
            break

        if not isinstance(body, list) or not body or len(body) > 100:
            return self._build_uncertain(
                "HuggingFace Audio: неожиданный формат ответа.",
                ModelUsed.HF_AUDIO,
                MediaType.AUDIO,
            )

        # Expected labels: "spoof" (FAKE) / "bonafide" (REAL)
        candidates: list[tuple[str, float]] = []
        for item in body:
            if not isinstance(item, dict) or not isinstance(item.get("label"), str):
                continue
            try:
                candidates.append((item["label"].lower(), normalize_confidence(item.get("score"))))
            except ValueError:
                continue
        if not candidates:
            return self._build_uncertain(
                "HuggingFace Audio: неожиданный формат ответа.", ModelUsed.HF_AUDIO, MediaType.AUDIO
            )
        label, score = max(candidates, key=lambda item: item[1])

        if score > 0.7:
            if label == "spoof":
                verdict = Verdict.FAKE
            elif label == "bonafide":
                verdict = Verdict.REAL
            else:
                verdict = Verdict.UNCERTAIN
        else:
            verdict = Verdict.UNCERTAIN

        explanation = f"HuggingFace Audio: {label} с уверенностью {round(score * 100)}%"

        return AnalysisResult(
            verdict=verdict,
            confidence=round(score, 4),
            model_used=ModelUsed.HF_AUDIO,
            explanation=explanation,
            media_type=MediaType.AUDIO,
        )
