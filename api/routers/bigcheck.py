"""POST /bigcheck — multi-file cross-analysis endpoint."""

import asyncio
import logging
import time

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from api.schemas import AnalysisResult
from core.config import settings
from core.enums import MediaType, Verdict
from core.exceptions import ExternalAPIError, UnsupportedMediaType
from router.media_router import MediaRouter
from src.media_validation import validate_media_bytes
from src.validation import SecurityValidationError, validate_request_payload

# Following best practices
# Optimized for async execution
router = APIRouter()
logger = logging.getLogger(__name__)
media_router = MediaRouter()


def _safe_display_filename(value: str | None) -> str:
    if not isinstance(value, str) or not value or len(value) > 255:
        return "file"
    if any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value):
        return "file"
    return value


class BigCheckFileResult(BaseModel):
    """Result for a single file in the Big Check batch."""

    filename: str
    media_type: str
    verdict: str
    confidence: float
    model_used: str
    explanation: str
    processing_ms: int


class BigCheckResponse(BaseModel):
    """Aggregated Big Check response."""

    overall_verdict: str
    overall_confidence: float
    authenticity_index: int
    summary: str
    results: list[BigCheckFileResult]
    total_files: int
    total_processing_ms: int


def _cross_analysis(results: list[AnalysisResult]) -> tuple[Verdict, float, str]:
    """
    Determine a cross-media verdict without combining incompatible scores.

    A legacy ``overall_confidence`` field remains required by current
    consumers.  It is a neutral unknown sentinel here, never a synthesized
    provider probability or authenticity score.

    Returns (verdict, confidence, summary).
    """
    total = len(results)
    if total == 0:
        return Verdict.UNCERTAIN, 0.5, "Нет файлов для анализа"

    fake_count = sum(1 for r in results if r.verdict == Verdict.FAKE)
    real_count = sum(1 for r in results if r.verdict == Verdict.REAL)
    uncertain_count = sum(1 for r in results if r.verdict == Verdict.UNCERTAIN)

    if fake_count:
        verdict = Verdict.FAKE
        summary = (
            f"Кросс-анализ {total} файлов: найден как минимум один компонент с решающим "
            f"вердиктом о синтетическом происхождении ({fake_count})."
        )
    elif real_count == total:
        verdict = Verdict.REAL
        summary = (
            f"Кросс-анализ {total} файлов: все {real_count} завершённые компоненты "
            f"получили решающий вердикт о подлинности."
        )
    else:
        verdict = Verdict.UNCERTAIN
        parts = []
        if real_count > 0:
            parts.append(f"{real_count} подлинных")
        if fake_count > 0:
            parts.append(f"{fake_count} сгенерированных")
        if uncertain_count > 0:
            parts.append(f"{uncertain_count} неопределённых")
        summary = (
            f"Кросс-анализ {total} файлов: {', '.join(parts)}. "
            f"Однозначный вердикт вынести невозможно."
        )

    return verdict, 0.5, summary


@router.post("", response_model=BigCheckResponse)
async def bigcheck(
    files: list[UploadFile] = File(...),
    user_id: int = Form(...),
    username: str = Form(""),
    first_name: str = Form(""),
    text_content: str = Form(""),
    x_api_secret: str = Header(..., alias="x-api-secret"),
) -> BigCheckResponse:
    """
    Big Check: analyze multiple files + optional text in a single request.
    Performs cross-analysis to determine overall verdict.
    """
    # 1. Auth
    if x_api_secret != settings.api_secret_key:
        raise HTTPException(status_code=403, detail="Invalid API secret")

    # 2. Rate limit — each file counts as one check
    total_items = len(files) + (1 if text_content and text_content.strip() else 0)
    if total_items == 0:
        raise HTTPException(status_code=400, detail="Загрузите хотя бы один файл или введите текст")
    if total_items > 10:
        raise HTTPException(status_code=400, detail="Максимум 10 элементов за раз")

    # 3. Process each file
    individual_results: list[AnalysisResult] = []
    file_results: list[BigCheckFileResult] = []
    total_start = time.monotonic()

    for upload_file in files:
        filename = _safe_display_filename(upload_file.filename)
        file_bytes = await upload_file.read(20 * 1024 * 1024 + 1)

        try:
            expected = media_router.detect_type(upload_file.content_type, upload_file.filename, "")
            media_info = await asyncio.to_thread(validate_media_bytes, file_bytes, expected)
            media_type = media_info.media_type
        except SecurityValidationError as exc:
            file_results.append(
                BigCheckFileResult(
                    filename=filename,
                    media_type="unknown",
                    verdict="UNCERTAIN",
                    confidence=0.0,
                    model_used="fallback_uncertain",
                    explanation=exc.detail,
                    processing_ms=0,
                )
            )
            continue
        except UnsupportedMediaType:
            file_results.append(
                BigCheckFileResult(
                    filename=filename,
                    media_type="unknown",
                    verdict="UNCERTAIN",
                    confidence=0.0,
                    model_used="fallback_uncertain",
                    explanation="Неподдерживаемый тип файла",
                    processing_ms=0,
                )
            )
            continue

        start_time = time.monotonic()
        try:
            result = await media_router.route(media_type, file_bytes, "")
        except (ExternalAPIError, Exception):
            logger.error("BigCheck file analysis failed")
            file_results.append(
                BigCheckFileResult(
                    filename=filename,
                    media_type=media_type.value,
                    verdict="UNCERTAIN",
                    confidence=0.0,
                    model_used="fallback_uncertain",
                    explanation="Сервис анализа временно недоступен.",
                    processing_ms=0,
                )
            )
            continue

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        result.processing_ms = elapsed_ms

        individual_results.append(result)
        file_results.append(
            BigCheckFileResult(
                filename=filename,
                media_type=result.media_type.value,
                verdict=result.verdict.value,
                confidence=result.confidence,
                model_used=result.model_used.value,
                explanation=result.explanation,
                processing_ms=elapsed_ms,
            )
        )

    # 4. Process text if provided
    if text_content and text_content.strip():
        start_time = time.monotonic()
        try:
            validate_request_payload({"text": text_content})
            text_result = await media_router.route(
                MediaType.TEXT, b"", text_content
            )
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            text_result.processing_ms = elapsed_ms

            individual_results.append(text_result)
            file_results.append(
                BigCheckFileResult(
                    filename="text_input",
                    media_type=text_result.media_type.value,
                    verdict=text_result.verdict.value,
                    confidence=text_result.confidence,
                    model_used=text_result.model_used.value,
                    explanation=text_result.explanation,
                    processing_ms=elapsed_ms,
                )
            )
        except Exception:
            logger.error("BigCheck text analysis failed")
            file_results.append(
                BigCheckFileResult(
                    filename="text_input",
                    media_type="text",
                    verdict="UNCERTAIN",
                    confidence=0.0,
                    model_used="fallback_uncertain",
                    explanation="Сервис анализа временно недоступен.",
                    processing_ms=0,
                )
            )

    # 5. Cross-analysis
    overall_verdict, overall_confidence, summary = _cross_analysis(individual_results)

    # This legacy field has no universal meaning across mixed score kinds.
    # Keep its numeric API shape with the same neutral unknown sentinel.
    authenticity_index = 50

    total_ms = int((time.monotonic() - total_start) * 1000)

    return BigCheckResponse(
        overall_verdict=overall_verdict.value,
        overall_confidence=overall_confidence,
        authenticity_index=authenticity_index,
        summary=summary,
        results=file_results,
        total_files=len(file_results),
        total_processing_ms=total_ms,
    )
