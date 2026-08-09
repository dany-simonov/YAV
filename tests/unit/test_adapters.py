"""Unit tests for all adapters — mock httpx, no real API calls."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.enums import ModelUsed, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_client(response_body: object, status_code: int = 200) -> AsyncMock:
    """Build a fully async-context-manager-compatible mock for httpx.AsyncClient."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = response_body

    mock_instance = AsyncMock()
    mock_instance.post = AsyncMock(return_value=mock_response)
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    return mock_instance


# ===========================================================================
# SightengineAdapter
# ===========================================================================


class TestSightengineAdapter:
    @pytest.mark.asyncio
    async def test_uses_genai_multipart_request_without_exposing_credentials(self):
        from adapters.sightengine import SightengineAdapter

        client = _mock_client({"status": "success", "type": {"ai_generated": 0.95}})
        with patch("adapters.sightengine.httpx.AsyncClient", return_value=client):
            result = await SightengineAdapter().analyze(b"image_bytes")
        assert client.post.await_args.args[0] == SightengineAdapter.URL
        assert client.post.await_args.kwargs["data"] == {
            "api_user": "test_se_user",
            "api_secret": "test_se_secret",
            "models": "genai",
        }
        assert client.post.await_args.kwargs["files"] == {"media": ("image.jpg", b"image_bytes", "image/jpeg")}
        assert "test_se_secret" not in result.explanation

    @pytest.mark.asyncio
    async def test_fake_verdict(self):
        from adapters.sightengine import SightengineAdapter

        with patch("httpx.AsyncClient", return_value=_mock_client(
            {"status": "success", "type": {"ai_generated": 0.95}}
        )):
            result = await SightengineAdapter().analyze(b"fake_image")
        assert result.verdict == Verdict.FAKE
        assert result.confidence >= 0.75
        assert result.model_used == ModelUsed.SIGHTENGINE

    @pytest.mark.asyncio
    async def test_real_verdict(self):
        from adapters.sightengine import SightengineAdapter

        with patch("httpx.AsyncClient", return_value=_mock_client(
            {"status": "success", "type": {"ai_generated": 0.10}}
        )):
            result = await SightengineAdapter().analyze(b"real_image")
        assert result.verdict == Verdict.REAL
        assert result.confidence <= 0.35

    @pytest.mark.asyncio
    async def test_uncertain_verdict_midrange(self):
        from adapters.sightengine import SightengineAdapter

        with patch("httpx.AsyncClient", return_value=_mock_client(
            {"status": "success", "type": {"ai_generated": 0.55}}
        )):
            result = await SightengineAdapter().analyze(b"ambiguous_image")
        assert result.verdict == Verdict.UNCERTAIN

    @pytest.mark.asyncio
    async def test_timeout_raises_typed_infrastructure_error(self):
        from adapters.sightengine import SightengineAdapter

        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_instance):
            with pytest.raises(ProviderInfrastructureError) as raised:
                await SightengineAdapter().analyze(b"image_bytes")
        assert raised.value.kind == "timeout"

    @pytest.mark.asyncio
    async def test_rate_limit_raises_external_api_error(self):
        from adapters.sightengine import SightengineAdapter

        with patch("httpx.AsyncClient", return_value=_mock_client({}, status_code=429)):
            with pytest.raises(ExternalAPIError) as exc_info:
                await SightengineAdapter().analyze(b"image_bytes")
        assert exc_info.value.service == "sightengine"
        assert exc_info.value.detail == "rate_limit"

    @pytest.mark.asyncio
    async def test_normal_4xx_is_not_typed_as_infrastructure_failure(self):
        from adapters.sightengine import SightengineAdapter

        with patch("adapters.sightengine.httpx.AsyncClient", return_value=_mock_client({}, status_code=422)):
            with pytest.raises(ExternalAPIError) as raised:
                await SightengineAdapter().analyze(b"image_bytes")
        assert not isinstance(raised.value, ProviderInfrastructureError)
        assert raised.value.detail == "request_error"

    @pytest.mark.asyncio
    async def test_provider_guard_denial_prevents_sightengine_http(self):
        from adapters.sightengine import SightengineAdapter
        from src.provider_protection import begin_provider_budget, end_provider_budget
        from src.rate_limit import RateLimitError

        async def deny(provider: str) -> None:
            assert provider == "sightengine"
            raise RateLimitError("provider_temporarily_unavailable", "safe", 503)

        tokens = begin_provider_budget(deny)
        try:
            with patch("adapters.sightengine.httpx.AsyncClient") as client:
                with pytest.raises(ProviderInfrastructureError) as raised:
                    await SightengineAdapter().analyze(b"image_bytes")
        finally:
            end_provider_budget(tokens)
        assert raised.value.kind == "capacity"
        client.assert_not_called()

    @pytest.mark.asyncio
    async def test_server_error_raises_external_api_error(self):
        from adapters.sightengine import SightengineAdapter

        with patch("httpx.AsyncClient", return_value=_mock_client({}, status_code=500)):
            with pytest.raises(ExternalAPIError):
                await SightengineAdapter().analyze(b"image_bytes")


# ===========================================================================
# ResembleAdapter
# ===========================================================================


class TestResembleAdapter:
    @pytest.mark.asyncio
    async def test_fake_verdict(self):
        from adapters.resemble import ResembleAdapter

        with patch("httpx.AsyncClient", return_value=_mock_client(
            {"success": True, "score": 0.89, "tampered": True}
        )):
            result = await ResembleAdapter().analyze(b"WAV_audio_bytes")
        assert result.verdict == Verdict.FAKE
        assert result.confidence >= 0.75
        assert result.model_used == ModelUsed.RESEMBLE

    @pytest.mark.asyncio
    async def test_real_verdict(self):
        from adapters.resemble import ResembleAdapter

        with patch("httpx.AsyncClient", return_value=_mock_client(
            {"success": True, "score": 0.12, "tampered": False}
        )):
            result = await ResembleAdapter().analyze(b"WAV_real_audio")
        assert result.verdict == Verdict.REAL
        assert result.confidence <= 0.30

    @pytest.mark.asyncio
    async def test_uncertain_verdict_midrange(self):
        from adapters.resemble import ResembleAdapter

        with patch("httpx.AsyncClient", return_value=_mock_client(
            {"success": True, "score": 0.50}
        )):
            result = await ResembleAdapter().analyze(b"WAV_ambiguous_audio")
        assert result.verdict == Verdict.UNCERTAIN

    @pytest.mark.asyncio
    async def test_timeout_raises_typed_infrastructure_error(self):
        from adapters.resemble import ResembleAdapter

        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_instance):
            with pytest.raises(ProviderInfrastructureError) as raised:
                await ResembleAdapter().analyze(b"WAV_bytes")
        assert raised.value.kind == "timeout"

    @pytest.mark.asyncio
    async def test_ogg_conversion_invoked(self):
        """OGG magic bytes trigger subprocess ffmpeg conversion before upload."""
        from adapters.resemble import ResembleAdapter

        ogg_data = b"OggS" + b"\x00" * 100

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = b"RIFF" + b"\x00" * 44  # minimal WAV stub

        with patch("subprocess.run", return_value=mock_proc) as mock_run, \
             patch("httpx.AsyncClient", return_value=_mock_client(
                 {"success": True, "score": 0.80, "tampered": True}
             )):
            result = await ResembleAdapter().analyze(ogg_data)

        mock_run.assert_called_once()
        call_cmd = mock_run.call_args[0][0]
        assert "ffmpeg" in call_cmd
        assert result.verdict == Verdict.FAKE

    @pytest.mark.asyncio
    async def test_server_error_raises_external_api_error(self):
        from adapters.resemble import ResembleAdapter

        with patch("httpx.AsyncClient", return_value=_mock_client({}, status_code=500)):
            with pytest.raises(ExternalAPIError) as exc_info:
                await ResembleAdapter().analyze(b"WAV_bytes")
        assert exc_info.value.service == "resemble"


# ===========================================================================
# SaplingAdapter
# ===========================================================================


class TestSaplingAdapter:
    @pytest.mark.asyncio
    async def test_fake_verdict(self):
        from adapters.sapling import SaplingAdapter

        body = {
            "score": 0.92,
            "sentence_scores": [["This was generated by AI.", 0.95]],
        }
        with patch("httpx.AsyncClient", return_value=_mock_client(body)):
            result = await SaplingAdapter().analyze(
                b"This text was clearly written by artificial intelligence and shows many patterns."
            )
        assert result.verdict == Verdict.FAKE
        assert result.confidence >= 0.80
        assert result.model_used == ModelUsed.SAPLING

    @pytest.mark.asyncio
    async def test_real_verdict(self):
        from adapters.sapling import SaplingAdapter

        body = {"score": 0.05, "sentence_scores": []}
        with patch("httpx.AsyncClient", return_value=_mock_client(body)):
            result = await SaplingAdapter().analyze(
                b"I went to the market yesterday morning and bought fresh bread."
            )
        assert result.verdict == Verdict.REAL
        assert result.confidence <= 0.25

    @pytest.mark.asyncio
    async def test_short_text_returns_uncertain_without_api_call(self):
        """Text shorter than 50 chars must return UNCERTAIN without calling API."""
        from adapters.sapling import SaplingAdapter

        with patch("httpx.AsyncClient") as mock_cls:
            result = await SaplingAdapter().analyze(b"Too short.")
        mock_cls.assert_not_called()
        assert result.verdict == Verdict.UNCERTAIN
        assert "короткий" in result.explanation

    @pytest.mark.asyncio
    async def test_timeout_raises_typed_infrastructure_error(self):
        from adapters.sapling import SaplingAdapter

        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_instance):
            with pytest.raises(ProviderInfrastructureError) as raised:
                await SaplingAdapter().analyze(b"x" * 60)
        assert raised.value.kind == "timeout"

    @pytest.mark.asyncio
    async def test_long_text_is_rejected_without_silent_truncation(self):
        """Text over 10 000 chars must not be silently sent to the provider."""
        from adapters.sapling import SaplingAdapter

        with patch("httpx.AsyncClient") as client:
            result = await SaplingAdapter().analyze(b"A" * 11_000)
        assert result.verdict == Verdict.UNCERTAIN
        assert "превышает" in result.explanation
        client.assert_not_called()

    @pytest.mark.asyncio
    async def test_uncertain_midrange(self):
        from adapters.sapling import SaplingAdapter

        body = {"score": 0.50, "sentence_scores": []}
        with patch("httpx.AsyncClient", return_value=_mock_client(body)):
            result = await SaplingAdapter().analyze(
                b"Some ambiguous text that is hard to classify either way."
            )
        assert result.verdict == Verdict.UNCERTAIN


# ===========================================================================
# HFImageAdapter
# ===========================================================================


class TestHFImageAdapter:
    @pytest.mark.asyncio
    async def test_fake_verdict(self):
        from adapters.hf_image import HFImageAdapter

        body = [{"label": "FAKE", "score": 0.94}, {"label": "REAL", "score": 0.06}]
        with patch("httpx.AsyncClient", return_value=_mock_client(body)):
            result = await HFImageAdapter().analyze(b"image_bytes")
        assert result.verdict == Verdict.FAKE
        assert result.confidence >= 0.7
        assert result.model_used == ModelUsed.HF_IMAGE

    @pytest.mark.asyncio
    async def test_real_verdict(self):
        from adapters.hf_image import HFImageAdapter

        body = [{"label": "REAL", "score": 0.91}, {"label": "FAKE", "score": 0.09}]
        with patch("httpx.AsyncClient", return_value=_mock_client(body)):
            result = await HFImageAdapter().analyze(b"real_image_bytes")
        assert result.verdict == Verdict.REAL

    @pytest.mark.asyncio
    async def test_low_confidence_returns_uncertain(self):
        from adapters.hf_image import HFImageAdapter

        body = [{"label": "FAKE", "score": 0.55}, {"label": "REAL", "score": 0.45}]
        with patch("httpx.AsyncClient", return_value=_mock_client(body)):
            result = await HFImageAdapter().analyze(b"ambiguous_image")
        assert result.verdict == Verdict.UNCERTAIN

    @pytest.mark.asyncio
    async def test_cold_start_retries_then_uncertain(self):
        """Model loading body causes retries; after MAX_RETRIES → UNCERTAIN."""
        from adapters.hf_image import HFImageAdapter

        cold_body = {"error": "Model is currently loading"}

        with patch("httpx.AsyncClient", return_value=_mock_client(cold_body)), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await HFImageAdapter().analyze(b"image_bytes")
        assert result.verdict == Verdict.UNCERTAIN

    @pytest.mark.asyncio
    async def test_timeout_raises_typed_infrastructure_error(self):
        from adapters.hf_image import HFImageAdapter

        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_instance):
            with pytest.raises(ProviderInfrastructureError) as raised:
                await HFImageAdapter().analyze(b"image_bytes")
        assert raised.value.kind == "timeout"


# ===========================================================================
# HFAudioAdapter
# ===========================================================================


class TestHFAudioAdapter:
    @pytest.mark.asyncio
    async def test_spoof_returns_fake(self):
        from adapters.hf_audio import HFAudioAdapter

        body = [{"label": "spoof", "score": 0.88}, {"label": "bonafide", "score": 0.12}]
        with patch("httpx.AsyncClient", return_value=_mock_client(body)):
            result = await HFAudioAdapter().analyze(b"WAV_audio_data")
        assert result.verdict == Verdict.FAKE
        assert result.model_used == ModelUsed.HF_AUDIO

    @pytest.mark.asyncio
    async def test_bonafide_returns_real(self):
        from adapters.hf_audio import HFAudioAdapter

        body = [{"label": "bonafide", "score": 0.92}, {"label": "spoof", "score": 0.08}]
        with patch("httpx.AsyncClient", return_value=_mock_client(body)):
            result = await HFAudioAdapter().analyze(b"WAV_audio_data")
        assert result.verdict == Verdict.REAL

    @pytest.mark.asyncio
    async def test_low_confidence_returns_uncertain(self):
        from adapters.hf_audio import HFAudioAdapter

        body = [{"label": "spoof", "score": 0.60}, {"label": "bonafide", "score": 0.40}]
        with patch("httpx.AsyncClient", return_value=_mock_client(body)):
            result = await HFAudioAdapter().analyze(b"WAV_ambiguous")
        assert result.verdict == Verdict.UNCERTAIN

    @pytest.mark.asyncio
    async def test_cold_start_retries_then_uncertain(self):
        from adapters.hf_audio import HFAudioAdapter

        cold_body = {"error": "Model is currently loading"}

        with patch("httpx.AsyncClient", return_value=_mock_client(cold_body)), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await HFAudioAdapter().analyze(b"WAV_audio")
        assert result.verdict == Verdict.UNCERTAIN

    @pytest.mark.asyncio
    async def test_timeout_raises_typed_infrastructure_error(self):
        from adapters.hf_audio import HFAudioAdapter

        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_instance):
            with pytest.raises(ProviderInfrastructureError) as raised:
                await HFAudioAdapter().analyze(b"WAV_audio")
        assert raised.value.kind == "timeout"

    @pytest.mark.asyncio
    async def test_ogg_input_triggers_ffmpeg_conversion(self):
        """OGG header (OggS) must trigger subprocess ffmpeg conversion."""
        from adapters.hf_audio import HFAudioAdapter

        ogg_data = b"OggS" + b"\x00" * 50

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = b"RIFF" + b"\x00" * 44

        body = [{"label": "bonafide", "score": 0.85}, {"label": "spoof", "score": 0.15}]

        with patch("subprocess.run", return_value=mock_proc) as mock_run, \
             patch("httpx.AsyncClient", return_value=_mock_client(body)):
            result = await HFAudioAdapter().analyze(ogg_data)

        mock_run.assert_called_once()
        assert result.verdict == Verdict.REAL
