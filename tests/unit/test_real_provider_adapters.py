"""Transport-mocked contracts for current real provider integrations."""

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from adapters.aiornot_image import AIOrNotImageAdapter
from adapters.hf_image import HFImageAdapter
from adapters.resemble import ResembleAdapter
from adapters.sapling import SaplingAdapter
from adapters.sightengine import SightengineAdapter
from core.config import settings
from core.enums import Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError


def _response(status: int, body: object = None) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.json.return_value = body
    return response


def _client(*, posts: list[MagicMock] | None = None, gets: list[MagicMock] | None = None, error: Exception | None = None) -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(side_effect=error if error else (posts or []))
    client.get = AsyncMock(side_effect=gets or [])
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize(("score", "verdict"), [(0, Verdict.REAL), (1, Verdict.FAKE)])
async def test_aiornot_image_uses_provider_verdict_and_confidence(score, verdict):
    client = _client(posts=[_response(200, {"verdict": "human" if score == 0 else "ai", "confidence": score})])
    with patch.object(settings, "aiornot_api_key", "unit-secret"), patch(
        "adapters.aiornot_image.httpx.AsyncClient", return_value=client
    ):
        result = await AIOrNotImageAdapter().analyze(b"image")

    assert (result.verdict, result.confidence, result.decision_confidence) == (verdict, score, score)
    assert result.ai_probability is None
    assert result.provider_evidence is not None
    assert result.provider_evidence.safe_details["provider_confidence"] == score
    assert "unit-secret" not in result.explanation


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "detail"), [(401, "auth_error"), (403, "auth_error"), (429, "rate_limit")])
async def test_aiornot_image_maps_auth_and_rate_errors(status, detail):
    client = _client(posts=[_response(status)])
    with patch.object(settings, "aiornot_api_key", "unit-secret"), patch(
        "adapters.aiornot_image.httpx.AsyncClient", return_value=client
    ), pytest.raises(ExternalAPIError) as raised:
        await AIOrNotImageAdapter().analyze(b"image")
    assert (raised.value.service, raised.value.detail, raised.value.status_code) == ("aiornot", detail, status)
    assert "unit-secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_aiornot_image_maps_timeout_5xx_and_malformed_response():
    with patch.object(settings, "aiornot_api_key", "unit-secret"), patch(
        "adapters.aiornot_image.httpx.AsyncClient", return_value=_client(error=httpx.ReadTimeout("timeout"))
    ), pytest.raises(ProviderInfrastructureError) as timeout:
        await AIOrNotImageAdapter().analyze(b"image")
    assert timeout.value.kind == "timeout"

    for response in (_response(503), _response(200, {"verdict": "ai"})):
        with patch.object(settings, "aiornot_api_key", "unit-secret"), patch(
            "adapters.aiornot_image.httpx.AsyncClient", return_value=_client(posts=[response])
        ), pytest.raises(ProviderInfrastructureError) as raised:
            await AIOrNotImageAdapter().analyze(b"image")
        assert raised.value.kind in {"unavailable", "invalid_response"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "score", "verdict"),
    [("Real", 0, Verdict.REAL), ("Fake", 1, Verdict.FAKE)],
)
async def test_hf_image_preserves_verified_model_label_and_raw_score(label, score, verdict):
    client = _client(posts=[_response(200, [{"label": label, "score": score}])])
    with patch.object(settings, "hf_api_token", "unit-secret"), patch(
        "adapters.hf_image.httpx.AsyncClient", return_value=client
    ):
        result = await HFImageAdapter().analyze(b"image")

    assert (result.verdict, result.confidence, result.ai_probability) == (verdict, score, None)
    assert result.provider_evidence is not None
    assert result.provider_evidence.safe_details == {
        "score_field": "top_label_score",
        "raw_label": label.upper(),
        "raw_label_score": score,
    }
    assert client.post.await_args.args[0].endswith("/dima806/deepfake_vs_real_image_detection")


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "detail"), [(401, "auth_error"), (403, "auth_error"), (429, "rate_limit")])
async def test_hf_image_maps_auth_and_rate_errors_without_secret_leakage(status, detail):
    with patch.object(settings, "hf_api_token", "unit-secret"), patch(
        "adapters.hf_image.httpx.AsyncClient", return_value=_client(posts=[_response(status)])
    ), pytest.raises(ExternalAPIError) as raised:
        await HFImageAdapter().analyze(b"image")
    assert (raised.value.service, raised.value.detail, raised.value.status_code) == (
        "huggingface", detail, status
    )
    assert "unit-secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_hf_image_maps_timeout_5xx_and_malformed_response():
    with patch.object(settings, "hf_api_token", "unit-secret"), patch(
        "adapters.hf_image.httpx.AsyncClient", return_value=_client(error=httpx.ReadTimeout("timeout"))
    ), pytest.raises(ProviderInfrastructureError) as timeout:
        await HFImageAdapter().analyze(b"image")
    assert timeout.value.kind == "timeout"

    for response in (_response(503), _response(200, [{"label": "unexpected", "score": 1}])):
        with patch.object(settings, "hf_api_token", "unit-secret"), patch(
            "adapters.hf_image.httpx.AsyncClient", return_value=_client(posts=[response])
        ), pytest.raises(ProviderInfrastructureError) as raised:
            await HFImageAdapter().analyze(b"image")
        assert raised.value.kind in {"unavailable", "invalid_response"}


def _resemble_item(*, status: str, score: object = 0.0, label: str = "real") -> dict[str, object]:
    return {
        "success": True,
        "item": {
            "uuid": "detect-1",
            "status": status,
            "media_type": "audio",
            "metrics": {"aggregated_score": score, "label": label},
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("score", "label", "verdict"), [(0, "real", Verdict.REAL), (1, "fake", Verdict.FAKE)])
async def test_resemble_v2_polls_processing_and_uses_final_aggregated_score(score, label, verdict):
    client = _client(posts=[_response(200, _resemble_item(status="processing"))], gets=[
        _response(200, _resemble_item(status="completed", score=score, label=label))
    ])
    with patch.object(settings, "resemble_api_key", "unit-secret"), patch(
        "adapters.resemble.httpx.AsyncClient", return_value=client
    ), patch("adapters.resemble.asyncio.sleep", new=AsyncMock()):
        result = await ResembleAdapter().analyze(b"WAV")

    assert (result.verdict, result.confidence, result.ai_probability) == (verdict, score, None)
    assert result.provider_evidence is not None
    assert result.provider_evidence.safe_details["score_field"] == "metrics.aggregated_score"
    assert client.get.await_count == 1
    assert client.post.await_args.kwargs["headers"]["Authorization"] == "Bearer unit-secret"


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "detail"), [(401, "auth_error"), (403, "auth_error"), (429, "rate_limit")])
async def test_resemble_v2_maps_auth_and_rate_errors(status, detail):
    client = _client(posts=[_response(status)])
    with patch.object(settings, "resemble_api_key", "unit-secret"), patch(
        "adapters.resemble.httpx.AsyncClient", return_value=client
    ), pytest.raises(ExternalAPIError) as raised:
        await ResembleAdapter().analyze(b"WAV")
    assert (raised.value.service, raised.value.detail, raised.value.status_code) == ("resemble", detail, status)
    assert "unit-secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_resemble_v2_maps_timeout_5xx_malformed_and_never_finalizes_processing():
    with patch.object(settings, "resemble_api_key", "unit-secret"), patch(
        "adapters.resemble.httpx.AsyncClient", return_value=_client(error=httpx.ReadTimeout("timeout"))
    ), pytest.raises(ProviderInfrastructureError) as timeout:
        await ResembleAdapter().analyze(b"WAV")
    assert timeout.value.kind == "timeout"

    for response in (_response(503), _response(200, {"success": True, "item": {}})):
        with patch.object(settings, "resemble_api_key", "unit-secret"), patch(
            "adapters.resemble.httpx.AsyncClient", return_value=_client(posts=[response])
        ), pytest.raises(ProviderInfrastructureError) as raised:
            await ResembleAdapter().analyze(b"WAV")
        assert raised.value.kind in {"unavailable", "invalid_response"}

    client = _client(posts=[_response(200, _resemble_item(status="processing"))], gets=[
        _response(200, _resemble_item(status="processing"))
    ])
    with patch.object(settings, "resemble_api_key", "unit-secret"), patch.object(
        ResembleAdapter, "MAX_POLLS", 1
    ), patch("adapters.resemble.httpx.AsyncClient", return_value=client), patch(
        "adapters.resemble.asyncio.sleep", new=AsyncMock()
    ), pytest.raises(ProviderInfrastructureError) as processing:
        await ResembleAdapter().analyze(b"WAV")
    assert processing.value.kind == "processing_timeout"


@pytest.mark.asyncio
async def test_sightengine_and_sapling_preserve_real_secondary_scores_and_typed_errors():
    sightengine = _client(posts=[_response(200, {
        "status": "success", "type": {"ai_generated": 0, "deepfake": 1}
    })])
    with patch.object(settings, "sightengine_api_user", "user"), patch.object(
        settings, "sightengine_api_secret", "secret"
    ), patch("adapters.sightengine.httpx.AsyncClient", return_value=sightengine):
        image = await SightengineAdapter().analyze(b"image")
    assert image.ai_probability == 0
    assert image.provider_evidence is not None
    assert image.provider_evidence.safe_details["deepfake_score"] == 1

    sapling = _client(posts=[_response(200, {
        "score": 1, "ai_fraction": 0, "sentence_scores": [["bounded", 1]]
    })])
    with patch.object(settings, "sapling_api_key", "secret"), patch(
        "adapters.sapling.httpx.AsyncClient", return_value=sapling
    ):
        text = await SaplingAdapter().analyze(b"x" * 60)
    assert text.ai_probability == 1
    assert text.provider_evidence is not None
    assert text.provider_evidence.safe_details["ai_fraction"] == 0

    for adapter, key, module in (
        (SightengineAdapter(), "sightengine_api_user", "adapters.sightengine"),
        (SaplingAdapter(), "sapling_api_key", "adapters.sapling"),
    ):
        data = b"image" if isinstance(adapter, SightengineAdapter) else b"x" * 60
        with ExitStack() as stack:
            stack.enter_context(patch.object(settings, key, "configured"))
            if isinstance(adapter, SightengineAdapter):
                stack.enter_context(patch.object(settings, "sightengine_api_secret", "configured-secret"))
            stack.enter_context(patch(
                f"{module}.httpx.AsyncClient", return_value=_client(posts=[_response(401)])
            ))
            with pytest.raises(ExternalAPIError) as auth:
                await adapter.analyze(data)
        assert auth.value.detail == "auth_error"
