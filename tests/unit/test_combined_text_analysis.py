"""Combined normal-TEXT orchestration: one report, parallel branches, one persistence."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.schemas import AnalysisResult, CredibilityAssessment
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ProviderInfrastructureError
from src.main import _analyze, _analyze_combined_normal_text, _execute_request
from src.validation import SecurityValidationError, validate_request_payload


def _ai_result(model=ModelUsed.GEMINI_TEXT):
    return AnalysisResult(
        verdict=Verdict.REAL, confidence=0.9, model_used=model,
        explanation="Признаки генерации не выражены.", media_type=MediaType.TEXT,
        semantics_version=2, authenticity_index=96,
    )


def _credibility_result():
    return CredibilityAssessment(
        status="completed", credibility_index=34, verdict="LOW_CREDIBILITY", confidence=0.8,
        summary="Ключевые утверждения требуют дополнительной проверки.",
    )


@pytest.mark.asyncio
async def test_combined_normal_text_runs_branches_concurrently_and_returns_one_report():
    ai_started = asyncio.Event()
    credibility_started = asyncio.Event()

    async def ai_branch(*_args, **_kwargs):
        ai_started.set()
        await asyncio.wait_for(credibility_started.wait(), timeout=0.2)
        return _ai_result()

    async def credibility_branch(*_args, **_kwargs):
        credibility_started.set()
        await asyncio.wait_for(ai_started.wait(), timeout=0.2)
        return _credibility_result()

    router = MagicMock()
    router.route = AsyncMock(side_effect=ai_branch)
    with patch("src.main.GeminiCredibilityAdapter.analyze", new=AsyncMock(side_effect=credibility_branch)):
        result = await _analyze_combined_normal_text(router, "короткий текст", None)

    assert result.model_used == ModelUsed.GEMINI_TEXT
    assert result.credibility is not None and result.credibility.status == "completed"
    assert result.credibility.credibility_index == 34
    assert "34/100" in (result.short_report or "")
    router.route.assert_awaited_once()


@pytest.mark.asyncio
async def test_short_and_long_text_keep_ai_routing_while_both_start_credibility():
    credibility = AsyncMock(return_value=_credibility_result())
    with patch("router.media_router.GeminiTextAdapter.analyze", new=AsyncMock(return_value=_ai_result())) as gemini, patch(
        "router.media_router.AIOrNotTextAdapter.analyze", new=AsyncMock(return_value=_ai_result(ModelUsed.AIORNOT_TEXT))
    ) as aiornot, patch("src.main.GeminiCredibilityAdapter.analyze", new=credibility):
        short = await _analyze(validate_request_payload({"text": "короткий текст"}), "", None)
        long_text = " ".join(["слово"] * 64)
        long = await _analyze(validate_request_payload({"text": long_text}), "", None)

    gemini.assert_awaited_once()
    aiornot.assert_awaited_once()
    assert short["model_used"] == ModelUsed.GEMINI_TEXT.value
    assert long["model_used"] == ModelUsed.AIORNOT_TEXT.value
    assert credibility.await_count == 2


@pytest.mark.asyncio
async def test_partial_credibility_failure_preserves_ai_result():
    router = MagicMock()
    router.route = AsyncMock(return_value=_ai_result())
    with patch("src.main.GeminiCredibilityAdapter.analyze", new=AsyncMock(
        side_effect=ProviderInfrastructureError("gemini", "timeout", stage="request")
    )):
        result = await _analyze_combined_normal_text(router, "текст", None)
    assert result.ai_status is None
    assert result.credibility is not None and result.credibility.status == "unavailable"


@pytest.mark.asyncio
async def test_partial_ai_failure_preserves_credibility_result():
    router = MagicMock()
    router.route = AsyncMock(side_effect=ProviderInfrastructureError("gemini", "timeout", stage="request"))
    with patch("src.main.GeminiCredibilityAdapter.analyze", new=AsyncMock(return_value=_credibility_result())):
        result = await _analyze_combined_normal_text(router, "текст", None)
    assert result.ai_status == "unavailable"
    assert result.credibility is not None and result.credibility.status == "completed"


@pytest.mark.asyncio
async def test_combined_result_is_persisted_once():
    result = _ai_result().model_copy(update={
        "credibility": _credibility_result(), "short_report": "Общий отчёт.",
    }).model_dump(mode="json")
    with patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "user", "emailVerification": True})), patch(
        "src.main.ensure_user_profile", new=AsyncMock(return_value={"$id": "user"})
    ), patch("src.main.enforce_admission", new=AsyncMock()), patch(
        "src.main.AppwriteTablesRateLimitStore"
    ), patch("src.main._analyze", new=AsyncMock(return_value=result)), patch(
        "src.main.persist_check_result", new=AsyncMock(return_value="check-1")
    ) as persist:
        response = await _execute_request({"text": "текст"}, "key", "user", "jwt")
    persist.assert_awaited_once()
    assert response["check_id"] == "check-1"
    assert response["credibility"]["credibility_index"] == 34


@pytest.mark.asyncio
async def test_authentication_failure_starts_no_provider_branch():
    with patch("src.main._analyze", new=AsyncMock()) as analyze:
        with pytest.raises(SecurityValidationError):
            await _execute_request({"text": "текст"}, "key", "", "")
    analyze.assert_not_awaited()
