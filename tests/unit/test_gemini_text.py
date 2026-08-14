"""Unit contracts for normal TEXT Gemini verification."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from adapters.gemini_text import GeminiTextAdapter
from core.config import settings
from core.enums import ModelUsed, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from src.execution_deadline import (
    ExecutionDeadline,
    ExecutionDeadlineExceeded,
    reset_execution_deadline,
    set_execution_deadline,
)


BASE_URL = "https://generativelanguage.googleapis.com"


def _response(status: int, body: object = None) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.json.return_value = body
    return response


def _generated(verdict: object, index: int, confidence: float, summary: str) -> dict[str, object]:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "verdict": verdict,
                                    "authenticity_index": index,
                                    "confidence": confidence,
                                    "reasoning_summary": summary,
                                },
                                ensure_ascii=False,
                            )
                        }
                    ]
                }
            }
        ]
    }


def _complex_generated() -> dict[str, object]:
    body = _generated("REAL", 96, 0.9, "Текст выглядит естественным и связным.")
    text = body["candidates"][0]["content"]["parts"][0]["text"]  # type: ignore[index]
    parsed = json.loads(text)
    parsed.update({"signals": [], "human_signals": ["Стиль изложения неоднороден."]})
    body["candidates"][0]["content"]["parts"][0]["text"] = json.dumps(parsed, ensure_ascii=False)  # type: ignore[index]
    return body


def _client(*, response: MagicMock | None = None, error: Exception | None = None) -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(return_value=response, side_effect=error)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _configured_gemini():
    return patch.object(settings, "gemini_api_key", "unit-test-key"), patch.object(
        settings, "gemini_api_url", BASE_URL
    ), patch.object(settings, "gemini_model", "gemini-test-model")


@pytest.mark.asyncio
async def test_text_uses_generate_content_structured_json_without_files_api():
    client = _client(
        response=_response(
            200,
            _generated("UNCERTAIN", 50, 0.4, "Фрагмент слишком короткий для уверенного вывода."),
        )
    )
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_text.httpx.AsyncClient", return_value=client):
        result = await GeminiTextAdapter().analyze("Привет".encode("utf-8"))

    request_url = client.post.await_args.args[0]
    request = client.post.await_args.kwargs
    assert request_url == f"{BASE_URL}/v1beta/models/gemini-test-model:generateContent"
    assert "/files" not in request_url and "/upload/" not in request_url
    assert request["headers"]["x-goog-api-key"] == "unit-test-key"
    assert request["json"]["generationConfig"]["responseMimeType"] == "application/json"
    assert request["json"]["generationConfig"]["responseJsonSchema"] == GeminiTextAdapter._RESPONSE_SCHEMA
    assert "Привет" in request["json"]["contents"][0]["parts"][0]["text"]
    assert result.verdict == Verdict.UNCERTAIN
    assert result.model_used == ModelUsed.GEMINI_TEXT


@pytest.mark.asyncio
async def test_complex_text_request_has_one_string_part_and_structured_output_shape_without_leaking_input():
    private_text = "Содержимое только для проверки структуры запроса."
    client = _client(response=_response(200, _complex_generated()))
    logs: list[str] = []
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_text.httpx.AsyncClient", return_value=client):
        await GeminiTextAdapter().analyze(private_text.encode("utf-8"), complex_mode=True, diagnostic_log=logs.append)

    payload = client.post.await_args.kwargs["json"]
    part = payload["contents"][0]["parts"][0]
    assert len(payload["contents"]) == len(payload["contents"][0]["parts"]) == 1
    assert set(part) == {"text"} and isinstance(part["text"], str)
    assert payload["generationConfig"] == {
        "responseMimeType": "application/json",
        "responseJsonSchema": GeminiTextAdapter._COMPLEX_RESPONSE_SCHEMA,
        "maxOutputTokens": GeminiTextAdapter.COMPLEX_MAX_OUTPUT_TOKENS,
    }
    shape_log = next(item for item in logs if "stage=request_shape" in item)
    assert "contents=1 parts=1 part_types=text" in shape_log
    assert "response_json_schema=yes" in shape_log
    assert private_text not in shape_log


@pytest.mark.asyncio
async def test_text_keeps_using_shared_model_when_credibility_model_is_configured():
    client = _client(response=_response(200, _generated("REAL", 90, 0.8, "Нормальный текст.")))
    config = _configured_gemini()
    with config[0], config[1], config[2], patch.object(
        settings, "gemini_credibility_model", "gemini-2.5-flash-lite"
    ), patch("adapters.gemini_text.httpx.AsyncClient", return_value=client):
        await GeminiTextAdapter().analyze(b"text")

    assert client.post.await_args.args[0] == f"{BASE_URL}/v1beta/models/gemini-test-model:generateContent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verdict", "index", "confidence", "summary"),
    [
        ("REAL", 90, 0.8, "Текст выглядит естественным и не содержит явных шаблонных признаков."),
        ("FAKE", 12, 0.93, "В тексте заметны повторяющиеся шаблоны и неестественная связность."),
        ("UNCERTAIN", 50, 0.4, "Для надёжной оценки недостаточно контекста и объёма текста."),
    ],
)
async def test_structured_result_preserves_canonical_authenticity_index(
    verdict, index, confidence, summary
):
    client = _client(response=_response(200, _generated(verdict, index, confidence, summary)))
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_text.httpx.AsyncClient", return_value=client):
        result = await GeminiTextAdapter().analyze("Короткий русский текст.".encode("utf-8"))

    assert result.verdict == Verdict(verdict)
    assert result.authenticity_index == index
    assert result.confidence == confidence
    assert result.explanation == summary
    assert result.provider_evidence is not None
    assert result.provider_evidence.provider == "gemini"
    assert result.provider_evidence.raw_score == index / 100


@pytest.mark.asyncio
async def test_known_gemini_prefix_is_removed_only_at_the_start_of_summary():
    supplied = "Gemini Text Verification: Текст выглядит естественным. Gemini: внутри не меняется."
    expected = "Текст выглядит естественным. Gemini: внутри не меняется."
    client = _client(response=_response(200, _generated("REAL", 90, 0.8, supplied)))
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_text.httpx.AsyncClient", return_value=client):
        result = await GeminiTextAdapter().analyze(b"test")
    assert result.explanation == expected


def test_summary_without_known_prefix_is_unchanged():
    summary = "Текст не содержит выраженных признаков синтетической генерации."
    assert GeminiTextAdapter._sanitize_summary(summary) == summary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]},
        _generated([], 50, 0.5, "Безопасный текст."),
        _generated({}, 50, 0.5, "Безопасный текст."),
        _generated(None, 50, 0.5, "Безопасный текст."),
        _generated(123, 50, 0.5, "Безопасный текст."),
        _generated("INVALID", 50, 0.5, "Безопасный текст."),
        _generated("REAL", -1, 0.5, "Безопасный текст."),
        _generated("REAL", 101, 0.5, "Безопасный текст."),
        _generated("REAL", 50, -0.1, "Безопасный текст."),
        _generated("REAL", 50, 1.1, "Безопасный текст."),
        _generated("REAL", 50, 0.5, ""),
    ],
)
async def test_invalid_provider_response_is_typed_and_does_not_leak_input(body):
    private_text = "секретный пользовательский текст"
    client = _client(response=_response(200, body))
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_text.httpx.AsyncClient", return_value=client):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await GeminiTextAdapter().analyze(private_text.encode("utf-8"))
    assert (raised.value.service, raised.value.kind, raised.value.stage) == (
        "gemini", "invalid_response", "response"
    )
    assert private_text not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_429_and_5xx_are_typed_as_temporary_provider_failures(status):
    client = _client(response=_response(status))
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_text.httpx.AsyncClient", return_value=client):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await GeminiTextAdapter().analyze(b"test")
    assert (raised.value.service, raised.value.kind, raised.value.status_code) == (
        "gemini", "unavailable", status
    )


@pytest.mark.asyncio
async def test_ordinary_4xx_is_a_provider_request_error():
    client = _client(response=_response(400))
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_text.httpx.AsyncClient", return_value=client):
        with pytest.raises(ExternalAPIError) as raised:
            await GeminiTextAdapter().analyze(b"test")
    assert (raised.value.service, raised.value.detail, raised.value.status_code) == (
        "gemini", "request_error", 400
    )


@pytest.mark.asyncio
async def test_400_preserves_safe_google_field_message_but_redacts_analyzed_text():
    private_text = "секретный пользовательский текст"
    response = _response(400, {"error": {
        "code": 400,
        "status": "INVALID_ARGUMENT",
        "message": f'Invalid JSON payload at generationConfig.responseJsonSchema: {private_text}',
    }})
    client = _client(response=response)
    logs: list[str] = []
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_text.httpx.AsyncClient", return_value=client):
        with pytest.raises(ExternalAPIError) as raised:
            await GeminiTextAdapter().analyze(private_text.encode("utf-8"), complex_mode=True, diagnostic_log=logs.append)

    assert raised.value.operation == "generate_content"
    assert (raised.value.upstream_status, raised.value.upstream_code) == ("INVALID_ARGUMENT", 400)
    assert private_text not in (raised.value.provider_message or "")
    error_log = next(item for item in logs if "stage=request_error" in item)
    assert "google_status=INVALID_ARGUMENT google_code=400" in error_log
    assert private_text not in error_log


@pytest.mark.asyncio
async def test_timeout_is_typed():
    client = _client(error=httpx.ReadTimeout("timed out"))
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_text.httpx.AsyncClient", return_value=client):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await GeminiTextAdapter().analyze(b"test")
    assert (raised.value.service, raised.value.kind) == ("gemini", "timeout")


@pytest.mark.asyncio
async def test_slow_async_provider_is_cancelled_inside_transport_budget():
    async def slow_post(*_args, **_kwargs):
        await asyncio.sleep(0.05)
        return _response(200, _generated("REAL", 90, 0.8, "Безопасный текст."))

    client = _client()
    client.post = slow_post
    config = _configured_gemini()
    with config[0], config[1], config[2], patch.object(
        GeminiTextAdapter, "TOTAL_TIMEOUT_SECONDS", 0.01
    ), patch.object(GeminiTextAdapter, "TRANSPORT_SAFETY_SECONDS", 0.001), patch(
        "adapters.gemini_text.httpx.AsyncClient", return_value=client
    ):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await GeminiTextAdapter().analyze(b"test")
    assert (raised.value.service, raised.value.kind, raised.value.stage) == (
        "gemini", "timeout", "request"
    )


@pytest.mark.asyncio
async def test_transport_timeout_uses_remaining_analysis_budget_minus_reserve():
    now = time.monotonic()
    deadline = ExecutionDeadline(now, now + 20, now + 2.5, now + 18)
    token = set_execution_deadline(deadline)
    client = _client(response=_response(200, _generated("REAL", 90, 0.8, "Безопасный текст.")))
    config = _configured_gemini()
    try:
        with config[0], config[1], config[2], patch("adapters.gemini_text.httpx.AsyncClient") as async_client:
            async_client.return_value = client
            await GeminiTextAdapter().analyze(b"test")
    finally:
        reset_execution_deadline(token)
    transport_timeout = async_client.call_args.kwargs["timeout"]
    assert 0 < transport_timeout.connect <= 0.5
    assert 0 < transport_timeout.read <= 0.5
    assert 0 < transport_timeout.write <= 0.5
    assert 0 < transport_timeout.pool <= 0.5


@pytest.mark.asyncio
async def test_safe_timing_logs_exclude_request_content_and_credentials():
    client = _client(response=_response(200, _generated("REAL", 90, 0.8, "Безопасный текст.")))
    logs: list[str] = []
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_text.httpx.AsyncClient", return_value=client):
        await GeminiTextAdapter().analyze(b"private text", diagnostic_log=logs.append)
    assert any("stage=request_start" in item for item in logs)
    assert any("stage=request_success" in item for item in logs)
    assert any("stage=normalize" in item for item in logs)
    assert all("private text" not in item and "unit-test-key" not in item for item in logs)


@pytest.mark.asyncio
async def test_missing_key_prevents_outbound_http():
    with patch.object(settings, "gemini_api_key", ""), patch("adapters.gemini_text.httpx.AsyncClient") as client:
        with pytest.raises(ProviderInfrastructureError) as raised:
            await GeminiTextAdapter().analyze(b"test")
    assert (raised.value.kind, raised.value.stage) == ("missing_credentials", "config")
    client.assert_not_called()


@pytest.mark.asyncio
async def test_root_deadline_prevents_outbound_http():
    now = time.monotonic()
    token = set_execution_deadline(ExecutionDeadline(now, now + 1, now - 0.01, now + 0.5))
    config = _configured_gemini()
    try:
        with config[0], config[1], config[2], patch("adapters.gemini_text.httpx.AsyncClient") as client:
            with pytest.raises(ExecutionDeadlineExceeded):
                await GeminiTextAdapter().analyze(b"test")
    finally:
        reset_execution_deadline(token)
    client.assert_not_called()
