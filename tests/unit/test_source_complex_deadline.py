import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from api.schemas import AnalysisResult
from core.enums import MediaType, ModelUsed, Verdict
from src.execution_deadline import ExecutionDeadline, reset_execution_deadline, set_execution_deadline
from src.source_ingestion import SourceDocument
from src.validation import SecurityValidationError


def _text_result() -> AnalysisResult:
    return AnalysisResult(
        verdict=Verdict.REAL, confidence=0.0, model_used=ModelUsed.GEMINI_TEXT,
        explanation="text completed", media_type=MediaType.TEXT, authenticity_index=100,
        analysis_mode="complex",
    )


class _SlowVideoIngestor:
    async def ingest(self, _url: str, *, diagnostic_log=None) -> SourceDocument:
        return SourceDocument("https://example.com/post", "Post", "", "", "x" * 200, (),
                              ("https://media.example/video.mp4",), False)

    async def download_media(self, _url: str, *, timeout_seconds: float | None = None):
        await asyncio.sleep(0.2)
        return b"not-reached", "video/mp4"


@pytest.mark.asyncio
async def test_slow_media_download_becomes_unavailable_while_text_partial_survives():
    # analysis deadline is intentionally tiny; child acquisition must finish
    # before it, preserving the already-completed text branch.
    now = time.monotonic()
    deadline = ExecutionDeadline(now, now + 2, now + 0.06, now + 1)
    token = set_execution_deadline(deadline)
    try:
        with patch("src.main.SourceIngestor", return_value=_SlowVideoIngestor()), patch(
            "src.main._analyze_complex_text", new=AsyncMock(return_value=_text_result())
        ):
            from src.main import _analyze_complex_source
            result = await _analyze_complex_source("https://example.com/post", None)
    finally:
        reset_execution_deadline(token)
    assert result.source is not None
    assert result.source.media[0].kind == "video"
    assert result.source.media[0].status == "unavailable"
    assert result.explanation == "text completed"


@pytest.mark.asyncio
async def test_source_without_text_or_media_has_a_distinct_controlled_error():
    class EmptyIngestor:
        async def ingest(self, _url: str, *, diagnostic_log=None) -> SourceDocument:
            return SourceDocument("https://example.com/post", "", "", "", "", (), (), False)

    with patch("src.main.SourceIngestor", return_value=EmptyIngestor()):
        from src.main import _analyze_complex_source

        with pytest.raises(SecurityValidationError) as raised:
            await _analyze_complex_source("https://example.com/post", None)

    assert raised.value.code == "source_no_analyzable_content"
    assert raised.value.detail == "На странице не найден материал для анализа."
