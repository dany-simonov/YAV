"""Bounded video analysis pipeline using FFmpeg over stdin only."""

import asyncio
import subprocess

from adapters.base import BaseAdapter
from api.schemas import AnalysisResult
from core.config import settings
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ExternalAPIError, FileTooLarge, VideoTooLong
from src.media_validation import MAX_VIDEO_FRAMES, validate_media_bytes
from src.validation import MAX_FILE_BYTES, SecurityValidationError


CONCURRENT_LIMIT = 5
FFMPEG_TIMEOUT_SECONDS = 20
MAX_FRAME_OUTPUT_BYTES = 64 * 1024 * 1024


def _get_duration(video_bytes: bytes) -> float:
    """Validate the container fail-closed and return its bounded duration."""
    info = validate_media_bytes(video_bytes, MediaType.VIDEO)
    if info.duration is None:
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)
    return info.duration


def _extract_frames(video_bytes: bytes) -> list[bytes]:
    """Extract at most 60 JPEG frames with a process and output-size bound."""
    sample_rate = max(1, int(settings.video_frame_sample_rate))
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-i",
        "pipe:0",
        "-vf",
        f"fps={sample_rate}",
        "-frames:v",
        str(MAX_VIDEO_FRAMES),
        "-fs",
        str(MAX_FRAME_OUTPUT_BYTES),
        "-f",
        "image2",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    try:
        completed = subprocess.run(
            command,
            input=video_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise SecurityValidationError("invalid_media", "Не удалось обработать видео.", 422) from exc
    if completed.returncode != 0 or len(completed.stdout) > MAX_FRAME_OUTPUT_BYTES:
        raise SecurityValidationError("invalid_media", "Не удалось обработать видео.", 422)

    frames: list[bytes] = []
    start = 0
    while len(frames) < MAX_VIDEO_FRAMES:
        beginning = completed.stdout.find(b"\xff\xd8", start)
        if beginning == -1:
            break
        end = completed.stdout.find(b"\xff\xd9", beginning + 2)
        if end == -1:
            break
        frames.append(completed.stdout[beginning : end + 2])
        start = end + 2
    return frames


class VideoPipeline(BaseAdapter):
    async def analyze(self, data: bytes) -> AnalysisResult:
        if len(data) > MAX_FILE_BYTES:
            raise FileTooLarge("Видеофайл слишком большой.")

        duration = _get_duration(data)
        if duration > settings.max_video_duration_seconds:
            raise VideoTooLong("Видео слишком длинное.")

        frames = _extract_frames(data)
        if not frames:
            return self._build_uncertain(
                "Не удалось извлечь кадры из видео.",
                ModelUsed.SIGHTENGINE_VIDEO,
                MediaType.VIDEO,
            )

        from adapters.hf_image import HFImageAdapter
        from adapters.sightengine import SightengineAdapter

        semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)
        sightengine_adapter = SightengineAdapter()
        hf_adapter = HFImageAdapter()
        use_hf_fallback = False

        try:
            await sightengine_adapter.analyze(frames[0])
        except ExternalAPIError as exc:
            if exc.detail in {"rate_limit", "server_error"}:
                use_hf_fallback = True
            else:
                raise

        active_adapter = hf_adapter if use_hf_fallback else sightengine_adapter
        model_used = ModelUsed.HF_IMAGE if use_hf_fallback else ModelUsed.SIGHTENGINE_VIDEO

        async def _analyze_frame(frame_bytes: bytes) -> float | None:
            async with semaphore:
                try:
                    result = await active_adapter.analyze(frame_bytes)
                    if result.verdict == Verdict.REAL:
                        return 1.0 - result.confidence
                    if result.verdict == Verdict.FAKE:
                        return result.confidence
                    return 0.5
                except ExternalAPIError:
                    return None

        raw_scores = await asyncio.gather(*(_analyze_frame(frame) for frame in frames))
        valid_scores = [score for score in raw_scores if score is not None]
        if not valid_scores:
            return self._build_uncertain(
                "Не удалось проанализировать кадры видео.", model_used, MediaType.VIDEO
            )

        total_frames = len(valid_scores)
        fake_count = sum(1 for score in valid_scores if score >= 0.75)
        real_count = sum(1 for score in valid_scores if score <= 0.35)
        fake_ratio = fake_count / total_frames
        fake_scores = [score for score in valid_scores if score >= 0.75]
        real_scores = [score for score in valid_scores if score <= 0.35]

        if fake_ratio >= 0.40:
            verdict = Verdict.FAKE
            confidence = sum(fake_scores) / len(fake_scores)
        elif fake_ratio <= 0.10:
            verdict = Verdict.REAL
            confidence = 1.0 - (sum(real_scores) / len(real_scores) if real_scores else 0.15)
        else:
            verdict = Verdict.UNCERTAIN
            confidence = 0.5

        fallback_note = " (использован HuggingFace как резервный)" if use_hf_fallback else ""
        explanation = (
            f"Видео-анализ{fallback_note}: {total_frames} кадров проверено. "
            f"Подозрительных: {fake_count}, подлинных: {real_count}. "
            f"Доля подозрительных: {round(fake_ratio * 100)}%."
        )
        return AnalysisResult(
            verdict=verdict,
            confidence=round(confidence, 4),
            model_used=model_used,
            explanation=explanation,
            media_type=MediaType.VIDEO,
        )
