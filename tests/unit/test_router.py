"""Unit tests for media router — type detection and routing."""

from unittest.mock import AsyncMock, patch

import pytest

from api.schemas import AnalysisResult
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError, UnsupportedMediaType
from router.media_router import MediaRouter

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FAKE_RESULT = AnalysisResult(
    verdict=Verdict.FAKE,
    confidence=0.95,
    model_used=ModelUsed.SIGHTENGINE,
    explanation="test",
    media_type=MediaType.IMAGE,
    processing_ms=100,
)

REAL_RESULT = AnalysisResult(
    verdict=Verdict.REAL,
    confidence=0.90,
    model_used=ModelUsed.RESEMBLE,
    explanation="test",
    media_type=MediaType.AUDIO,
    processing_ms=200,
)

UNCERTAIN_RESULT = AnalysisResult(
    verdict=Verdict.UNCERTAIN,
    confidence=0.50,
    model_used=ModelUsed.FALLBACK_UNCERTAIN,
    explanation="test",
    media_type=MediaType.AUDIO,
    processing_ms=50,
)


# ===========================================================================
# detect_type — MIME / extension / text
# ===========================================================================


class TestDetectType:
    def test_detect_image_by_jpeg_mime(self):
        assert MediaRouter().detect_type("image/jpeg", "photo.jpg") == MediaType.IMAGE

    def test_detect_image_by_png_mime(self):
        assert MediaRouter().detect_type("image/png", None) == MediaType.IMAGE

    def test_detect_audio_by_ogg_mime(self):
        assert MediaRouter().detect_type("audio/ogg", "voice.ogg") == MediaType.AUDIO

    def test_detect_audio_by_mpeg_mime(self):
        assert MediaRouter().detect_type("audio/mpeg", "song.mp3") == MediaType.AUDIO

    def test_detect_video_by_mp4_mime(self):
        assert MediaRouter().detect_type("video/mp4", "clip.mp4") == MediaType.VIDEO

    def test_detect_video_by_extension_fallback(self):
        assert MediaRouter().detect_type(None, "clip.mp4") == MediaType.VIDEO

    def test_detect_image_by_extension_fallback(self):
        assert MediaRouter().detect_type(None, "photo.png") == MediaType.IMAGE

    def test_detect_audio_by_wav_extension(self):
        assert MediaRouter().detect_type(None, "audio.wav") == MediaType.AUDIO

    def test_detect_text_when_text_content_provided(self):
        assert MediaRouter().detect_type(None, None, text_content="some text here") == MediaType.TEXT

    def test_text_takes_priority_over_mime(self):
        # text_content always wins
        assert MediaRouter().detect_type("image/jpeg", "photo.jpg", text_content="hello") == MediaType.TEXT

    def test_unsupported_type_raises(self):
        with pytest.raises(UnsupportedMediaType):
            MediaRouter().detect_type("application/zip", "archive.zip")

    def test_unknown_extension_raises(self):
        with pytest.raises(UnsupportedMediaType):
            MediaRouter().detect_type(None, "file.xyz")

    def test_none_none_no_text_raises(self):
        with pytest.raises(UnsupportedMediaType):
            MediaRouter().detect_type(None, None)


# ===========================================================================
# route — adapter dispatch
# ===========================================================================


class TestRoute:
    @pytest.mark.asyncio
    async def test_image_routes_to_sightengine(self):
        mock_analyze = AsyncMock(return_value=FAKE_RESULT)
        with patch("router.media_router.SightengineAdapter.analyze", mock_analyze):
            result = await MediaRouter().route(MediaType.IMAGE, b"img_bytes")
        mock_analyze.assert_awaited_once()
        assert result.verdict == Verdict.FAKE

    @pytest.mark.asyncio
    async def test_image_falls_back_to_hf_image_on_api_error(self):
        se_analyze = AsyncMock(side_effect=ExternalAPIError("sightengine", "rate_limit"))
        hf_result = AnalysisResult(
            verdict=Verdict.REAL,
            confidence=0.88,
            model_used=ModelUsed.HF_IMAGE,
            explanation="HF fallback",
            media_type=MediaType.IMAGE,
            processing_ms=300,
        )
        hf_analyze = AsyncMock(return_value=hf_result)

        with patch("router.media_router.SightengineAdapter.analyze", se_analyze), \
             patch("router.media_router.HFImageAdapter.analyze", hf_analyze):
            result = await MediaRouter().route(MediaType.IMAGE, b"img_bytes")

        se_analyze.assert_awaited_once()
        hf_analyze.assert_awaited_once()
        assert result.model_used == ModelUsed.HF_IMAGE

    @pytest.mark.asyncio
    async def test_sightengine_429_uses_hf_fallback_without_raw_provider_error(self):
        client = AsyncMock()
        client.post = AsyncMock(return_value=type("Response", (), {"status_code": 429})())
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        hf_result = AnalysisResult(
            verdict=Verdict.REAL,
            confidence=0.1,
            model_used=ModelUsed.HF_IMAGE,
            explanation="safe fallback",
            media_type=MediaType.IMAGE,
        )
        with patch("adapters.sightengine.httpx.AsyncClient", return_value=client), patch(
            "router.media_router.HFImageAdapter.analyze", new=AsyncMock(return_value=hf_result)
        ) as hf:
            result = await MediaRouter().route(MediaType.IMAGE, b"img_bytes")
        assert result is hf_result
        hf.assert_awaited_once()
        assert "raw" not in result.explanation

    @pytest.mark.asyncio
    async def test_image_falls_back_after_typed_infrastructure_failure(self):
        primary = AsyncMock(side_effect=ProviderInfrastructureError("sightengine", "timeout"))
        fallback = AsyncMock(return_value=REAL_RESULT)
        with patch("router.media_router.SightengineAdapter.analyze", primary), patch(
            "router.media_router.HFImageAdapter.analyze", fallback
        ):
            result = await MediaRouter().route(MediaType.IMAGE, b"img_bytes")
        assert result.verdict == Verdict.REAL
        fallback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_audio_routes_to_resemble(self):
        mock_analyze = AsyncMock(return_value=REAL_RESULT)
        with patch("router.media_router.ResembleAdapter.analyze", mock_analyze):
            result = await MediaRouter().route(MediaType.AUDIO, b"audio_bytes")
        mock_analyze.assert_awaited_once()
        assert result.verdict == Verdict.REAL

    @pytest.mark.asyncio
    @pytest.mark.parametrize("verdict", [Verdict.REAL, Verdict.FAKE])
    async def test_audio_keeps_decisive_resemble_result_without_hf_merge(self, verdict):
        primary = AnalysisResult(
            verdict=verdict,
            confidence=0.9,
            model_used=ModelUsed.RESEMBLE,
            explanation="decisive",
            media_type=MediaType.AUDIO,
        )
        with patch(
            "router.media_router.ResembleAdapter.analyze", new=AsyncMock(return_value=primary)
        ), patch("router.media_router.HFAudioAdapter.analyze", new=AsyncMock()) as hf:
            result = await MediaRouter().route(MediaType.AUDIO, b"audio_bytes")
        assert result is primary
        hf.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_audio_calls_hf_fallback_when_resemble_uncertain(self):
        """When Resemble returns UNCERTAIN, HFAudio must be invoked as fallback."""
        hf_result = AnalysisResult(
            verdict=Verdict.FAKE,
            confidence=0.80,
            model_used=ModelUsed.HF_AUDIO,
            explanation="HF Audio fallback",
            media_type=MediaType.AUDIO,
            processing_ms=250,
        )
        resemble_analyze = AsyncMock(return_value=UNCERTAIN_RESULT)
        hf_analyze = AsyncMock(return_value=hf_result)

        with patch("router.media_router.ResembleAdapter.analyze", resemble_analyze), \
             patch("router.media_router.HFAudioAdapter.analyze", hf_analyze):
            result = await MediaRouter().route(MediaType.AUDIO, b"audio_bytes")

        resemble_analyze.assert_awaited_once()
        hf_analyze.assert_awaited_once()
        # HF result must override since it's not UNCERTAIN
        assert result.verdict == Verdict.FAKE

    @pytest.mark.asyncio
    async def test_audio_falls_back_to_hf_on_api_error(self):
        se_error = AsyncMock(side_effect=ExternalAPIError("resemble", "rate_limit"))
        hf_analyze = AsyncMock(return_value=REAL_RESULT)

        with patch("router.media_router.ResembleAdapter.analyze", se_error), \
             patch("router.media_router.HFAudioAdapter.analyze", hf_analyze):
            result = await MediaRouter().route(MediaType.AUDIO, b"audio_bytes")

        hf_analyze.assert_awaited_once()
        assert result.verdict == Verdict.REAL

    @pytest.mark.asyncio
    async def test_audio_falls_back_to_hf_on_technical_resemble_failure(self):
        primary = AsyncMock(side_effect=ProviderInfrastructureError("resemble", "timeout"))
        hf_result = AnalysisResult(
            verdict=Verdict.FAKE,
            confidence=0.9,
            model_used=ModelUsed.HF_AUDIO,
            explanation="safe fallback",
            media_type=MediaType.AUDIO,
        )
        with patch("router.media_router.ResembleAdapter.analyze", primary), patch(
            "router.media_router.HFAudioAdapter.analyze", new=AsyncMock(return_value=hf_result)
        ) as hf:
            result = await MediaRouter().route(MediaType.AUDIO, b"audio_bytes")
        assert result is hf_result
        hf.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_video_routes_to_legacy_pipeline_after_direct_technical_failure(self):
        video_result = AnalysisResult(
            verdict=Verdict.FAKE,
            confidence=0.70,
            model_used=ModelUsed.SIGHTENGINE_VIDEO,
            explanation="3/5 fake frames",
            media_type=MediaType.VIDEO,
            processing_ms=8000,
        )
        mock_analyze = AsyncMock(return_value=video_result)
        with patch(
            "router.media_router.SightengineVideoAdapter.analyze",
            new=AsyncMock(side_effect=ProviderInfrastructureError("sightengine", "unavailable")),
        ), patch("router.media_router.VideoPipeline.analyze", mock_analyze):
            result = await MediaRouter().route(MediaType.VIDEO, b"video_bytes")
        mock_analyze.assert_awaited_once()
        assert result.model_used == ModelUsed.SIGHTENGINE_VIDEO

    @pytest.mark.asyncio
    async def test_text_routes_to_sapling(self):
        text_result = AnalysisResult(
            verdict=Verdict.FAKE,
            confidence=0.92,
            model_used=ModelUsed.SAPLING,
            explanation="Sapling: 92% AI",
            media_type=MediaType.TEXT,
            processing_ms=400,
        )
        mock_analyze = AsyncMock(return_value=text_result)
        with patch("router.media_router.SaplingAdapter.analyze", mock_analyze):
            result = await MediaRouter().route(
                MediaType.TEXT, b"some text for sapling", text_content="some text for sapling"
            )
        mock_analyze.assert_awaited_once()
        assert result.model_used == ModelUsed.SAPLING

    @pytest.mark.asyncio
    async def test_eligible_text_routes_to_aiornot_without_sapling(self):
        text = " ".join(["word"] * 64)
        aiornot_result = AnalysisResult(
            verdict=Verdict.FAKE,
            confidence=0.9,
            model_used=ModelUsed.AIORNOT_TEXT,
            explanation="safe",
            media_type=MediaType.TEXT,
        )
        with patch(
            "router.media_router.AIOrNotTextAdapter.analyze",
            new=AsyncMock(return_value=aiornot_result),
        ) as aiornot, patch("router.media_router.SaplingAdapter.analyze", new=AsyncMock()) as sapling:
            result = await MediaRouter().route(MediaType.TEXT, b"", text)
        aiornot.assert_awaited_once()
        sapling.assert_not_awaited()
        assert result.model_used == ModelUsed.AIORNOT_TEXT

    @pytest.mark.asyncio
    async def test_short_valid_text_bypasses_aiornot_and_routes_to_sapling(self):
        text = "x" * 50
        fallback = AnalysisResult(
            verdict=Verdict.UNCERTAIN,
            confidence=0.5,
            model_used=ModelUsed.SAPLING,
            explanation="safe",
            media_type=MediaType.TEXT,
        )
        with patch("router.media_router.AIOrNotTextAdapter.analyze", new=AsyncMock()) as aiornot, patch(
            "router.media_router.SaplingAdapter.analyze", new=AsyncMock(return_value=fallback)
        ) as sapling:
            result = await MediaRouter().route(MediaType.TEXT, b"", text)
        aiornot.assert_not_awaited()
        sapling.assert_awaited_once()
        assert result.model_used == ModelUsed.SAPLING

    @pytest.mark.asyncio
    async def test_eligible_text_uses_sapling_after_aiornot_technical_failure(self):
        text = " ".join(["word"] * 64)
        fallback = AnalysisResult(
            verdict=Verdict.REAL,
            confidence=0.1,
            model_used=ModelUsed.SAPLING,
            explanation="safe",
            media_type=MediaType.TEXT,
        )
        with patch(
            "router.media_router.AIOrNotTextAdapter.analyze",
            new=AsyncMock(side_effect=ProviderInfrastructureError("aiornot", "timeout")),
        ), patch("router.media_router.SaplingAdapter.analyze", new=AsyncMock(return_value=fallback)) as sapling:
            result = await MediaRouter().route(MediaType.TEXT, b"", text)
        sapling.assert_awaited_once()
        assert result.model_used == ModelUsed.SAPLING
