"""Unit contracts for the one-request, non-grounded credibility branch."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from adapters.gemini_credibility import GeminiCredibilityAdapter
from core.config import settings
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from src.execution_deadline import ExecutionDeadline, reset_execution_deadline, set_execution_deadline


BASE_URL = "https://generativelanguage.googleapis.com"


def _response(status: int, body: object) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.json.return_value = body
    return response


def _body(index=34, confidence=0.88, summary="Материал требует дополнительной проверки.", issues=None):
    return {
        "candidates": [{"content": {"parts": [{"text": json.dumps({
            "credibility_index": index, "confidence": confidence, "summary": summary,
            "issues": [] if issues is None else issues,
        }, ensure_ascii=False)}]}}],
    }


def _complex_body():
    body = _body()
    text = body["candidates"][0]["content"]["parts"][0]["text"]
    parsed = json.loads(text)
    parsed["credible_points"] = ["Текст содержит проверяемые формулировки."]
    body["candidates"][0]["content"]["parts"][0]["text"] = json.dumps(parsed, ensure_ascii=False)
    return body


def _issue(issue_type="UNSUPPORTED_CLAIM", severity="MEDIUM", claim="Утверждение", explanation="Недостаточно подтверждений."):
    return {"type": issue_type, "severity": severity, "claim": claim, "explanation": explanation}


def _client(response=None, error=None):
    client = AsyncMock()
    client.post = AsyncMock(return_value=response, side_effect=error)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _configured_gemini():
    return patch.object(settings, "gemini_api_key", "unit-test-key"), patch.object(
        settings, "gemini_api_url", BASE_URL
    ), patch.object(settings, "gemini_model", "gemini-shared-model")


@pytest.mark.asyncio
async def test_credibility_uses_one_ordinary_generate_content_request_without_tools_or_search():
    client = _client(_response(200, _body()))
    config = _configured_gemini()
    with config[0], config[1], config[2], patch.object(
        settings, "gemini_credibility_model", "gemini-unused-dedicated-model"
    ), patch("adapters.gemini_credibility.httpx.AsyncClient", return_value=client):
        result = await GeminiCredibilityAdapter().analyze("Мамонты жили в Сибири во время плейстоцена.".encode())

    request = client.post.await_args
    assert request.args[0] == f"{BASE_URL}/v1beta/models/gemini-shared-model:generateContent"
    assert "tools" not in request.kwargs["json"]
    assert "google_search" not in str(request.kwargs["json"])
    assert "grounding" not in str(request.kwargs["json"])
    assert request.kwargs["json"]["generationConfig"]["maxOutputTokens"] == 700
    assert GeminiCredibilityAdapter.TOTAL_TIMEOUT_SECONDS - GeminiCredibilityAdapter.TRANSPORT_SAFETY_SECONDS == 11
    assert result.sources == []


@pytest.mark.asyncio
async def test_complex_credibility_request_has_one_string_part_without_response_schema_or_input_leakage():
    private_text = "Содержимое для проверки структуры credibility запроса."
    client = _client(_response(200, _complex_body()))
    logs: list[str] = []
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_credibility.httpx.AsyncClient", return_value=client):
        result = await GeminiCredibilityAdapter().analyze(private_text.encode(), complex_mode=True, diagnostic_log=logs.append)

    payload = client.post.await_args.kwargs["json"]
    part = payload["contents"][0]["parts"][0]
    assert len(payload["contents"]) == len(payload["contents"][0]["parts"]) == 1
    assert set(part) == {"text"} and isinstance(part["text"], str)
    assert payload["generationConfig"] == {
        "temperature": 0.1,
        "maxOutputTokens": GeminiCredibilityAdapter.COMPLEX_MAX_OUTPUT_TOKENS,
    }
    assert "responseJsonSchema" not in payload["generationConfig"]
    assert result.credible_points == ["Текст содержит проверяемые формулировки."]
    shape_log = next(item for item in logs if "stage=request_shape" in item)
    assert "contents=1 parts=1 part_types=text" in shape_log
    assert "response_json_schema=no" in shape_log
    assert private_text not in shape_log


@pytest.mark.asyncio
async def test_credibility_400_records_safe_google_message_and_generate_content_operation():
    private_text = "секретный текст credibility"
    client = _client(_response(400, {"error": {
        "code": 400,
        "status": "INVALID_ARGUMENT",
        "message": f"Invalid value at generationConfig.maxOutputTokens: {private_text}",
    }}))
    logs: list[str] = []
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_credibility.httpx.AsyncClient", return_value=client):
        with pytest.raises(ExternalAPIError) as raised:
            await GeminiCredibilityAdapter().analyze(private_text.encode(), complex_mode=True, diagnostic_log=logs.append)

    assert raised.value.operation == "generate_content"
    assert (raised.value.upstream_status, raised.value.upstream_code) == ("INVALID_ARGUMENT", 400)
    assert private_text not in (raised.value.provider_message or "")
    error_log = next(item for item in logs if "stage=request_error" in item)
    assert "google_status=INVALID_ARGUMENT google_code=400" in error_log
    assert private_text not in error_log


@pytest.mark.parametrize(
    ("index", "issue", "expected_verdict"),
    [
        (91, None, "HIGH_CREDIBILITY"),
        (18, _issue("FACTUAL_CONTRADICTION", "HIGH", "Динозавры живут под Ямалом.", "Утверждение противоречит общеизвестным научным данным."), "VERY_LOW_CREDIBILITY"),
        (35, _issue("LOGICAL_INCONSISTENCY", "HIGH", "Причина одновременно исключает следствие.", "Вывод не следует из исходного условия."), "LOW_CREDIBILITY"),
        (48, _issue("UNSUPPORTED_CLAIM", "MEDIUM", "Редкий факт без подтверждения.", "Недостаточно известных оснований для уверенного вывода."), "MIXED_CREDIBILITY"),
        (75, None, "MOSTLY_CREDIBLE"),
    ],
)
def test_credibility_preserves_server_validated_semantic_assessment(index, issue, expected_verdict):
    result = GeminiCredibilityAdapter._result(_response(200, _body(index=index, issues=[] if issue is None else [issue])))
    assert result.credibility_index == index
    assert result.verdict == expected_verdict
    assert result.sources == []
    assert all(item.source_refs == [] for item in result.issues)


def test_hypothetical_statement_can_return_credible_result_without_artificial_issue():
    result = GeminiCredibilityAdapter._result(_response(200, _body(
        index=82,
        summary="Гипотетическая формулировка не заявлена как установленный факт.",
        issues=[],
    )))
    assert result.verdict == "HIGH_CREDIBILITY"
    assert result.issues == []


@pytest.mark.parametrize(
    "body",
    [
        {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]},
        _body(index=True), _body(index=101), _body(confidence=True), _body(confidence=1.1),
        _body(issues=[_issue()] * 6),
        _body(issues=[{**_issue(), "source_refs": [0]}]),
    ],
)
def test_credibility_rejects_malformed_or_unsupported_provider_output(body):
    with pytest.raises(ProviderInfrastructureError) as raised:
        GeminiCredibilityAdapter._result(_response(200, body))
    assert raised.value.kind == "invalid_response"


@pytest.mark.asyncio
async def test_credibility_handles_missing_config_timeout_and_http_errors_without_provider_content():
    with patch.object(settings, "gemini_api_key", ""):
        with pytest.raises(ProviderInfrastructureError) as missing:
            await GeminiCredibilityAdapter().analyze(b"text")
    assert missing.value.kind == "missing_credentials"

    config = _configured_gemini()
    with config[0], config[1], config[2], patch(
        "adapters.gemini_credibility.httpx.AsyncClient", return_value=_client(error=httpx.ReadTimeout("timeout"))
    ):
        with pytest.raises(ProviderInfrastructureError) as timeout:
            await GeminiCredibilityAdapter().analyze(b"text")
    assert timeout.value.kind == "timeout"

    for status, error_type, category in ((400, ExternalAPIError, "request_rejected"), (429, ProviderInfrastructureError, "rate_limited"), (500, ProviderInfrastructureError, "unavailable")):
        config = _configured_gemini()
        with config[0], config[1], config[2], patch(
            "adapters.gemini_credibility.httpx.AsyncClient", return_value=_client(_response(status, {"error": {"secret": "never-log"}}))
        ):
            with pytest.raises(error_type) as raised:
                await GeminiCredibilityAdapter().analyze(b"text")
        assert raised.value.detail == category


@pytest.mark.asyncio
async def test_insufficient_deadline_does_not_start_credibility_http():
    client = _client(_response(200, _body()))
    deadline = ExecutionDeadline.from_execution_timeout(3, 1, 1)
    token = set_execution_deadline(deadline)
    config = _configured_gemini()
    try:
        with config[0], config[1], config[2], patch(
            "adapters.gemini_credibility.httpx.AsyncClient", return_value=client
        ):
            with pytest.raises(ProviderInfrastructureError) as raised:
                await GeminiCredibilityAdapter().analyze(b"text")
    finally:
        reset_execution_deadline(token)
    assert raised.value.kind == "timeout"
    client.post.assert_not_awaited()
