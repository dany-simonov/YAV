"""Shared safe HTTP error conversion for local FastAPI endpoints."""

from fastapi import HTTPException

from src.validation import SecurityValidationError


def security_http_error(error: SecurityValidationError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "detail": error.detail},
    )
