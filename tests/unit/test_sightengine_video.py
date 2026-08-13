"""Focused tests for direct Sightengine AI-generated video detection."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from adapters.sightengine_video import SightengineVideoAdapter
from adapters.video_pipeline import VideoPipeline
from api.schemas import AnalysisResult
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from router.media_router import MediaRouter
from src.provider_protection import begin_provider_budget, end_provider_budget
from src.rate_limit import RateLimitError


VIDEO = b"validated-video-bytes"


def _response(status_code: int, body: object = None):
    response = MagicMock(status_code=status_code)
    response.json.return_value = body
    return response


def _client(*, response=None, error=None):
    client = AsyncMock()
    client.post = AsyncMock(return_value=response, side_effect=error)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _payload(*scores: float, **extra):
    return {
        "status": "success",
        "data": {"frames": [{"type": {"ai_generated": score}} for score in scores]},
        **extra,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scores", "verdict", "confidence"),
    [((0.95, 0.2), Verdict.FAKE, 0.95), ((0.1, 0.2), Verdict.REAL, 0.2), ((0.5, 0.2), Verdict.UNCERTAIN, 0.5)],
)
async def test_completed_video_results_use_only_direct_genai_scores(scores, verdict, confidence):
    with patch(
        "adapters.sightengine_video.httpx.AsyncClient",
        return_value=_client(response=_response(200, _payload(*scores))),
    ):
        result = await SightengineVideoAdapter().analyze(VIDEO)
    assert result.verdict == verdict
    assert result.confidence == confidence
    assert result.model_used == ModelUsed.SIGHTENGINE_VIDEO_DIRECT
    assert result.media_type == MediaType.VIDEO


@pytest.mark.asyncio
async def test_uses_sync_endpoint_genai_model_and_shared_sightengine_credentials():
    client = _client(response=_response(200, _payload(0.95)))
    with patch("adapters.sightengine_video.httpx.AsyncClient", return_value=client):
        result = await SightengineVideoAdapter().analyze(VIDEO)
    assert client.post.await_args.args[0] == SightengineVideoAdapter.URL
    assert client.post.await_args.kwargs["data"] == {
        "api_user": "test_se_user",
        "api_secret": "test_se_secret",
        "models": "genai",
    }
    assert client.post.await_args.kwargs["files"] == {"media": ("video.mp4", VIDEO, "video/mp4")}
    assert "test_se_secret" not in result.explanation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "kind"),
    [(httpx.ReadTimeout("x"), "timeout"), (httpx.ConnectError("x"), "transport")],
)
async def test_timeout_and_transport_are_typed(error, kind):
    with patch("adapters.sightengine_video.httpx.AsyncClient", return_value=_client(error=error)):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await SightengineVideoAdapter().analyze(VIDEO)
    assert (raised.value.service, raised.value.kind) == ("sightengine", kind)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_temporary_capacity_and_5xx_are_typed_unavailable(status):
    with patch("adapters.sightengine_video.httpx.AsyncClient", return_value=_client(response=_response(status))):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await SightengineVideoAdapter().analyze(VIDEO)
    assert raised.value.kind == "unavailable"


@pytest.mark.asyncio
async def test_normal_4xx_is_not_infrastructure_failure():
    body = {"status": "failure", "error": {"code": "invalid_model", "message": "Unknown model"}}
    with patch("adapters.sightengine_video.httpx.AsyncClient", return_value=_client(response=_response(422, body))):
        with pytest.raises(ExternalAPIError) as raised:
            await SightengineVideoAdapter().analyze(VIDEO)
    assert not isinstance(raised.value, ProviderInfrastructureError)
    assert raised.value.detail == "request_error"
    assert raised.value.status_code == 422
    assert raised.value.provider_message == "code=invalid_model message=Unknown model"


@pytest.mark.asyncio
async def test_4xx_diagnostic_omits_secret_shaped_provider_message():
    body = {
        "error": {
            "code": "invalid_credentials",
            "message": "Authorization: Bearer provider-secret",
        }
    }
    with patch("adapters.sightengine_video.httpx.AsyncClient", return_value=_client(response=_response(401, body))):
        with pytest.raises(ExternalAPIError) as raised:
            await SightengineVideoAdapter().analyze(VIDEO)
    assert raised.value.provider_message == "code=invalid_credentials"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"status": "success", "data": {}}, _payload("bad")])
async def test_malformed_success_is_typed_and_does_not_leak_provider_payload(body):
    raw = "provider-internal-payload"
    if isinstance(body, dict):
        body["raw"] = raw
    with patch("adapters.sightengine_video.httpx.AsyncClient", return_value=_client(response=_response(200, body))):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await SightengineVideoAdapter().analyze(VIDEO)
    assert str(raised.value) == "sightengine: invalid_response"
    assert raw not in str(raised.value)
    assert "test_se_secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_guard_denial_prevents_outbound_http():
    async def deny(provider):
        assert provider == "sightengine"
        raise RateLimitError("provider_temporarily_unavailable", "safe", 503)

    tokens = begin_provider_budget(deny)
    try:
        with patch("adapters.sightengine_video.httpx.AsyncClient") as client:
            with pytest.raises(ProviderInfrastructureError) as raised:
                await SightengineVideoAdapter().analyze(VIDEO)
    finally:
        end_provider_budget(tokens)
    assert raised.value.kind == "capacity"
    client.assert_not_called()



@pytest.mark.asyncio
async def test_direct_success_routes_result():
    result = AnalysisResult(
        verdict=Verdict.FAKE,
        confidence=0.9,
        model_used=ModelUsed.GEMINI_VIDEO,
        explanation="safe",
        media_type=MediaType.VIDEO,
    )

    with patch(
        "router.media_router.GeminiVideoAdapter.analyze",
        new=AsyncMock(return_value=result),
    ) as gemini, patch.object(VideoPipeline, "analyze", new=AsyncMock()) as legacy_pipeline, patch(
        "adapters.sightengine_video.SightengineVideoAdapter.analyze", new=AsyncMock()
    ) as sightengine:
        routed = await MediaRouter().route(MediaType.VIDEO, VIDEO)

    assert routed is result
    gemini.assert_awaited_once_with(VIDEO, mime_type="video/mp4")
    sightengine.assert_not_awaited()
    legacy_pipeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_technical_failure_propagates():
    with patch(
        "router.media_router.GeminiVideoAdapter.analyze",
        new=AsyncMock(
            side_effect=ProviderInfrastructureError("gemini", "timeout")
        ),
    ):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await MediaRouter().route(MediaType.VIDEO, VIDEO)

    assert (raised.value.service, raised.value.kind) == ("gemini", "timeout")
