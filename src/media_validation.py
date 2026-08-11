"""Bounded image decoding and FFprobe-based audio inspection."""

from __future__ import annotations

import json
import math
import subprocess

from dataclasses import dataclass
from io import BytesIO
from typing import Any, Callable

from PIL import Image, UnidentifiedImageError

from core.enums import MediaType
from src.validation import MAX_FILE_BYTES, SecurityValidationError


PROBE_TIMEOUT_SECONDS = 5
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_DIMENSION = 8_192
MAX_AUDIO_DURATION_SECONDS = 300
MAX_AUDIO_CHANNELS = 2
MAX_AUDIO_SAMPLE_RATE = 96_000
# Retained for the isolated legacy VideoPipeline module; the production VIDEO
# route does not use these limits or invoke FFprobe.
MAX_VIDEO_DURATION_SECONDS = 60
MAX_VIDEO_WIDTH = 1_920
MAX_VIDEO_HEIGHT = 1_080
MAX_VIDEO_FRAMES = 60

_ISO_BMFF_AUDIO_BRANDS = {b"m4a ", b"m4b ", b"m4p ", b"f4a "}
_ISO_BMFF_VIDEO_BRANDS = {
    b"isom",
    b"iso2",
    b"iso3",
    b"iso4",
    b"iso5",
    b"iso6",
    b"iso7",
    b"iso8",
    b"iso9",
    b"avc1",
    b"cmfc",
    b"cmfs",
    b"dash",
    b"f4v ",
    b"m4v ",
    b"mj2s",
    b"mp41",
    b"mp42",
    b"msdh",
    b"msix",
    b"qt  ",
}
_ISO_BMFF_PROBE_FORMATS = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}

DiagnosticLog = Callable[[str], None]


def _diagnose(diagnostic_log: DiagnosticLog | None, message: str) -> None:
    if diagnostic_log is not None:
        diagnostic_log(message)

@dataclass(frozen=True)
class MediaInfo:
    media_type: MediaType
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    channels: int | None = None
    sample_rate: int | None = None


def _detect_iso_base_media_signature(data: bytes) -> MediaType | None:
    """Recognize a structurally valid leading ISO Base Media ``ftyp`` box.

    ``ftyp`` is a box type at offset 4, not a file magic value at offset 0.
    Check the complete declared first box and known major/compatible brands so a
    coincidental ``ftyp`` substring cannot identify arbitrary bytes as media.
    """
    if len(data) < 16 or data[4:8] != b"ftyp":
        return None

    box_size = int.from_bytes(data[:4], "big")
    header_size = 8
    if box_size == 1:
        if len(data) < 24:
            return None
        box_size = int.from_bytes(data[8:16], "big")
        header_size = 16
    if box_size < header_size + 8 or box_size > len(data) or (box_size - header_size - 8) % 4:
        return None

    major_brand_offset = header_size
    brands = {
        data[major_brand_offset : major_brand_offset + 4].lower(),
        *(
            data[offset : offset + 4].lower()
            for offset in range(major_brand_offset + 8, box_size, 4)
        ),
    }
    major_brand = data[major_brand_offset : major_brand_offset + 4].lower()
    if major_brand in _ISO_BMFF_AUDIO_BRANDS:
        return MediaType.AUDIO
    if major_brand in _ISO_BMFF_VIDEO_BRANDS:
        return MediaType.VIDEO
    if brands & _ISO_BMFF_VIDEO_BRANDS:
        return MediaType.VIDEO
    if brands & _ISO_BMFF_AUDIO_BRANDS:
        return MediaType.AUDIO
    return None


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
    iso_base_media_type = _detect_iso_base_media_signature(data)
    if iso_base_media_type is not None:
        return iso_base_media_type
    raise SecurityValidationError("unsupported_media_type", "Неподдерживаемый формат файла.", 415)


def _run_probe(data: bytes, diagnostic_log: DiagnosticLog | None = None) -> dict[str, Any]:
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
    _diagnose(diagnostic_log, "media_validation stage=ffprobe result=start")
    try:
        completed = subprocess.run(
            command,
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        _diagnose(diagnostic_log, "media_validation stage=ffprobe result=failed reason=binary_missing")
        raise SecurityValidationError("invalid_media", "Не удалось проверить медиафайл.", 422) from exc
    except subprocess.TimeoutExpired as exc:
        _diagnose(diagnostic_log, "media_validation stage=ffprobe result=failed reason=timeout")
        raise SecurityValidationError("invalid_media", "Не удалось проверить медиафайл.", 422) from exc
    if completed.returncode != 0 or len(completed.stdout) > 64 * 1024:
        _diagnose(diagnostic_log, "media_validation stage=ffprobe result=failed reason=nonzero_exit")
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)
    try:
        result = json.loads(completed.stdout)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        _diagnose(diagnostic_log, "media_validation stage=ffprobe result=failed reason=invalid_json")
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422) from exc
    if not isinstance(result, dict):
        _diagnose(diagnostic_log, "media_validation stage=ffprobe result=failed reason=invalid_json")
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)
    _diagnose(diagnostic_log, "media_validation stage=ffprobe result=ok")
    return result


def _as_positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422) from exc
    if parsed <= 0:
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)
    return parsed


def _as_duration(value: Any, diagnostic_log: DiagnosticLog | None = None) -> float:
    if value is None:
        _diagnose(diagnostic_log, "media_validation stage=ffprobe result=failed reason=missing_duration")
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)
    try:
        duration = float(value)
    except (TypeError, ValueError) as exc:
        _diagnose(diagnostic_log, "media_validation stage=ffprobe result=failed reason=invalid_duration")
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422) from exc
    if not math.isfinite(duration) or duration < 0:
        _diagnose(diagnostic_log, "media_validation stage=ffprobe result=failed reason=invalid_duration")
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)
    return duration


def _first_stream(
    probe: dict[str, Any], stream_type: str, diagnostic_log: DiagnosticLog | None = None
) -> dict[str, Any]:
    streams = probe.get("streams")
    if not isinstance(streams, list):
        _diagnose(diagnostic_log, "media_validation stage=ffprobe result=failed reason=no_stream")
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)
    for stream in streams:
        if isinstance(stream, dict) and stream.get("codec_type") == stream_type:
            return stream
    _diagnose(diagnostic_log, "media_validation stage=ffprobe result=failed reason=no_stream")
    raise SecurityValidationError("invalid_media", "Файл не содержит ожидаемый медиа-поток.", 422)


def _require_codec(stream: dict[str, Any], allowed: set[str]) -> None:
    codec = stream.get("codec_name")
    if not isinstance(codec, str) or codec.lower() not in allowed:
        raise SecurityValidationError("unsupported_media_type", "Неподдерживаемый формат файла.", 415)


def _require_container_format(
    probe: dict[str, Any], allowed: set[str], diagnostic_log: DiagnosticLog | None = None
) -> None:
    format_data = probe.get("format")
    if not isinstance(format_data, dict):
        _diagnose(diagnostic_log, "media_validation stage=ffprobe result=failed reason=unsupported_container")
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)
    format_name = format_data.get("format_name")
    if not isinstance(format_name, str):
        _diagnose(diagnostic_log, "media_validation stage=ffprobe result=failed reason=unsupported_container")
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)
    formats = {item.strip().lower() for item in format_name.split(",")}
    if not formats & allowed:
        _diagnose(diagnostic_log, "media_validation stage=ffprobe result=failed reason=unsupported_container")
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)


def _validate_image_decode(data: bytes, diagnostic_log: DiagnosticLog | None = None) -> tuple[int, int]:
    """Decode a supported image in-process after enforcing pixel bounds."""
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise SecurityValidationError("unsupported_media_type", "Неподдерживаемый формат файла.", 415)
            width, height = image.size
            if (
                width <= 0
                or height <= 0
                or width > MAX_IMAGE_DIMENSION
                or height > MAX_IMAGE_DIMENSION
                or width * height > MAX_IMAGE_PIXELS
            ):
                raise SecurityValidationError("media_limits_exceeded", "Размер изображения превышает лимит.", 422)
            image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        _diagnose(diagnostic_log, "media_validation stage=image_decode result=failed")
        raise SecurityValidationError("invalid_media", "Изображение повреждено или некорректно.", 422)
    _diagnose(diagnostic_log, "media_validation stage=image_decode result=ok")
    return width, height


def validate_media_bytes(
    data: bytes, expected: MediaType | None = None, diagnostic_log: DiagnosticLog | None = None
) -> MediaInfo:
    if not data:
        raise SecurityValidationError("invalid_media", "Файл пустой.", 422)
    if len(data) > MAX_FILE_BYTES:
        raise SecurityValidationError("file_too_large", "Файл превышает лимит в 20 MiB.", 413)

    actual = detect_signature(data)
    _diagnose(diagnostic_log, f"media_validation stage=signature result=ok detected={actual.value}")
    if expected is not None and expected != actual:
        raise SecurityValidationError(
            "media_type_mismatch", "Содержимое файла не соответствует заявленному формату.", 415
        )

    if actual == MediaType.IMAGE:
        width, height = _validate_image_decode(data, diagnostic_log)
        _diagnose(diagnostic_log, "media_validation stage=limits result=ok")
        return MediaInfo(actual, width=width, height=height)

    if actual == MediaType.VIDEO:
        # Appwrite Cloud does not ship FFmpeg.  Keep the fail-closed byte-size,
        # structural signature, and declared-type checks above, then let the
        # direct video provider inspect container/codec/duration details.
        _diagnose(diagnostic_log, "media_validation stage=video_base_validation result=ok")
        return MediaInfo(actual)

    # Audio remains the only media type that uses the local FFprobe path.
    probe = _run_probe(data, diagnostic_log)
    format_data = probe.get("format")
    if not isinstance(format_data, dict):
        _diagnose(diagnostic_log, "media_validation stage=ffprobe result=failed reason=unknown_probe_failure")
        raise SecurityValidationError("invalid_media", "Файл повреждён или некорректен.", 422)
    duration = _as_duration(format_data.get("duration"), diagnostic_log)

    if _detect_iso_base_media_signature(data) is not None:
        _require_container_format(probe, _ISO_BMFF_PROBE_FORMATS, diagnostic_log)
    elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        _require_container_format(probe, {"avi"}, diagnostic_log)

    if actual == MediaType.AUDIO:
        stream = _first_stream(probe, "audio", diagnostic_log)
        _require_codec(
            stream,
            {"mp3", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_u8", "opus", "vorbis", "aac", "alac"},
        )
        channels = _as_positive_int(stream.get("channels"))
        sample_rate = _as_positive_int(stream.get("sample_rate"))
        if duration > MAX_AUDIO_DURATION_SECONDS or channels > MAX_AUDIO_CHANNELS or sample_rate > MAX_AUDIO_SAMPLE_RATE:
            raise SecurityValidationError("media_limits_exceeded", "Параметры аудио превышают лимит.", 422)
        _diagnose(diagnostic_log, "media_validation stage=limits result=ok")
        return MediaInfo(actual, duration=duration, channels=channels, sample_rate=sample_rate)
