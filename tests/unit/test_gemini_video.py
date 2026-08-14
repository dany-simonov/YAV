"""Unit tests for the server-side Gemini VIDEO File API adapter."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from adapters.gemini_video import GeminiVideoAdapter
from api.schemas import AnalysisResult
from core.config import settings
from core.enums import MediaType, ModelUsed, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError
from router.media_router import MediaRouter
from src.execution_deadline import ExecutionDeadline, ExecutionDeadlineExceeded, reset_execution_deadline, set_execution_deadline


VIDEO = b"validated-video-bytes"
BASE_URL = "https://generativelanguage.googleapis.com"


def _response(status: int, body: object = None, *, headers: dict[str, str] | None = None) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.headers = headers or {}
    response.json.return_value = body
    return response


def _file(state: str = "ACTIVE", *, wrapped: bool = True) -> dict[str, object]:
    file = {
        "name": "files/yav-video-1",
        "uri": f"{BASE_URL}/v1beta/files/yav-video-1",
        "state": state,
        "mimeType": "video/mp4",
    }
    return {"file": file} if wrapped else file


def _generated(
    verdict: str, index: int, confidence: float, summary: str = "Visible evidence reviewed."
) -> dict[str, object]:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": (
                                '{"verdict":"%s","authenticity_index":%s,'
                                '"confidence":%s,"reasoning_summary":%s}'
                            )
                            % (verdict, index, confidence, json.dumps(summary, ensure_ascii=False))
                        }
                    ]
                }
            }
        ]
    }


def _client(*, posts: list[MagicMock], gets: list[MagicMock] | None = None) -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(side_effect=posts)
    client.get = AsyncMock(side_effect=gets or [])
    client.delete = AsyncMock(return_value=_response(200))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _configured_gemini():
    return patch.object(settings, "gemini_api_key", "unit-test-key"), patch.object(
        settings, "gemini_api_url", BASE_URL
    ), patch.object(settings, "gemini_model", "gemini-test-model")


@pytest.mark.asyncio
async def test_video_router_uses_gemini_with_verified_mime_type():
    expected = AnalysisResult(
        verdict=Verdict.REAL,
        confidence=0.9,
        model_used=ModelUsed.GEMINI_VIDEO,
        explanation="safe",
        media_type=MediaType.VIDEO,
    )
    with patch(
        "router.media_router.GeminiVideoAdapter.analyze", new=AsyncMock(return_value=expected)
    ) as analyze:
        actual = await MediaRouter().route(MediaType.VIDEO, VIDEO, mime_type="video/quicktime")

    assert actual is expected
    analyze.assert_awaited_once_with(VIDEO, mime_type="video/quicktime")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verdict", "index", "confidence"),
    [("REAL", 91, 0.8), ("FAKE", 10, 0.95), ("UNCERTAIN", 50, 0.4)],
)
async def test_gemini_structured_video_result_preserves_canonical_index(verdict, index, confidence):
    client = _client(
        posts=[
            _response(200, headers={"x-goog-upload-url": f"{BASE_URL}/upload/session"}),
            _response(200, _file()),
            _response(200, _generated(verdict, index, confidence)),
        ]
    )
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_video.httpx.AsyncClient", return_value=client):
        result = await GeminiVideoAdapter().analyze(VIDEO)

    assert result.verdict == Verdict(verdict)
    assert result.authenticity_index == index
    assert result.confidence == confidence
    assert result.model_used == ModelUsed.GEMINI_VIDEO
    assert result.provider_evidence is not None
    assert result.provider_evidence.provider == "gemini"
    assert result.provider_evidence.raw_score == index / 100
    assert result.explanation == "Visible evidence reviewed."
    assert "unit-test-key" not in result.explanation
    assert client.delete.await_count == 1


@pytest.mark.asyncio
async def test_video_keeps_using_shared_model_when_credibility_model_is_configured():
    client = _client(posts=[
        _response(200, headers={"x-goog-upload-url": f"{BASE_URL}/upload/session"}),
        _response(200, _file()),
        _response(200, _generated("REAL", 95, 0.9)),
    ])
    config = _configured_gemini()
    with config[0], config[1], config[2], patch.object(
        settings, "gemini_credibility_model", "gemini-2.5-flash-lite"
    ), patch("adapters.gemini_video.httpx.AsyncClient", return_value=client):
        await GeminiVideoAdapter().analyze(VIDEO)

    assert client.post.await_args_list[2].args[0] == f"{BASE_URL}/v1beta/models/gemini-test-model:generateContent"


@pytest.mark.asyncio
async def test_gemini_russian_summary_is_preserved_in_result_and_persistence():
    summary = "На видео не обнаружено выраженных визуальных признаков синтетической генерации."
    client = _client(
        posts=[
            _response(200, headers={"x-goog-upload-url": f"{BASE_URL}/upload/session"}),
            _response(200, _file()),
            _response(200, _generated("REAL", 95, 0.9, summary)),
        ]
    )
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_video.httpx.AsyncClient", return_value=client):
        result = await GeminiVideoAdapter().analyze(VIDEO)

    from src.appwrite_store import map_analysis_to_check_row

    payload = result.model_dump(mode="json")
    row = map_analysis_to_check_row(payload, "authenticated-user")
    assert summary in result.explanation
    assert summary in payload["explanation"]
    assert summary in row["explanation"]


@pytest.mark.asyncio
async def test_gemini_summary_removes_only_a_known_provider_prefix():
    summary = "Gemini Video Verification: Видео не содержит выраженных признаков синтетической генерации."
    expected = "Видео не содержит выраженных признаков синтетической генерации."
    client = _client(
        posts=[
            _response(200, headers={"x-goog-upload-url": f"{BASE_URL}/upload/session"}),
            _response(200, _file()),
            _response(200, _generated("REAL", 95, 0.9, summary)),
        ]
    )
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_video.httpx.AsyncClient", return_value=client):
        result = await GeminiVideoAdapter().analyze(VIDEO)

    assert result.explanation == expected


def test_gemini_summary_without_a_known_prefix_is_unchanged():
    summary = "Видео не содержит выраженных признаков синтетической генерации."

    assert GeminiVideoAdapter._sanitize_summary(summary) == summary


@pytest.mark.asyncio
async def test_gemini_polls_temporary_file_until_active():
    client = _client(
        posts=[
            _response(200, headers={"x-goog-upload-url": f"{BASE_URL}/upload/session"}),
            _response(200, _file("PROCESSING")),
            _response(200, _generated("REAL", 90, 0.9)),
        ],
        gets=[_response(200, _file("ACTIVE", wrapped=False))],
    )
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_video.httpx.AsyncClient", return_value=client), patch(
        "adapters.gemini_video.asyncio.sleep", new=AsyncMock()
    ):
        result = await GeminiVideoAdapter().analyze(VIDEO)

    assert result.authenticity_index == 90
    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_gemini_rejects_invalid_structured_response_and_still_cleans_up():
    client = _client(
        posts=[
            _response(200, headers={"x-goog-upload-url": f"{BASE_URL}/upload/session"}),
            _response(200, _file()),
            _response(200, {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}),
        ]
    )
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_video.httpx.AsyncClient", return_value=client), pytest.raises(
        ProviderInfrastructureError
    ) as raised:
        await GeminiVideoAdapter().analyze(VIDEO)

    assert (raised.value.service, raised.value.kind) == ("gemini", "invalid_response")
    assert client.delete.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_gemini_429_and_5xx_are_typed_as_temporary_provider_failures(status):
    client = _client(posts=[_response(status)])
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_video.httpx.AsyncClient", return_value=client), pytest.raises(
        ProviderInfrastructureError
    ) as raised:
        await GeminiVideoAdapter().analyze(VIDEO)

    assert (raised.value.service, raised.value.kind) == ("gemini", "unavailable")


@pytest.mark.asyncio
async def test_gemini_4xx_is_a_provider_request_error():
    client = _client(posts=[_response(400)])
    config = _configured_gemini()
    with config[0], config[1], config[2], patch("adapters.gemini_video.httpx.AsyncClient", return_value=client), pytest.raises(
        ExternalAPIError
    ) as raised:
        await GeminiVideoAdapter().analyze(VIDEO)

    assert (raised.value.service, raised.value.detail, raised.value.status_code) == (
        "gemini",
        "request_error",
        400,
    )
    assert raised.value.operation == "files_start"


@pytest.mark.asyncio
async def test_gemini_adapter_stops_at_its_bounded_timeout():
    async def delayed_post(*_args, **_kwargs):
        await asyncio.sleep(0.05)
        return _response(200)

    client = AsyncMock()
    client.post = delayed_post
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    config = _configured_gemini()
    with config[0], config[1], config[2], patch.object(GeminiVideoAdapter, "TOTAL_TIMEOUT_SECONDS", 0.01), patch(
        "adapters.gemini_video.httpx.AsyncClient", return_value=client
    ), pytest.raises(ProviderInfrastructureError) as raised:
        await GeminiVideoAdapter().analyze(VIDEO)

    assert (raised.value.service, raised.value.kind) == ("gemini", "processing_timeout")


@pytest.mark.asyncio
async def test_gemini_adapter_never_starts_after_root_analysis_deadline():
    now = time.monotonic()
    deadline = ExecutionDeadline(now, now + 1, now - 0.01, now + 0.5)
    token = set_execution_deadline(deadline)
    config = _configured_gemini()
    try:
        with config[0], config[1], config[2], patch("adapters.gemini_video.httpx.AsyncClient") as client:
            with pytest.raises(ExecutionDeadlineExceeded):
                await GeminiVideoAdapter().analyze(VIDEO)
    finally:
        reset_execution_deadline(token)

    client.assert_not_called()
