"""Focused contract tests for the AI or Not Text adapter."""

from email.parser import BytesParser
from email.policy import default
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from adapters.aiornot_text import AIOrNotTextAdapter
from api.schemas import AnalysisResult
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from router.media_router import MediaRouter
from src.provider_protection import admit_provider_operation, begin_provider_budget, end_provider_budget
from src.rate_limit import RateLimitError


ELIGIBLE_TEXT = " ".join(["слово"] * 64)
BOUNDARY_ERROR = AIOrNotTextAdapter.MULTIPART_BOUNDARY_ERROR


def _response(status_code: int, body: object = None):
    if body is None:
        return httpx.Response(status_code, content=b"", headers={"content-type": "text/plain"})
    return httpx.Response(status_code, json=body)


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
async def test_request_uses_sync_endpoint_bearer_auth_and_multipart_text():
    client = _client(response=_response(200, _payload(0.95)))
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=client):
        await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    assert client.post.await_args.args[0] == AIOrNotTextAdapter.URL
    assert client.post.await_args.kwargs["headers"] == {"Authorization": "Bearer test_aiornot_key"}
    assert client.post.await_args.kwargs["files"] == {"text": (None, ELIGIBLE_TEXT)}


@pytest.mark.asyncio
async def test_smoke_sized_ascii_prose_is_one_multipart_text_field():
    sentence = (
        "A careful reviewer reads the whole passage before deciding how it was written, "
        "because ordinary prose contains varied phrasing and a consistent train of thought. "
    )
    smoke_text = (sentence * 6)[:843]
    assert 830 <= len(smoke_text) <= 843
    assert len(smoke_text.split()) > 64
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json=_payload(0.25))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=client):
        await AIOrNotTextAdapter().analyze(smoke_text.encode("utf-8"))

    request = captured["request"]
    assert request.method == "POST"
    assert str(request.url) == AIOrNotTextAdapter.URL
    assert request.url.query == b""
    assert "authorization" in request.headers
    content_type = request.headers["content-type"]
    assert content_type.startswith("multipart/form-data; boundary=")
    assert "application/x-www-form-urlencoded" not in content_type
    boundary = content_type.split("boundary=", 1)[1]
    assert boundary
    parsed = BytesParser(policy=default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + request.content
    )
    parts = list(parsed.iter_parts())
    assert len(parts) == 1
    assert parts[0].get_param("name", header="content-disposition") == "text"
    assert parts[0].get_content() == smoke_text


@pytest.mark.asyncio
async def test_confirmed_live_response_preserves_canonical_probability_semantics():
    confidence = 0.9762120842933656
    body = {"report": {"ai_text": {"confidence": confidence, "is_detected": True}}}
    with patch(
        "adapters.aiornot_text.httpx.AsyncClient",
        return_value=_client(response=_response(200, body)),
    ):
        result = await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    assert result.semantics_version == 2
    assert result.ai_probability == confidence
    assert result.authenticity_index == 2
    assert result.provider_evidence is not None
    assert result.provider_evidence.provider == "aiornot"


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
@pytest.mark.parametrize("status", [400, 401, 403, 422])
async def test_ordinary_4xx_preserves_status_without_becoming_infrastructure_failure(status):
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(response=_response(status))):
        with pytest.raises(ExternalAPIError) as raised:
            await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    assert not isinstance(raised.value, ProviderInfrastructureError)
    assert raised.value.detail == "request_error"
    assert raised.value.status_code == status


@pytest.mark.asyncio
async def test_4xx_provider_message_is_bounded_and_redacts_sensitive_values():
    raw_token = "provider-token-should-not-log"
    raw_key = "test_aiornot_key"
    body = {
        "message": (
            f"Authorization: Bearer {raw_token}\r\n"
            f"AIORNOT_API_KEY={raw_key}; invalid credentials"
        )
    }
    with patch(
        "adapters.aiornot_text.httpx.AsyncClient",
        return_value=_client(response=_response(401, body)),
    ):
        with pytest.raises(ExternalAPIError) as raised:
            await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    message = raised.value.provider_message
    assert message is not None
    assert len(message) <= 300
    assert "\r" not in message and "\n" not in message
    assert raw_token not in message
    assert raw_key not in message
    assert "Authorization=[REDACTED]" in message
    assert "AIORNOT_API_KEY=[REDACTED]" in message


@pytest.mark.asyncio
async def test_4xx_does_not_retain_provider_message_that_echoes_analyzed_text():
    with patch(
        "adapters.aiornot_text.httpx.AsyncClient",
        return_value=_client(response=_response(400, {"detail": f"invalid text: {ELIGIBLE_TEXT}"})),
    ):
        with pytest.raises(ExternalAPIError) as raised:
            await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    assert raised.value.provider_message is None


@pytest.mark.asyncio
async def test_json_4xx_retains_only_safe_structure_and_allowlisted_message():
    body = {"error": {"message": "plan does not permit text analysis", "internal": "do-not-log"}, "trace": 1}
    with patch(
        "adapters.aiornot_text.httpx.AsyncClient",
        return_value=_client(response=_response(400, body)),
    ):
        with pytest.raises(ExternalAPIError) as raised:
            await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    error = raised.value
    assert error.content_type == "application/json"
    assert error.response_length > 0
    assert error.response_keys == ("error", "trace")
    assert error.response_paths == ("error.message",)
    assert error.provider_message == "plan does not permit text analysis"
    assert "internal" not in error.response_paths


@pytest.mark.asyncio
async def test_non_json_4xx_records_metadata_without_reading_body_into_diagnostics():
    response = httpx.Response(400, text="private gateway body", headers={"content-type": "text/html; charset=utf-8"})
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(response=response)):
        with pytest.raises(ExternalAPIError) as raised:
            await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    error = raised.value
    assert (error.content_type, error.response_length) == ("text/html", len(response.content))
    assert error.response_keys == ()
    assert error.response_paths == ()
    assert error.provider_message is None


@pytest.mark.asyncio
async def test_short_plain_text_4xx_retains_harmless_provider_reason():
    reason = "some harmless provider error"
    response = httpx.Response(400, text=reason, headers={"content-type": "text/plain; charset=utf-8"})
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(response=response)):
        with pytest.raises(ExternalAPIError) as raised:
            await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    assert raised.value.provider_message == reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "secret"),
    [
        ("API key test_aiornot_key is invalid", "test_aiornot_key"),
        ("Authorization: Bearer provider-token-should-not-log", "provider-token-should-not-log"),
    ],
)
async def test_short_plain_text_4xx_redacts_keys_and_bearer_tokens(reason, secret):
    response = httpx.Response(400, text=reason, headers={"content-type": "text/plain"})
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(response=response)):
        with pytest.raises(ExternalAPIError) as raised:
            await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    assert raised.value.provider_message is not None
    assert secret not in raised.value.provider_message
    assert "[REDACTED]" in raised.value.provider_message


@pytest.mark.asyncio
async def test_plain_text_4xx_omits_echoed_submitted_text():
    response = httpx.Response(
        400,
        text=f"invalid input: {ELIGIBLE_TEXT}",
        headers={"content-type": "text/plain"},
    )
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(response=response)):
        with pytest.raises(ExternalAPIError) as raised:
            await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    assert raised.value.provider_message is None


@pytest.mark.asyncio
async def test_plain_text_4xx_over_300_bytes_is_omitted():
    response = httpx.Response(400, content=b"x" * 301, headers={"content-type": "text/plain"})
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(response=response)):
        with pytest.raises(ExternalAPIError) as raised:
            await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    assert raised.value.response_length == 301
    assert raised.value.provider_message is None


@pytest.mark.asyncio
async def test_exact_plain_text_boundary_error_is_typed_technical_failure():
    response = httpx.Response(400, text=BOUNDARY_ERROR, headers={"content-type": "text/plain"})
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(response=response)):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    assert (raised.value.service, raised.value.kind) == ("aiornot", "unavailable")


@pytest.mark.asyncio
async def test_exact_boundary_error_falls_back_to_sapling_result():
    response = httpx.Response(400, text=BOUNDARY_ERROR, headers={"content-type": "text/plain"})
    sapling_result = AnalysisResult(
        verdict=Verdict.REAL,
        confidence=0.1,
        model_used=ModelUsed.SAPLING,
        explanation="safe",
        media_type=MediaType.TEXT,
    )
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(response=response)), patch(
        "router.media_router.SaplingAdapter.analyze", new=AsyncMock(return_value=sapling_result)
    ) as sapling:
        result = await MediaRouter().route(MediaType.TEXT, b"", ELIGIBLE_TEXT)
    sapling.assert_awaited_once_with(ELIGIBLE_TEXT.encode())
    assert result is sapling_result
    assert result.model_used == ModelUsed.SAPLING


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 422])
async def test_other_aiornot_4xx_never_falls_back_to_sapling(status):
    reason = "another provider validation error"
    response = httpx.Response(status, text=reason, headers={"content-type": "text/plain"})
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(response=response)), patch(
        "router.media_router.SaplingAdapter.analyze", new=AsyncMock()
    ) as sapling:
        with pytest.raises(ExternalAPIError):
            await MediaRouter().route(MediaType.TEXT, b"", ELIGIBLE_TEXT)
    sapling.assert_not_awaited()


@pytest.mark.asyncio
async def test_boundary_fallback_admits_both_provider_operations():
    response = httpx.Response(400, text=BOUNDARY_ERROR, headers={"content-type": "text/plain"})
    admitted: list[str] = []

    async def guard(provider: str):
        admitted.append(provider)

    async def sapling_fallback(_adapter, _data: bytes):
        await admit_provider_operation("sapling")
        return AnalysisResult(
            verdict=Verdict.REAL,
            confidence=0.1,
            model_used=ModelUsed.SAPLING,
            explanation="safe",
            media_type=MediaType.TEXT,
        )

    tokens = begin_provider_budget(guard)
    try:
        with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(response=response)), patch(
            "router.media_router.SaplingAdapter.analyze", new=sapling_fallback
        ):
            await MediaRouter().route(MediaType.TEXT, b"", ELIGIBLE_TEXT)
    finally:
        end_provider_budget(tokens)
    assert admitted == ["aiornot", "sapling"]


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
