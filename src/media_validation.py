"""Bounded media inspection using the existing FFmpeg runtime."""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from typing import Any

from core.enums import MediaType
from src.validation import MAX_FILE_BYTES, SecurityValidationError


PROBE_TIMEOUT_SECONDS = 5
DECODE_TIMEOUT_SECONDS = 8
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_DIMENSION = 8_192
MAX_AUDIO_DURATION_SECONDS = 300
MAX_AUDIO_CHANNELS = 2
MAX_AUDIO_SAMPLE_RATE = 96_000
MAX_VIDEO_DURATION_SECONDS = 60
MAX_VIDEO_WIDTH = 1_920
MAX_VIDEO_HEIGHT = 1_080
MAX_VIDEO_FRAMES = 60


@dataclass(frozen=True)
class MediaInfo:
    media_type: MediaType
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    channels: int | None = None
    sample_rate: int | None = None


def detect_signature(data: bytes) -> MediaType:
    if data.startswith(b"\xff\xd8\xff"):
        return MediaType.IMAGE
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return MediaType.IMAGE
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return MediaType.IMAGE
    if data.startswith(b"OggS") or data.startswith(b"ID3") or data[:2] == b"\xff\xfb":
        return MediaType.AUDIO
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return MediaType.AUDIO
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return MediaType.VIDEO
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12].lower()
        if brand in {b"m4a ", b"m4b ", b"m4p "}:
            return MediaType.AUDIO
        if brand in {b"isom", b"iso2", b"avc1", b"mp41", b"mp42", b"qt  "}:
            return MediaType.VIDEO
    raise SecurityValidationError("unsupported_media_type", "Неподдерживаемый формат файла.", 415)


def _run_probe(data: bytes) -> dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-nostdin",
        "-show_entries",
        "format=format_name,duration:stream=codec_type,codec_name,width,height,channels,sample_rate",
        "-of",
        "json",
        "-i",
        "pipe:0",
    ]
    try:
        completed = subprocess.run(
            command,
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise SecurityValidationError("invalid_media", "Не удалось проверить медиафайл.", 422) from exc
    if completed.returncode != 0 or len(completed.stdout) > 64 * 1024:
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)
    try:
        result = json.loads(completed.stdout)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422) from exc
    if not isinstance(result, dict):
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)
    return result


def _as_positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422) from exc
    if parsed <= 0:
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)
    return parsed


def _as_duration(value: Any) -> float:
    try:
        duration = float(value)
    except (TypeError, ValueError) as exc:
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422) from exc
    if not math.isfinite(duration) or duration < 0:
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)
    return duration


def _first_stream(probe: dict[str, Any], stream_type: str) -> dict[str, Any]:
    streams = probe.get("streams")
    if not isinstance(streams, list):
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)
    for stream in streams:
        if isinstance(stream, dict) and stream.get("codec_type") == stream_type:
            return stream
    raise SecurityValidationError("invalid_media", "Файл не содержит ожидаемый медиа-поток.", 422)


def _require_codec(stream: dict[str, Any], allowed: set[str]) -> None:
    codec = stream.get("codec_name")
    if not isinstance(codec, str) or codec.lower() not in allowed:
        raise SecurityValidationError("unsupported_media_type", "Неподдерживаемый формат файла.", 415)


def _validate_image_decode(data: bytes) -> None:
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-i",
        "pipe:0",
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(
            command,
            input=data,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=DECODE_TIMEOUT_SECONDS,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise SecurityValidationError("invalid_media", "Не удалось проверить изображение.", 422) from exc
    if completed.returncode != 0:
        raise SecurityValidationError("invalid_media", "Изображение повреждено или некорректно.", 422)


def validate_media_bytes(data: bytes, expected: MediaType | None = None) -> MediaInfo:
    if not data:
        raise SecurityValidationError("invalid_media", "Файл пустой.", 422)
    if len(data) > MAX_FILE_BYTES:
        raise SecurityValidationError("file_too_large", "Файл превышает лимит в 20 MiB.", 413)

    actual = detect_signature(data)
    if expected is not None and expected != actual:
        raise SecurityValidationError(
            "media_type_mismatch", "Содержимое файла не соответствует заявленному формату.", 415
        )

    probe = _run_probe(data)
    if actual == MediaType.IMAGE:
        stream = _first_stream(probe, "video")
        _require_codec(stream, {"mjpeg", "png", "webp"})
        width = _as_positive_int(stream.get("width"))
        height = _as_positive_int(stream.get("height"))
        if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION or width * height > MAX_IMAGE_PIXELS:
            raise SecurityValidationError("media_limits_exceeded", "Размер изображения превышает лимит.", 422)
        _validate_image_decode(data)
        return MediaInfo(actual, width=width, height=height)

    format_data = probe.get("format")
    if not isinstance(format_data, dict):
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)
    duration = _as_duration(format_data.get("duration"))

    if actual == MediaType.AUDIO:
        stream = _first_stream(probe, "audio")
        _require_codec(
            stream,
            {"mp3", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_u8", "opus", "vorbis", "aac", "alac"},
        )
        channels = _as_positive_int(stream.get("channels"))
        sample_rate = _as_positive_int(stream.get("sample_rate"))
        if duration > MAX_AUDIO_DURATION_SECONDS or channels > MAX_AUDIO_CHANNELS or sample_rate > MAX_AUDIO_SAMPLE_RATE:
            raise SecurityValidationError("media_limits_exceeded", "Параметры аудио превышают лимит.", 422)
        return MediaInfo(actual, duration=duration, channels=channels, sample_rate=sample_rate)

    stream = _first_stream(probe, "video")
    _require_codec(stream, {"h264", "hevc", "mpeg4", "mjpeg"})
    width = _as_positive_int(stream.get("width"))
    height = _as_positive_int(stream.get("height"))
    if duration > MAX_VIDEO_DURATION_SECONDS or width > MAX_VIDEO_WIDTH or height > MAX_VIDEO_HEIGHT:
        raise SecurityValidationError("media_limits_exceeded", "Параметры видео превышают лимит.", 422)
    return MediaInfo(actual, duration=duration, width=width, height=height)
