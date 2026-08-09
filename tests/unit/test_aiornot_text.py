"""Focused contract tests for the AI or Not Text adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from adapters.aiornot_text import AIOrNotTextAdapter
from api.schemas import AnalysisResult
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from router.media_router import MediaRouter
from src.provider_protection import begin_provider_budget, end_provider_budget
from src.rate_limit import RateLimitError


ELIGIBLE_TEXT = " ".join(["слово"] * 64)


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


def _payload(confidence: float, detected: bool = True, **extra):
    return {
        "report": {"ai_text": {"confidence": confidence, "is_detected": detected, **extra}},
        "metadata": {"character_count": len(ELIGIBLE_TEXT), "word_count": 64},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("confidence", "detected", "verdict"),
    [(0.95, True, Verdict.FAKE), (0.05, False, Verdict.REAL), (0.50, True, Verdict.UNCERTAIN)],
)
async def test_completed_responses_preserve_ai_probability(confidence, detected, verdict):
    with patch(
        "adapters.aiornot_text.httpx.AsyncClient",
        return_value=_client(response=_response(200, _payload(confidence, detected))),
    ):
        result = await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    assert result.verdict == verdict
    assert result.confidence == confidence
    assert result.model_used == ModelUsed.AIORNOT_TEXT


@pytest.mark.asyncio
async def test_request_uses_sync_endpoint_bearer_auth_and_form_text():
    client = _client(response=_response(200, _payload(0.95)))
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=client):
        await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    assert client.post.await_args.args[0] == AIOrNotTextAdapter.URL
    assert client.post.await_args.kwargs["headers"] == {"Authorization": "Bearer test_aiornot_key"}
    assert client.post.await_args.kwargs["data"] == {"text": ELIGIBLE_TEXT}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "kind"),
    [(httpx.ReadTimeout("x"), "timeout"), (httpx.ConnectError("x"), "transport")],
)
async def test_timeout_and_transport_are_typed(error, kind):
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(error=error)):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    assert (raised.value.service, raised.value.kind) == ("aiornot", kind)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503])
async def test_5xx_is_typed_unavailable(status):
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(response=_response(status))):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    assert raised.value.kind == "unavailable"


@pytest.mark.asyncio
async def test_4xx_is_not_misclassified_as_infrastructure_failure():
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(response=_response(422))):
        with pytest.raises(ExternalAPIError) as raised:
            await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    assert not isinstance(raised.value, ProviderInfrastructureError)
    assert raised.value.detail == "request_error"


@pytest.mark.asyncio
async def test_429_is_typed_temporary_unavailability_without_raw_error_leak():
    raw_error = "provider throttled request=internal"
    with patch(
        "adapters.aiornot_text.httpx.AsyncClient",
        return_value=_client(response=_response(429, {"detail": raw_error})),
    ):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    assert str(raised.value) == "aiornot: unavailable"
    assert raw_error not in str(raised.value)


@pytest.mark.asyncio
async def test_429_makes_sapling_fallback_available_without_raw_error_leak():
    sapling_result = AnalysisResult(
        verdict=Verdict.REAL,
        confidence=0.1,
        model_used=ModelUsed.SAPLING,
        explanation="safe",
        media_type=MediaType.TEXT,
    )
    with patch(
        "adapters.aiornot_text.httpx.AsyncClient",
        return_value=_client(response=_response(429, {"detail": "provider throttled"})),
    ), patch(
        "router.media_router.SaplingAdapter.analyze", new=AsyncMock(return_value=sapling_result)
    ) as sapling:
        result = await MediaRouter().route(MediaType.TEXT, b"", ELIGIBLE_TEXT)
    assert result is sapling_result
    sapling.assert_awaited_once()
    assert "throttled" not in result.explanation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [{}, {"report": {}}, {"report": {"ai_text": {"confidence": "bad", "is_detected": True}}}],
)
async def test_malformed_success_payload_is_typed_and_does_not_leak_data(body):
    text = f"private input {ELIGIBLE_TEXT}"
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(response=_response(200, body))):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await AIOrNotTextAdapter().analyze(text.encode())
    assert str(raised.value) == "aiornot: invalid_response"
    assert text not in str(raised.value)
    assert "test_aiornot_key" not in str(raised.value)


@pytest.mark.asyncio
async def test_extra_provider_fields_are_ignored_and_not_exposed():
    raw_text = "provider raw text"
    body = _payload(0.95, annotations=[[raw_text, 0.99]], request_id="internal")
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(response=_response(200, body))):
        result = await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    assert raw_text not in result.explanation
    assert not hasattr(result, "annotations")


def test_eligibility_requires_both_provider_minimums():
    assert AIOrNotTextAdapter.is_eligible(ELIGIBLE_TEXT)
    assert not AIOrNotTextAdapter.is_eligible("x" * 250)
    assert not AIOrNotTextAdapter.is_eligible("word " * 63)


@pytest.mark.asyncio
async def test_ineligible_text_does_not_admit_or_send_a_provider_request():
    with patch("adapters.aiornot_text.admit_provider_operation", new=AsyncMock()) as admit, patch(
        "adapters.aiornot_text.httpx.AsyncClient"
    ) as client:
        with pytest.raises(ValueError):
            await AIOrNotTextAdapter().analyze(("word " * 63).encode())
    admit.assert_not_awaited()
    client.assert_not_called()


@pytest.mark.asyncio
async def test_guard_denial_prevents_outbound_http():
    async def deny(_provider):
        raise RateLimitError("provider_temporarily_unavailable", "safe", 503)

    tokens = begin_provider_budget(deny)
    try:
        with patch("adapters.aiornot_text.httpx.AsyncClient") as client:
            with pytest.raises(ProviderInfrastructureError) as raised:
                await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    finally:
        end_provider_budget(tokens)
    assert raised.value.kind == "capacity"
    client.assert_not_called()


@pytest.mark.asyncio
async def test_guard_denial_falls_back_to_sapling_without_aiornot_http():
    async def guard(provider):
        if provider == "aiornot":
            raise RateLimitError("provider_temporarily_unavailable", "safe", 503)

    sapling_result = AnalysisResult(
        verdict=Verdict.REAL,
        confidence=0.1,
        model_used=ModelUsed.SAPLING,
        explanation="safe",
        media_type=MediaType.TEXT,
    )
    tokens = begin_provider_budget(guard)
    try:
        with patch("adapters.aiornot_text.httpx.AsyncClient") as client, patch(
            "router.media_router.SaplingAdapter.analyze", new=AsyncMock(return_value=sapling_result)
        ) as sapling:
            result = await MediaRouter().route(MediaType.TEXT, b"", ELIGIBLE_TEXT)
    finally:
        end_provider_budget(tokens)
    assert result is sapling_result
    client.assert_not_called()
    sapling.assert_awaited_once()
