"""Combined normal-TEXT orchestration: one report, parallel branches, one persistence."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.schemas import AIOriginDetails, AnalysisResult, CredibilityAssessment
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ProviderInfrastructureError
from src.main import _analyze, _analyze_combined_normal_text, _analyze_complex_text, _execute_request
from src.validation import SecurityValidationError, validate_request_payload


def _ai_result(model=ModelUsed.GEMINI_TEXT):
    return AnalysisResult(
        verdict=Verdict.REAL, confidence=0.9, model_used=model,
        explanation="Признаки генерации не выражены.", media_type=MediaType.TEXT,
        semantics_version=2, authenticity_index=96,
    )


def _credibility_result(processing_ms: int | None = None):
    return CredibilityAssessment(
        status="completed", credibility_index=34, verdict="LOW_CREDIBILITY", confidence=0.8,
        summary="Ключевые утверждения требуют дополнительной проверки.", processing_ms=processing_ms,
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
    assert result.credibility.model == "gemini_credibility"
    assert result.credibility.processing_ms is not None
    assert result.credibility.processing_ms >= 0
    assert "34/100" in (result.short_report or "")
    router.route.assert_awaited_once()


@pytest.mark.asyncio
async def test_complex_text_runs_exactly_two_parallel_gemini_branches():
    ai_started = asyncio.Event()
    credibility_started = asyncio.Event()

    async def ai_branch(*_args, **kwargs):
        assert kwargs["complex_mode"] is True
        ai_started.set()
        await asyncio.wait_for(credibility_started.wait(), timeout=0.2)
        return _ai_result().model_copy(update={"analysis_mode": "complex"})

    async def credibility_branch(*_args, **kwargs):
        assert kwargs["complex_mode"] is True
        credibility_started.set()
        await asyncio.wait_for(ai_started.wait(), timeout=0.2)
        return _credibility_result()

    with patch("src.main.GeminiTextAdapter.analyze", new=AsyncMock(side_effect=ai_branch)) as text, patch(
        "src.main.GeminiCredibilityAdapter.analyze", new=AsyncMock(side_effect=credibility_branch)
    ) as credibility:
        result = await _analyze_complex_text("длинный текст " * 100, None)

    assert result.analysis_mode == "complex"
    assert result.credibility is not None and result.credibility.status == "completed"
    text.assert_awaited_once()
    credibility.assert_awaited_once()


@pytest.mark.asyncio
async def test_unified_complex_text_only_passes_one_nonempty_string_corpus_to_both_branches():
    text = "Текстовый материал для Unified Complex. " * 12
    captured: list[tuple[bytes, bool]] = []
    logs: list[str] = []

    async def ai_branch(data, *, complex_mode, **_kwargs):
        captured.append((data, complex_mode))
        return _ai_result()

    async def credibility_branch(data, *, complex_mode, **_kwargs):
        captured.append((data, complex_mode))
        return _credibility_result()

    request = validate_request_payload({"mode": "complex", "text": text, "fileIds": []})
    with patch("src.main.GeminiTextAdapter.analyze", new=AsyncMock(side_effect=ai_branch)), patch(
        "src.main.GeminiCredibilityAdapter.analyze", new=AsyncMock(side_effect=credibility_branch)
    ):
        result = await _analyze(request, "", logs.append)

    assert result["analysis_mode"] == "complex"
    assert len(captured) == 2
    assert all(isinstance(data, bytes) and data.decode("utf-8") == text and complex_mode for data, complex_mode in captured)
    corpus_log = next(item for item in logs if item.startswith("complex_text_corpus"))
    assert "manual_text_present=yes source_text_present=no" in corpus_log
    assert f"combined_corpus_length={len(text)}" in corpus_log
    assert "combined_corpus_empty=no combined_corpus_type=str truncated=no" in corpus_log
    assert text not in corpus_log


@pytest.mark.asyncio
async def test_combined_credibility_processing_time_excludes_parallel_ai_branch_waiting():
    credibility_finished = asyncio.Event()

    async def ai_branch(*_args, **_kwargs):
        await asyncio.wait_for(credibility_finished.wait(), timeout=0.2)
        await asyncio.sleep(0.06)
        return _ai_result()

    async def credibility_branch(*_args, **_kwargs):
        await asyncio.sleep(0.005)
        credibility_finished.set()
        return _credibility_result()

    router = MagicMock()
    router.route = AsyncMock(side_effect=ai_branch)

    with patch("src.main.GeminiCredibilityAdapter.analyze", new=AsyncMock(side_effect=credibility_branch)):
        result = await _analyze_combined_normal_text(router, "текст", None)

    assert result.credibility is not None
    assert result.credibility.processing_ms is not None
    assert result.credibility.processing_ms < 50


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
    assert result.credibility.model == "gemini_credibility"
    assert result.credibility.processing_ms is None


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
        "credibility": _credibility_result(processing_ms=8120), "short_report": "Общий отчёт.",
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
    assert response["credibility"]["processing_ms"] == 8120
    assert persist.await_args.args[0]["credibility"]["processing_ms"] == 8120


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ai_outcome", "credibility_outcome", "expected_ai_status", "expected_credibility_status"),
    [
        pytest.param(
            _ai_result().model_copy(update={"ai_details": AIOriginDetails(human_signals=["Проверен стиль изложения."])}),
            _credibility_result(),
            None,
            "completed",
            id="full",
        ),
        pytest.param(
            _ai_result().model_copy(update={"ai_details": AIOriginDetails(human_signals=["Проверен стиль изложения."])}),
            ProviderInfrastructureError("gemini", "timeout", stage="request"),
            None,
            "unavailable",
            id="credibility-unavailable",
        ),
        pytest.param(
            ProviderInfrastructureError("gemini", "timeout", stage="request"),
            _credibility_result(),
            "unavailable",
            "completed",
            id="ai-unavailable",
        ),
    ],
)
async def test_unified_complex_text_only_persists_without_source_or_manual_media(
    ai_outcome,
    credibility_outcome,
    expected_ai_status,
    expected_credibility_status,
):
    """Text-only unified Complex must not require source_label/source media.

    Before the regression fix this reached successful Gemini branches, then
    raised AttributeError while persistence read ComplexAnalyzeRequest.source_label.
    """
    text = "Проверочный материал для комплексного анализа. " * 12
    ai_mock = AsyncMock(side_effect=ai_outcome) if isinstance(ai_outcome, BaseException) else AsyncMock(return_value=ai_outcome)
    credibility_mock = (
        AsyncMock(side_effect=credibility_outcome)
        if isinstance(credibility_outcome, BaseException)
        else AsyncMock(return_value=credibility_outcome)
    )
    with patch("src.main.get_authenticated_account", new=AsyncMock(return_value={"$id": "user", "emailVerification": True})), patch(
        "src.main.ensure_user_profile", new=AsyncMock(return_value={"$id": "user"})
    ), patch("src.main.AppwriteTablesRateLimitStore"
    ), patch("src.main.GeminiTextAdapter.analyze", new=ai_mock), patch(
        "src.main.GeminiCredibilityAdapter.analyze", new=credibility_mock
    ), patch("src.main.persist_check_result", new=AsyncMock(return_value="check-1")) as persist:
        response = await _execute_request(
            {"mode": "complex", "text": text, "fileIds": []},
            "key",
            "user",
            "jwt",
        )

    persist.assert_awaited_once()
    persisted_result, persisted_user, source_label, persisted_key = persist.await_args.args
    assert (persisted_user, source_label, persisted_key) == ("user", "", "key")
    assert response["check_id"] == "check-1"
    assert response["analysis_mode"] == "complex"
    assert "source" not in response and "complex_media" not in response
    assert "source" not in persisted_result and "complex_media" not in persisted_result
    assert response.get("ai_status") == expected_ai_status
    assert response["credibility"]["status"] == expected_credibility_status

    if expected_ai_status is None:
        assert response["authenticity_index"] == 96
        assert response["verdict"] == "REAL"
        assert response["confidence"] == 0.9
        assert persisted_result["authenticity_index"] == 96
        assert response["ai_details"]["human_signals"] == ["Проверен стиль изложения."]
        assert persisted_result["ai_details"]["human_signals"] == ["Проверен стиль изложения."]
    else:
        assert "authenticity_index" not in response


@pytest.mark.asyncio
async def test_authentication_failure_starts_no_provider_branch():
    with patch("src.main._analyze", new=AsyncMock()) as analyze:
        with pytest.raises(SecurityValidationError):
            await _execute_request({"text": "текст"}, "key", "", "")
    analyze.assert_not_awaited()
