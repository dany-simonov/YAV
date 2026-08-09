from unittest.mock import patch

import pytest

from core.enums import MediaType
from src.media_validation import validate_media_bytes
from src.validation import SecurityValidationError


def _image_probe(width=100, height=100):
    return {"streams": [{"codec_type": "video", "codec_name": "mjpeg", "width": width, "height": height}]}


def _audio_probe(duration=1, channels=1, sample_rate=44_100):
    return {"format": {"duration": str(duration)}, "streams": [{"codec_type": "audio", "codec_name": "opus", "channels": channels, "sample_rate": str(sample_rate)}]}


def _video_probe(duration=1, width=1920, height=1080):
    return {"format": {"duration": str(duration)}, "streams": [{"codec_type": "video", "codec_name": "h264", "width": width, "height": height}]}


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
            validate_media_bytes(b"\x00\x00\x00\x18ftypisom" + b"x", MediaType.VIDEO)
    assert raised.value.code == "media_limits_exceeded"
