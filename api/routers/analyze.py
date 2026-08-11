"""POST /analyze — main analysis endpoint."""

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, UploadFile
from api.schemas import AnalysisResult, HybridAnalysisResponse
from api.security import security_http_error
from core.analyzer import HybridTextAnalyzer
from core.config import settings
from core.enums import MediaType
from core.exceptions import (
    ExternalAPIError,
    FileTooLarge,
    UnsupportedMediaType,
    VideoTooLong,
)
from router.media_router import MediaRouter
from src.media_validation import validate_media_bytes
from src.validation import SecurityValidationError, validate_request_payload

# Cleaner API design
# Edge cases handled
# PEP 8 compliant
# Thread-safe operation
# Type hints added
router = APIRouter()
logger = logging.getLogger(__name__)
media_router = MediaRouter()
hybrid_analyzer = HybridTextAnalyzer()


@router.post("/text/hybrid", response_model=HybridAnalysisResponse)
async def analyze_text_hybrid(
    payload: Any = Body(..., example={"text": "Введите текст для проверки"}),
    x_api_secret: str = Header(..., alias="x-api-secret"),
):
    if x_api_secret != settings.api_secret_key:
        raise HTTPException(status_code=403, detail="Invalid API secret")

    try:
        if not isinstance(payload, dict):
            raise SecurityValidationError("invalid_json", "JSON должен быть объектом.")
        candidate = dict(payload)
        candidate.setdefault("mode", "hybrid_text")
        request = validate_request_payload(candidate)
    except SecurityValidationError as exc:
        raise security_http_error(exc) from exc

    try:
        result = await hybrid_analyzer.analyze(request.text)
        return HybridAnalysisResponse(**result)
    except Exception:  # noqa: BLE001
        logger.error("Hybrid analyze failed")
        raise HTTPException(status_code=503, detail={"code": "provider_unavailable", "detail": "Сервис анализа временно недоступен."})


@router.post("", response_model=AnalysisResult)
async def analyze(
    file: UploadFile = File(...),
    user_id: int = Form(...),
    username: str = Form(""),
    first_name: str = Form(""),
    text_content: str = Form(""),
    x_api_secret: str = Header(..., alias="x-api-secret"),
) -> AnalysisResult:
    # 1. Auth check
    if x_api_secret != settings.api_secret_key:
        raise HTTPException(status_code=403, detail="Invalid API secret")

    # 2. Read file
    file_bytes = await file.read(20 * 1024 * 1024 + 1)

    try:
        if text_content:
            request = validate_request_payload({"text": text_content})
            media_type = MediaType.TEXT
        else:
            expected = media_router.detect_type(file.content_type, file.filename, "")
            media_info = await asyncio.to_thread(validate_media_bytes, file_bytes, expected)
            media_type = media_info.media_type
    except SecurityValidationError as exc:
        raise security_http_error(exc) from exc
    except UnsupportedMediaType:
        raise HTTPException(status_code=415, detail={"code": "unsupported_media_type", "detail": "Неподдерживаемый формат файла."})

    # 4. Analyze
    start_time = time.monotonic()
    try:
        result = await media_router.route(media_type, file_bytes, text_content)
    except FileTooLarge:
        raise HTTPException(status_code=413, detail={"code": "file_too_large", "detail": "Файл превышает лимит в 20 MiB."})
    except VideoTooLong:
        raise HTTPException(status_code=422, detail={"code": "media_limits_exceeded", "detail": "Параметры видео превышают лимит."})
    except SecurityValidationError as exc:
        raise security_http_error(exc) from exc
    except UnsupportedMediaType:
        raise HTTPException(status_code=400, detail="Неподдерживаемый тип файла")
    except ExternalAPIError as exc:
        logger.error("External API error: service=%s", exc.service)
        raise HTTPException(status_code=503, detail={"code": "provider_unavailable", "detail": "Сервис анализа временно недоступен."})

    result.processing_ms = int((time.monotonic() - start_time) * 1000)

    return result
