"""Unit contracts for the single-call grounded text credibility branch."""

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


def _body(index=34, confidence=0.88, issues=None, chunks=None, supports=None):
    issues = issues if issues is not None else []
    chunks = chunks if chunks is not None else []
    supports = supports if supports is not None else []
    return {
        "candidates": [{
            "content": {"parts": [{"text": json.dumps({
                "credibility_index": index,
                "confidence": confidence,
                "summary": "Материал содержит утверждения, требующие дополнительной проверки.",
                "issues": issues,
            }, ensure_ascii=False)}]},
            "groundingMetadata": {"groundingChunks": chunks, "groundingSupports": supports},
        }],
    }


def _client(response):
    client = AsyncMock()
    client.post = (
        AsyncMock(side_effect=response)
        if isinstance(response, BaseException)
        else AsyncMock(return_value=response)
    )
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _configured_gemini():
    return patch.object(settings, "gemini_api_key", "unit-test-key"), patch.object(
        settings, "gemini_api_url", BASE_URL
    ), patch.object(settings, "gemini_model", "gemini-test-model")


@pytest.mark.asyncio
async def test_grounded_credibility_uses_one_generate_content_request_and_grounding_sources():
    client = _client(_response(200, _body(chunks=[
        {"web": {"title": "Официальный источник", "uri": "https://example.org/report"}},
    ])))
    config = _configured_gemini()
    with config[0], config[1], config[2], patch(
        "adapters.gemini_credibility.httpx.AsyncClient", return_value=client
    ):
        result = await GeminiCredibilityAdapter().analyze("Проверяемое утверждение".encode())

    request = client.post.await_args.kwargs["json"]
    assert client.post.await_count == 1
    assert request["tools"] == [{"google_search": {}}]
    assert "googleSearch" not in request["tools"][0]
    assert request["generationConfig"]["maxOutputTokens"] == GeminiCredibilityAdapter.MAX_OUTPUT_TOKENS
    assert result.credibility_index == 34
    assert result.verdict == "LOW_CREDIBILITY"
    assert result.sources[0].url == "https://example.org/report"


@pytest.mark.parametrize(
    ("status_code", "exception_type", "category"),
    [
        (400, ExternalAPIError, "request_rejected"),
        (401, ExternalAPIError, "auth_configuration"),
        (403, ExternalAPIError, "auth_configuration"),
        (429, ProviderInfrastructureError, "rate_limited"),
        (500, ProviderInfrastructureError, "unavailable"),
    ],
)
@pytest.mark.asyncio
async def test_grounded_credibility_classifies_http_failure_without_exposing_provider_content(
    status_code, exception_type, category,
):
    client = _client(_response(status_code, {"error": {"message": "not safe to log"}}))
    diagnostics: list[str] = []
    config = _configured_gemini()
    with config[0], config[1], config[2], patch(
        "adapters.gemini_credibility.httpx.AsyncClient", return_value=client
    ):
        with pytest.raises(exception_type) as raised:
            await GeminiCredibilityAdapter().analyze(b"text", diagnostic_log=diagnostics.append)

    assert raised.value.service == "gemini"
    assert raised.value.detail == category
    assert raised.value.status_code == status_code
    assert any(
        f"stage=request_error category={category} status_code={status_code}" in message
        for message in diagnostics
    )
    assert all("not safe to log" not in message for message in diagnostics)


@pytest.mark.asyncio
async def test_grounded_credibility_classifies_timeout_and_transport_diagnostics():
    config = _configured_gemini()
    for error, expected_stage, expected_category in (
        (httpx.ReadTimeout("timeout"), "request_timeout", "timeout"),
        (httpx.ConnectError("network"), "request_error", "transport"),
    ):
        client = _client(error)
        diagnostics: list[str] = []
        with config[0], config[1], config[2], patch(
            "adapters.gemini_credibility.httpx.AsyncClient", return_value=client
        ):
            with pytest.raises(ProviderInfrastructureError) as raised:
                await GeminiCredibilityAdapter().analyze(b"text", diagnostic_log=diagnostics.append)
        assert raised.value.kind == expected_category
        assert any(
            f"stage={expected_stage} category={expected_category} status_code=none" in message
            for message in diagnostics
        )


@pytest.mark.parametrize(
    ("index", "verdict"),
    [(0, "VERY_LOW_CREDIBILITY"), (20, "VERY_LOW_CREDIBILITY"), (21, "LOW_CREDIBILITY"),
     (40, "LOW_CREDIBILITY"), (41, "MIXED_CREDIBILITY"), (60, "MIXED_CREDIBILITY"),
     (61, "MOSTLY_CREDIBLE"), (80, "MOSTLY_CREDIBLE"), (81, "HIGH_CREDIBILITY"),
     (100, "HIGH_CREDIBILITY")],
)
def test_credibility_verdict_boundaries(index, verdict):
    assert GeminiCredibilityAdapter._verdict(index) == verdict


@pytest.mark.parametrize("invalid", [True, -1, 101, 20.0])
def test_credibility_rejects_invalid_index(invalid):
    response = _response(200, _body(index=invalid))
    with pytest.raises(ProviderInfrastructureError) as raised:
        GeminiCredibilityAdapter._result(response)
    assert raised.value.kind == "invalid_response"


@pytest.mark.parametrize("invalid", [True, float("nan"), -0.1, 1.1, "0.8"])
def test_credibility_rejects_invalid_confidence(invalid):
    response = _response(200, _body(confidence=invalid))
    with pytest.raises(ProviderInfrastructureError) as raised:
        GeminiCredibilityAdapter._result(response)
    assert raised.value.kind == "invalid_response"


def test_credibility_limits_issues_and_sources_deduplicates_and_rejects_unsafe_urls():
    issue = {
        "type": "UNSUPPORTED_CLAIM", "severity": "MEDIUM", "claim": "Утверждение",
        "explanation": "Для него нет достаточного подтверждения.", "source_refs": [],
    }
    chunks = [
        {"web": {"title": "Надёжный источник", "uri": "https://example.org/a"}},
        {"web": {"title": "Дубликат", "uri": "https://example.org/a"}},
        {"web": {"title": "Небезопасный", "uri": "javascript:alert(1)"}},
        {"web": {"title": "Локальный", "uri": "http://127.0.0.1/admin"}},
        {"web": {"title": "Числовой loopback", "uri": "http://2130706433/admin"}},
        *[{"web": {"title": f"Источник {number}", "uri": f"https://example.org/{number}"}}
          for number in range(2, 8)],
    ]
    result = GeminiCredibilityAdapter._result(_response(200, _body(issues=[issue] * 5, chunks=chunks)))
    assert len(result.issues) == 5
    assert len(result.sources) == 5
    assert [source.url for source in result.sources] == [
        "https://example.org/a", "https://example.org/2", "https://example.org/3",
        "https://example.org/4", "https://example.org/5",
    ]


def _issue(claim="Утверждение", explanation="Для него нет достаточного подтверждения.", **overrides):
    return {
        "type": "UNSUPPORTED_CLAIM", "severity": "MEDIUM", "claim": claim,
        "explanation": explanation, "source_refs": [999, "bogus"], **overrides,
    }


def _source(title, url):
    return {"web": {"title": title, "uri": url}}


def _support(text, refs):
    return {"segment": {"text": text}, "groundingChunkIndices": refs}


def test_grounding_supports_build_canonical_raw_to_final_refs_and_ignore_model_refs():
    issue = _issue("Ключевое утверждение", "Ключевое утверждение не подтверждается источниками.")
    result = GeminiCredibilityAdapter._result(_response(200, _body(
        issues=[issue],
        chunks=[_source("A", "https://a.example"), _source("Unsafe", "javascript:alert(1)"), _source("B", "https://b.example")],
        supports=[_support("Ключевое утверждение", [2])],
    )))
    assert [item.url for item in result.sources] == ["https://a.example", "https://b.example"]
    assert result.issues[0].source_refs == [1]


def test_grounding_supports_preserve_all_valid_raw_chunk_indexes():
    result = GeminiCredibilityAdapter._result(_response(200, _body(
        issues=[_issue("Третье утверждение", "Третье утверждение требует проверки.")],
        chunks=[
            _source("A", "https://a.example"), _source("B", "https://b.example"),
            _source("C", "https://c.example"),
        ],
        supports=[_support("Третье утверждение", [0, 1, 2])],
    )))
    assert [item.url for item in result.sources] == [
        "https://a.example", "https://b.example", "https://c.example",
    ]
    assert result.issues[0].source_refs == [0, 1, 2]


def test_grounding_support_duplicate_and_removed_refs_are_canonical_and_unique():
    issue = _issue("Факт", "Факт требует проверки.")
    result = GeminiCredibilityAdapter._result(_response(200, _body(
        issues=[issue],
        chunks=[_source("A", "https://a.example"), _source("B", "https://b.example"), _source("A duplicate", "https://a.example"), _source("Unsafe", "http://127.0.0.1")],
        supports=[_support("Факт", [2, 0, 2, 3, True])],
    )))
    assert [item.url for item in result.sources] == ["https://a.example", "https://b.example"]
    assert result.issues[0].source_refs == [0]


def test_unmatched_grounding_support_leaves_issue_refs_empty_without_invalidating_report():
    result = GeminiCredibilityAdapter._result(_response(200, _body(
        issues=[_issue("Утверждение", "Объяснение")],
        chunks=[_source("A", "https://a.example")],
        supports=[_support("Совершенно другой сегмент", [0])],
    )))
    assert result.issues[0].source_refs == []


def test_credibility_rejects_more_than_five_issues():
    issue = {
        "type": "UNSUPPORTED_CLAIM", "severity": "LOW", "claim": "Утверждение",
        "explanation": "Недостаточно подтверждений.", "source_refs": [],
    }
    with pytest.raises(ProviderInfrastructureError):
        GeminiCredibilityAdapter._result(_response(200, _body(issues=[issue] * 6)))


def test_credibility_rejects_non_russian_summary_and_keeps_grounding_url_without_title():
    body = _body(chunks=[{"web": {"uri": "https://example.org/source"}}])
    body["candidates"][0]["content"]["parts"][0]["text"] = json.dumps({
        "credibility_index": 60, "confidence": 0.8, "summary": "English only summary.", "issues": [],
    })
    with pytest.raises(ProviderInfrastructureError):
        GeminiCredibilityAdapter._result(_response(200, body))

    result = GeminiCredibilityAdapter._result(_response(200, _body(chunks=[
        {"web": {"uri": "https://example.org/source"}},
    ])))
    assert result.sources[0].title == "example.org"


@pytest.mark.asyncio
async def test_insufficient_deadline_does_not_start_grounded_http():
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
