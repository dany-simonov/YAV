import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.enums import MediaType
from src.media_validation import _run_probe, detect_signature, validate_media_bytes
from src.validation import SecurityValidationError


def _image_probe(width=100, height=100):
    return {"streams": [{"codec_type": "video", "codec_name": "mjpeg", "width": width, "height": height}]}


def _audio_probe(duration=1, channels=1, sample_rate=44_100):
    return {"format": {"duration": str(duration)}, "streams": [{"codec_type": "audio", "codec_name": "opus", "channels": channels, "sample_rate": str(sample_rate)}]}


def _video_probe(duration=1, width=1920, height=1080, format_name="mov,mp4,m4a,3gp,3g2,mj2"):
    return {"format": {"duration": str(duration), "format_name": format_name}, "streams": [{"codec_type": "video", "codec_name": "h264", "width": width, "height": height}]}


def _ftyp(major_brand, compatible_brands=()):
    box_size = 16 + 4 * len(compatible_brands)
    return box_size.to_bytes(4, "big") + b"ftyp" + major_brand + b"\0\0\0\0" + b"".join(compatible_brands)


@pytest.mark.parametrize("data", [b"\xff\xd8\xff" + b"x", b"\x89PNG\r\n\x1a\n" + b"x", b"RIFFxxxxWEBP" + b"x"])
def test_valid_image_signatures_are_verified(data):
    with patch("src.media_validation._run_probe", return_value=_image_probe()), patch("src.media_validation._validate_image_decode"):
        assert validate_media_bytes(data, MediaType.IMAGE).media_type == MediaType.IMAGE


def test_media_type_spoof_is_rejected():
    with pytest.raises(SecurityValidationError) as raised:
        validate_media_bytes(b"OggS" + b"x", MediaType.IMAGE)
    assert raised.value.code == "media_type_mismatch"


def test_invalid_magic_and_zero_byte_are_rejected():
    for data in (b"", b"<svg></svg>"):
        with pytest.raises(SecurityValidationError):
            validate_media_bytes(data)


@pytest.mark.parametrize(
    "header",
    [
        _ftyp(b"isom", (b"iso2", b"avc1")),
        _ftyp(b"mp42", (b"isom",)),
        _ftyp(b"iso5", (b"iso6",)),
        _ftyp(b"qt  ", ()),
    ],
)
def test_structural_iso_base_media_headers_are_recognized_as_video(header):
    assert detect_signature(header) == MediaType.VIDEO


def test_iso_base_media_compatible_brand_is_used_when_major_brand_varies():
    assert detect_signature(_ftyp(b"zzzz", (b"iso6",))) == MediaType.VIDEO


def test_iso_base_media_reaches_ffprobe_after_structural_signature_validation():
    with patch("src.media_validation._run_probe", return_value=_video_probe()) as probe:
        assert validate_media_bytes(_ftyp(b"iso6", (b"mp42",)), MediaType.VIDEO).media_type == MediaType.VIDEO
    probe.assert_called_once()


@pytest.mark.parametrize(
    ("side_effect", "completed", "reason"),
    [
        (FileNotFoundError(), None, "binary_missing"),
        (subprocess.TimeoutExpired("ffprobe", 5), None, "timeout"),
        (None, SimpleNamespace(returncode=1, stdout=b""), "nonzero_exit"),
        (None, SimpleNamespace(returncode=0, stdout=b"not-json"), "invalid_json"),
    ],
)
def test_probe_failures_emit_safe_diagnostic_codes(side_effect, completed, reason):
    logs = []
    with patch("src.media_validation.subprocess.run", side_effect=side_effect, return_value=completed):
        with pytest.raises(SecurityValidationError) as raised:
            _run_probe(b"bytes containing file-id jwt filename and stderr", logs.append)
    assert raised.value.code == "invalid_media"
    assert logs[-1] == f"media_validation stage=ffprobe result=failed reason={reason}"
    assert all(secret not in " ".join(logs) for secret in ("file-id", "jwt", "filename", "stderr"))


def test_happy_video_validation_emits_safe_stage_logs():
    logs = []
    with patch("src.media_validation._run_probe", return_value=_video_probe()) as probe:
        assert validate_media_bytes(_ftyp(b"iso6", (b"mp42",)), MediaType.VIDEO, logs.append).media_type == MediaType.VIDEO
    probe.assert_called_once()
    assert logs == [
        "media_validation stage=signature result=ok detected=video",
        "media_validation stage=limits result=ok",
    ]


@pytest.mark.parametrize(
    "data",
    [
        b"random ftyp isom bytes",
        b"\x00\x00\x00\x18ftypisom",
        b"\x00\x00\x00\x10ftypzzzz\x00\x00\x00\x00",
        b"<html>ftypisom</html>",
    ],
)
def test_invalid_or_non_structural_ftyp_is_rejected_before_probe(data):
    with patch("src.media_validation._run_probe") as probe:
        with pytest.raises(SecurityValidationError) as raised:
            validate_media_bytes(data, MediaType.VIDEO)
    assert raised.value.code == "unsupported_media_type"
    probe.assert_not_called()


def test_avi_signature_remains_supported():
    data = b"RIFF\x00\x00\x00\x00AVI " + b"x"
    with patch("src.media_validation._run_probe", return_value=_video_probe(format_name="avi")):
        assert validate_media_bytes(data, MediaType.VIDEO).media_type == MediaType.VIDEO


def test_huge_image_dimensions_are_rejected():
    with patch("src.media_validation._run_probe", return_value=_image_probe(9000, 9000)):
        with pytest.raises(SecurityValidationError) as raised:
            validate_media_bytes(b"\xff\xd8\xff" + b"x", MediaType.IMAGE)
    assert raised.value.code == "media_limits_exceeded"


@pytest.mark.parametrize("probe", [_audio_probe(duration=301), _audio_probe(channels=3), _audio_probe(sample_rate=96_001)])
def test_audio_limits_are_rejected(probe):
    with patch("src.media_validation._run_probe", return_value=probe):
        with pytest.raises(SecurityValidationError) as raised:
            validate_media_bytes(b"OggS" + b"x", MediaType.AUDIO)
    assert raised.value.code == "media_limits_exceeded"


@pytest.mark.parametrize("probe", [_video_probe(duration=61), _video_probe(width=1921), _video_probe(height=1081)])
def test_video_limits_are_rejected(probe):
    with patch("src.media_validation._run_probe", return_value=probe):
        with pytest.raises(SecurityValidationError) as raised:
            validate_media_bytes(_ftyp(b"isom", (b"iso2", b"avc1")), MediaType.VIDEO)
    assert raised.value.code == "media_limits_exceeded"
