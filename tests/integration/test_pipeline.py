"""Integration tests — require real API keys and network access.

Run with:
    pytest tests/integration/ -m integration

All tests skip gracefully if sample files are not present.
Results should be recorded in tests/integration/results.md.
"""

import os

import pytest

from core.enums import ModelUsed, Verdict


def _read_sample(path: str) -> bytes:
    """Read sample file; skip test if missing."""
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        pytest.skip(f"Test file not found: {path}")


# ===========================================================================
# SightengineAdapter (images)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_image_sightengine():
    from adapters.sightengine import SightengineAdapter

    data = _read_sample("media_samples/real/photo1.jpg")
    result = await SightengineAdapter().analyze(data)

    assert result.verdict in (Verdict.REAL, Verdict.UNCERTAIN)
    assert 0.0 <= result.confidence <= 1.0
    assert result.model_used == ModelUsed.SIGHTENGINE


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fake_image_sightengine():
    from adapters.sightengine import SightengineAdapter

    data = _read_sample("media_samples/fake/ai_generated1.jpg")
    result = await SightengineAdapter().analyze(data)

    assert result.confidence >= 0.0
    assert result.model_used == ModelUsed.SIGHTENGINE


@pytest.mark.integration
@pytest.mark.asyncio
async def test_hf_image_real():
    from adapters.hf_image import HFImageAdapter

    data = _read_sample("media_samples/real/photo1.jpg")
    result = await HFImageAdapter().analyze(data)

    assert result.verdict in (Verdict.REAL, Verdict.UNCERTAIN, Verdict.FAKE)
    assert 0.0 <= result.confidence <= 1.0
    assert result.model_used == ModelUsed.HF_IMAGE


@pytest.mark.integration
@pytest.mark.asyncio
async def test_hf_image_fake():
    from adapters.hf_image import HFImageAdapter

    data = _read_sample("media_samples/fake/ai_generated1.jpg")
    result = await HFImageAdapter().analyze(data)

    assert result.verdict in (Verdict.REAL, Verdict.UNCERTAIN, Verdict.FAKE)
    assert result.model_used == ModelUsed.HF_IMAGE


# ===========================================================================
# ResembleAdapter (audio)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_audio_resemble():
    from adapters.resemble import ResembleAdapter

    data = _read_sample("media_samples/real/voice1.wav")
    result = await ResembleAdapter().analyze(data)

    assert result.verdict in (Verdict.REAL, Verdict.UNCERTAIN)
    assert 0.0 <= result.confidence <= 1.0
    assert result.model_used == ModelUsed.RESEMBLE


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fake_audio_resemble():
    from adapters.resemble import ResembleAdapter

    data = _read_sample("media_samples/fake/tts1.wav")
    result = await ResembleAdapter().analyze(data)

    assert result.confidence >= 0.0
    assert result.model_used == ModelUsed.RESEMBLE


@pytest.mark.integration
@pytest.mark.asyncio
async def test_hf_audio_real():
    from adapters.hf_audio import HFAudioAdapter

    data = _read_sample("media_samples/real/voice1.wav")
    result = await HFAudioAdapter().analyze(data)

    assert result.verdict in (Verdict.REAL, Verdict.UNCERTAIN, Verdict.FAKE)
    assert result.model_used == ModelUsed.HF_AUDIO


# ===========================================================================
# SaplingAdapter (text)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_text_sapling():
    from adapters.sapling import SaplingAdapter

    data = _read_sample("media_samples/real/article1.txt")
    result = await SaplingAdapter().analyze(data)

    assert result.verdict in (Verdict.REAL, Verdict.UNCERTAIN)
    assert 0.0 <= result.confidence <= 1.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ai_text_sapling():
    from adapters.sapling import SaplingAdapter

    data = _read_sample("media_samples/fake/ai_text1.txt")
    result = await SaplingAdapter().analyze(data)

    assert result.confidence >= 0.0


# ===========================================================================
# Full routing pipeline (MediaRouter)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_router_image_end_to_end():
    from core.enums import MediaType
    from router.media_router import MediaRouter

    data = _read_sample("media_samples/real/photo1.jpg")
    result = await MediaRouter().route(MediaType.IMAGE, data)

    assert result.verdict in (Verdict.REAL, Verdict.UNCERTAIN, Verdict.FAKE)
    assert 0.0 <= result.confidence <= 1.0
    assert result.processing_ms >= 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_router_audio_end_to_end():
    from core.enums import MediaType
    from router.media_router import MediaRouter

    data = _read_sample("media_samples/real/voice1.wav")
    result = await MediaRouter().route(MediaType.AUDIO, data)

    assert result.verdict in (Verdict.REAL, Verdict.UNCERTAIN, Verdict.FAKE)
    assert result.processing_ms >= 0


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY is required for Gemini integration",
)
async def test_router_text_end_to_end():
    from core.enums import MediaType
    from router.media_router import MediaRouter

    text = (
        "Artificial intelligence has revolutionized the way we work, communicate, "
        "and interact with technology in our daily lives."
    )
    result = await MediaRouter().route(MediaType.TEXT, text.encode(), text_content=text)

    assert result.verdict in (Verdict.REAL, Verdict.UNCERTAIN, Verdict.FAKE)
    assert result.processing_ms >= 0
