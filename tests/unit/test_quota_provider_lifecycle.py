"""Quota terminal-state integration for completed and failed provider chains."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from api.schemas import AnalysisResult
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from router.media_router import MediaRouter
from src.main import _analyze
from src.provider_protection import admit_provider_operation
from src.rate_limit import QuotaReservation, RateLimitError
from src.validation import TextAnalyzeRequest


class _QuotaStore:
    def __init__(self, guard=None) -> None:
        self.reservation = QuotaReservation("reservation", "user", "quota_daily", "2026-08-09", "reserved")
        self.reserve_calls = 0
        self.transitions: list[str] = []
        self.guard_provider = guard or AsyncMock()

    async def reserve_quota(self, _user_id):
        self.reserve_calls += 1
        return self.reservation

    async def transition_quota(self, reservation, target):
        assert reservation is self.reservation
        self.transitions.append(target)


def _result(verdict: Verdict) -> AnalysisResult:
    return AnalysisResult(
        verdict=verdict,
        confidence=0.5,
        model_used=ModelUsed.SAPLING,
        explanation="safe",
        media_type=MediaType.TEXT,
    )


async def _analyze_with_route(store: _QuotaStore, route):
    request = TextAnalyzeRequest(text="x" * 50)
    with patch("src.main.MediaRouter.route", new=route):
        return await _analyze(request, "jwt", quota_store=store, user_id="user")


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", [Verdict.REAL, Verdict.FAKE, Verdict.UNCERTAIN])
async def test_completed_result_finalizes_quota_exactly_once(verdict):
    store = _QuotaStore()
    await _analyze_with_route(store, AsyncMock(return_value=_result(verdict)))
    assert store.transitions == ["consumed"]


@pytest.mark.asyncio
async def test_completed_hybrid_result_consumes_quota_once():
    store = _QuotaStore()
    hybrid_result = {
        "verdict": "clean",
        "ai_verdict": "REAL",
        "ai_confidence": 0.1,
        "fact_checks": [],
        "tokens": [],
    }
    with patch("src.main.hybrid_analyzer.analyze", new=AsyncMock(return_value=hybrid_result)):
        result = await _analyze(
            TextAnalyzeRequest(text="x" * 200, mode="hybrid_text"),
            "jwt",
            quota_store=store,
            user_id="user",
        )
    assert result["verdict"] == "clean"
    assert store.transitions == ["consumed"]


@pytest.mark.asyncio
async def test_terminal_hybrid_technical_failure_refunds_quota_once():
    store = _QuotaStore()
    with patch(
        "src.main.hybrid_analyzer.analyze",
        new=AsyncMock(side_effect=ProviderInfrastructureError("sapling", "timeout")),
    ):
        with pytest.raises(ProviderInfrastructureError):
            await _analyze(
                TextAnalyzeRequest(text="x" * 200, mode="hybrid_text"),
                "jwt",
                quota_store=store,
                user_id="user",
            )
    assert store.transitions == ["refunded"]


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", [Verdict.REAL, Verdict.UNCERTAIN])
async def test_successful_fallback_finalizes_without_refund(verdict):
    store = _QuotaStore()

    async def completed_fallback(*_args, **_kwargs):
        try:
            raise ProviderInfrastructureError("primary", "timeout")
        except ProviderInfrastructureError:
            return _result(verdict)

    await _analyze_with_route(store, completed_fallback)
    assert store.transitions == ["consumed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["timeout", "transport", "unavailable", "invalid_response"])
async def test_all_technical_provider_failures_refund_exactly_once(kind):
    store = _QuotaStore()
    route = AsyncMock(side_effect=ProviderInfrastructureError("provider", kind))
    with pytest.raises(ProviderInfrastructureError):
        await _analyze_with_route(store, route)
    assert store.transitions == ["refunded"]


@pytest.mark.asyncio
async def test_ordinary_provider_error_consumes_quota_before_function_maps_it():
    store = _QuotaStore()
    route = AsyncMock(side_effect=ExternalAPIError("aiornot", "request_error"))
    with pytest.raises(ExternalAPIError):
        await _analyze_with_route(store, route)
    assert store.transitions == ["consumed"]


@pytest.mark.asyncio
async def test_mixed_technical_fallback_chain_refunds_once_and_propagates():
    store = _QuotaStore()

    async def exhausted_chain(*_args, **_kwargs):
        try:
            raise ProviderInfrastructureError("primary", "timeout")
        except ProviderInfrastructureError:
            raise ProviderInfrastructureError("fallback", "unavailable")

    with pytest.raises(ProviderInfrastructureError) as raised:
        await _analyze_with_route(store, exhausted_chain)
    assert raised.value.service == "fallback"
    assert store.transitions == ["refunded"]


@pytest.mark.asyncio
async def test_provider_guard_capacity_denial_refunds_once():
    async def deny(_provider):
        raise RateLimitError("provider_temporarily_unavailable", "safe", 503)

    store = _QuotaStore(guard=deny)

    async def admitted_operation(*_args, **_kwargs):
        await admit_provider_operation("sightengine")
        raise AssertionError("provider HTTP must not execute after guard denial")

    with pytest.raises(ProviderInfrastructureError) as raised:
        await _analyze_with_route(store, admitted_operation)
    assert raised.value.kind == "capacity"
    assert store.transitions == ["refunded"]


@pytest.mark.asyncio
async def test_provider_guard_denial_with_successful_fallback_finalizes_once():
    async def guard(provider):
        if provider == "sightengine":
            raise RateLimitError("provider_temporarily_unavailable", "safe", 503)

    store = _QuotaStore(guard=guard)
    real_route = MediaRouter.route

    async def image_route(router, *_args, **_kwargs):
        return await real_route(router, MediaType.IMAGE, b"image")

    async def primary(_adapter, _data):
        await admit_provider_operation("sightengine")
        raise AssertionError("provider HTTP must not execute after guard denial")

    async def fallback(_adapter, _data):
        await admit_provider_operation("huggingface")
        return _result(Verdict.REAL)

    with patch("src.main.MediaRouter.route", new=image_route), patch(
        "router.media_router.SightengineAdapter.analyze", new=primary
    ), patch("router.media_router.HFImageAdapter.analyze", new=fallback):
        await _analyze(TextAnalyzeRequest(text="x" * 50), "jwt", quota_store=store, user_id="user")

    assert store.transitions == ["consumed"]


@pytest.mark.asyncio
async def test_exception_propagation_cannot_trigger_double_refund():
    store = _QuotaStore()
    route = AsyncMock(side_effect=ProviderInfrastructureError("provider", "transport"))
    with pytest.raises(ProviderInfrastructureError):
        await _analyze_with_route(store, route)
    assert store.transitions.count("refunded") == 1
    assert "consumed" not in store.transitions


@pytest.mark.asyncio
async def test_aiornot_completed_result_consumes_quota_once():
    store = _QuotaStore()
    text = " ".join(["word"] * 64)
    completed = _result(Verdict.FAKE)
    completed.model_used = ModelUsed.AIORNOT_TEXT
    with patch("router.media_router.AIOrNotTextAdapter.analyze", new=AsyncMock(return_value=completed)) as aiornot, patch(
        "router.media_router.SaplingAdapter.analyze", new=AsyncMock()
    ) as sapling:
        result = await _analyze(TextAnalyzeRequest(text=text), "jwt", quota_store=store, user_id="user")
    assert store.transitions == ["consumed"]
    aiornot.assert_awaited_once()
    sapling.assert_not_awaited()
    assert result["model_used"] == "aiornot_text"
    assert result["verdict"] == "FAKE"
    assert json.loads(json.dumps(result)) == result


@pytest.mark.asyncio
async def test_aiornot_technical_failure_then_sapling_success_consumes_quota_once():
    store = _QuotaStore()
    text = " ".join(["word"] * 64)
    with patch(
        "router.media_router.AIOrNotTextAdapter.analyze",
        new=AsyncMock(side_effect=ProviderInfrastructureError("aiornot", "timeout")),
    ), patch("router.media_router.SaplingAdapter.analyze", new=AsyncMock(return_value=_result(Verdict.REAL))) as sapling:
        await _analyze(TextAnalyzeRequest(text=text), "jwt", quota_store=store, user_id="user")
    assert store.transitions == ["consumed"]
    sapling.assert_awaited_once()


@pytest.mark.asyncio
async def test_boundary_failure_then_sapling_success_consumes_one_reservation():
    store = _QuotaStore()
    text = " ".join(["word"] * 64)
    boundary_error = "Invalid `boundary` for `multipart/form-data` request"
    response = httpx.Response(400, text=boundary_error, headers={"content-type": "text/plain"})
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=client), patch(
        "router.media_router.SaplingAdapter.analyze", new=AsyncMock(return_value=_result(Verdict.REAL))
    ) as sapling:
        result = await _analyze(TextAnalyzeRequest(text=text), "jwt", quota_store=store, user_id="user")
    assert store.reserve_calls == 1
    assert store.transitions == ["consumed"]
    sapling.assert_awaited_once()
    assert result["model_used"] == "sapling"


@pytest.mark.asyncio
async def test_boundary_failure_then_technical_sapling_failure_refunds_one_reservation():
    store = _QuotaStore()
    text = " ".join(["word"] * 64)
    response = httpx.Response(
        400,
        text="Invalid `boundary` for `multipart/form-data` request",
        headers={"content-type": "text/plain"},
    )
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=client), patch(
        "router.media_router.SaplingAdapter.analyze",
        new=AsyncMock(side_effect=ProviderInfrastructureError("sapling", "unavailable")),
    ):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await _analyze(TextAnalyzeRequest(text=text), "jwt", quota_store=store, user_id="user")
    assert (raised.value.service, raised.value.kind) == ("sapling", "unavailable")
    assert store.reserve_calls == 1
    assert store.transitions == ["refunded"]


@pytest.mark.asyncio
async def test_aiornot_and_sapling_technical_failures_refund_once():
    store = _QuotaStore()
    text = " ".join(["word"] * 64)
    with patch(
        "router.media_router.AIOrNotTextAdapter.analyze",
        new=AsyncMock(side_effect=ProviderInfrastructureError("aiornot", "timeout")),
    ), patch(
        "router.media_router.SaplingAdapter.analyze",
        new=AsyncMock(side_effect=ProviderInfrastructureError("sapling", "unavailable")),
    ):
        with pytest.raises(ProviderInfrastructureError):
            await _analyze(TextAnalyzeRequest(text=text), "jwt", quota_store=store, user_id="user")
    assert store.transitions == ["refunded"]


@pytest.mark.asyncio
async def test_sightengine_technical_failure_then_hf_success_consumes_quota_once():
    store = _QuotaStore()
    real_route = MediaRouter.route

    async def image_route(router, *_args, **_kwargs):
        return await real_route(router, MediaType.IMAGE, b"image")

    image_result = AnalysisResult(
        verdict=Verdict.REAL,
        confidence=0.1,
        model_used=ModelUsed.HF_IMAGE,
        explanation="safe",
        media_type=MediaType.IMAGE,
    )
    with patch("src.main.MediaRouter.route", new=image_route), patch(
        "router.media_router.SightengineAdapter.analyze",
        new=AsyncMock(side_effect=ProviderInfrastructureError("sightengine", "unavailable")),
    ), patch("router.media_router.HFImageAdapter.analyze", new=AsyncMock(return_value=image_result)) as hf:
        await _analyze(TextAnalyzeRequest(text="x" * 50), "jwt", quota_store=store, user_id="user")
    assert store.transitions == ["consumed"]
    hf.assert_awaited_once()


@pytest.mark.asyncio
async def test_sightengine_and_hf_technical_failures_refund_once():
    store = _QuotaStore()
    real_route = MediaRouter.route

    async def image_route(router, *_args, **_kwargs):
        return await real_route(router, MediaType.IMAGE, b"image")

    with patch("src.main.MediaRouter.route", new=image_route), patch(
        "router.media_router.SightengineAdapter.analyze",
        new=AsyncMock(side_effect=ProviderInfrastructureError("sightengine", "timeout")),
    ), patch(
        "router.media_router.HFImageAdapter.analyze",
        new=AsyncMock(side_effect=ProviderInfrastructureError("huggingface", "unavailable")),
    ):
        with pytest.raises(ProviderInfrastructureError):
            await _analyze(TextAnalyzeRequest(text="x" * 50), "jwt", quota_store=store, user_id="user")
    assert store.transitions == ["refunded"]


@pytest.mark.asyncio
async def test_audio_technical_fallback_success_consumes_quota_once():
    store = _QuotaStore()
    real_route = MediaRouter.route

    async def audio_route(router, *_args, **_kwargs):
        return await real_route(router, MediaType.AUDIO, b"audio")

    fallback_result = AnalysisResult(
        verdict=Verdict.REAL,
        confidence=0.9,
        model_used=ModelUsed.HF_AUDIO,
        explanation="safe",
        media_type=MediaType.AUDIO,
    )
    with patch("src.main.MediaRouter.route", new=audio_route), patch(
        "router.media_router.ResembleAdapter.analyze",
        new=AsyncMock(side_effect=ProviderInfrastructureError("resemble", "timeout")),
    ), patch(
        "router.media_router.HFAudioAdapter.analyze", new=AsyncMock(return_value=fallback_result)
    ) as hf:
        await _analyze(TextAnalyzeRequest(text="x" * 50), "jwt", quota_store=store, user_id="user")

    assert store.transitions == ["consumed"]
    hf.assert_awaited_once()


@pytest.mark.asyncio
async def test_audio_terminal_technical_failures_refund_quota_once():
    store = _QuotaStore()
    real_route = MediaRouter.route

    async def audio_route(router, *_args, **_kwargs):
        return await real_route(router, MediaType.AUDIO, b"audio")

    with patch("src.main.MediaRouter.route", new=audio_route), patch(
        "router.media_router.ResembleAdapter.analyze",
        new=AsyncMock(side_effect=ProviderInfrastructureError("resemble", "timeout")),
    ), patch(
        "router.media_router.HFAudioAdapter.analyze",
        new=AsyncMock(side_effect=ProviderInfrastructureError("huggingface", "unavailable")),
    ):
        with pytest.raises(ProviderInfrastructureError):
            await _analyze(TextAnalyzeRequest(text="x" * 50), "jwt", quota_store=store, user_id="user")

    assert store.transitions == ["refunded"]


@pytest.mark.asyncio
async def test_direct_video_success_consumes_quota_without_legacy_pipeline():
    store = _QuotaStore()
    real_route = MediaRouter.route

    async def video_route(router, *_args, **_kwargs):
        return await real_route(router, MediaType.VIDEO, b"validated-video")

    direct_result = AnalysisResult(
        verdict=Verdict.FAKE,
        confidence=0.9,
        model_used=ModelUsed.SIGHTENGINE_VIDEO_DIRECT,
        explanation="safe",
        media_type=MediaType.VIDEO,
    )
    with patch("src.main.MediaRouter.route", new=video_route), patch(
        "router.media_router.SightengineVideoAdapter.analyze", new=AsyncMock(return_value=direct_result)
    ), patch("router.media_router.VideoPipeline.analyze", new=AsyncMock()) as legacy:
        await _analyze(TextAnalyzeRequest(text="x" * 50), "jwt", quota_store=store, user_id="user")
    assert store.transitions == ["consumed"]
    legacy.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_video_technical_failure_then_legacy_success_consumes_quota_once():
    store = _QuotaStore()
    real_route = MediaRouter.route

    async def video_route(router, *_args, **_kwargs):
        return await real_route(router, MediaType.VIDEO, b"validated-video")

    legacy_result = AnalysisResult(
        verdict=Verdict.UNCERTAIN,
        confidence=0.5,
        model_used=ModelUsed.SIGHTENGINE_VIDEO,
        explanation="safe",
        media_type=MediaType.VIDEO,
    )
    with patch("src.main.MediaRouter.route", new=video_route), patch(
        "router.media_router.SightengineVideoAdapter.analyze",
        new=AsyncMock(side_effect=ProviderInfrastructureError("sightengine", "timeout")),
    ), patch(
        "router.media_router.VideoPipeline.analyze", new=AsyncMock(return_value=legacy_result)
    ) as legacy:
        await _analyze(TextAnalyzeRequest(text="x" * 50), "jwt", quota_store=store, user_id="user")

    assert store.transitions == ["consumed"]
    legacy.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_video_and_legacy_technical_failures_refund_once():
    store = _QuotaStore()
    real_route = MediaRouter.route

    async def video_route(router, *_args, **_kwargs):
        return await real_route(router, MediaType.VIDEO, b"validated-video")

    with patch("src.main.MediaRouter.route", new=video_route), patch(
        "router.media_router.SightengineVideoAdapter.analyze",
        new=AsyncMock(side_effect=ProviderInfrastructureError("sightengine", "unavailable")),
    ), patch(
        "router.media_router.VideoPipeline.analyze",
        new=AsyncMock(side_effect=ProviderInfrastructureError("legacy", "timeout")),
    ):
        with pytest.raises(ProviderInfrastructureError):
            await _analyze(TextAnalyzeRequest(text="x" * 50), "jwt", quota_store=store, user_id="user")
    assert store.transitions == ["refunded"]
