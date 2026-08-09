"""Focused contract tests for the AI or Not Voice adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from adapters.aiornot_audio import AIOrNotAudioAdapter
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from src.provider_protection import begin_provider_budget, end_provider_budget
from src.rate_limit import RateLimitError


AUDIO = b"voice-audio-bytes"


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


def _payload(verdict: str, confidence: float, **extra):
    return {"report": {"verdict": verdict, "confidence": confidence, **extra}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_verdict", "confidence", "verdict"),
    [("ai", 0.95, Verdict.FAKE), ("human", 0.05, Verdict.REAL), ("uncertain", 0.5, Verdict.UNCERTAIN)],
)
async def test_completed_voice_results_preserve_provider_confidence(provider_verdict, confidence, verdict):
    with patch(
        "adapters.aiornot_audio.httpx.AsyncClient",
        return_value=_client(response=_response(200, _payload(provider_verdict, confidence))),
    ):
        result = await AIOrNotAudioAdapter().analyze(AUDIO)
    assert result.verdict == verdict
    assert result.confidence == confidence
    assert result.model_used == ModelUsed.AIORNOT_AUDIO
    assert result.media_type == MediaType.AUDIO


@pytest.mark.asyncio
async def test_uses_voice_endpoint_bearer_auth_and_multipart_file():
    client = _client(response=_response(200, _payload("ai", 0.95)))
    with patch("adapters.aiornot_audio.httpx.AsyncClient", return_value=client):
        result = await AIOrNotAudioAdapter().analyze(AUDIO)
    assert client.post.await_args.args[0] == AIOrNotAudioAdapter.URL
    assert client.post.await_args.kwargs["headers"] == {"Authorization": "Bearer test_aiornot_key"}
    assert client.post.await_args.kwargs["files"] == {"file": ("audio.mp3", AUDIO, "audio/mpeg")}
    assert "test_aiornot_key" not in result.explanation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "kind"),
    [(httpx.ReadTimeout("x"), "timeout"), (httpx.ConnectError("x"), "transport")],
)
async def test_timeout_and_transport_are_typed(error, kind):
    with patch("adapters.aiornot_audio.httpx.AsyncClient", return_value=_client(error=error)):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await AIOrNotAudioAdapter().analyze(AUDIO)
    assert (raised.value.service, raised.value.kind) == ("aiornot", kind)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_capacity_and_5xx_are_typed_unavailable(status):
    with patch("adapters.aiornot_audio.httpx.AsyncClient", return_value=_client(response=_response(status))):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await AIOrNotAudioAdapter().analyze(AUDIO)
    assert raised.value.kind == "unavailable"


@pytest.mark.asyncio
async def test_normal_4xx_is_not_infrastructure_failure():
    with patch("adapters.aiornot_audio.httpx.AsyncClient", return_value=_client(response=_response(422))):
        with pytest.raises(ExternalAPIError) as raised:
            await AIOrNotAudioAdapter().analyze(AUDIO)
    assert not isinstance(raised.value, ProviderInfrastructureError)
    assert raised.value.detail == "request_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [{}, {"report": {}}, _payload("other", 0.5), _payload("ai", "bad")],
)
async def test_malformed_success_is_typed_without_raw_payload_or_secret_leak(body):
    raw = "provider-internal-payload"
    if isinstance(body, dict):
        body["raw"] = raw
    with patch("adapters.aiornot_audio.httpx.AsyncClient", return_value=_client(response=_response(200, body))):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await AIOrNotAudioAdapter().analyze(AUDIO)
    assert str(raised.value) == "aiornot: invalid_response"
    assert raw not in str(raised.value)
    assert "test_aiornot_key" not in str(raised.value)


@pytest.mark.asyncio
async def test_guard_denial_prevents_outbound_http():
    async def deny(provider):
        assert provider == "aiornot"
        raise RateLimitError("provider_temporarily_unavailable", "safe", 503)

    tokens = begin_provider_budget(deny)
    try:
        with patch("adapters.aiornot_audio.httpx.AsyncClient") as client:
            with pytest.raises(ProviderInfrastructureError) as raised:
                await AIOrNotAudioAdapter().analyze(AUDIO)
    finally:
        end_provider_budget(tokens)
    assert raised.value.kind == "capacity"
    client.assert_not_called()
