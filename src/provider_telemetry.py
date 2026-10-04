"""Provider-confirmed operation telemetry stored separately from rate limits."""

from __future__ import annotations

import hashlib
import logging
import os
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any, Mapping

import httpx

from src.rate_limit import _window

logger = logging.getLogger(__name__)
_runtime_api_key: ContextVar[str] = ContextVar("provider_telemetry_api_key", default="")


def begin_provider_telemetry(api_key: str) -> Token[str]:
    return _runtime_api_key.set(api_key)


def end_provider_telemetry(token: Token[str]) -> None:
    _runtime_api_key.reset(token)


class AppwriteProviderTelemetryStore:
    """Persist aggregate, provider-reported operation counts.

    This is intentionally not an admission counter: recording a provider's
    response must not affect quota enforcement or analysis availability.
    """

    def __init__(self, api_key: str) -> None:
        self.endpoint = os.getenv("APPWRITE_FUNCTION_API_ENDPOINT", "").rstrip("/")
        self.project = os.getenv("APPWRITE_FUNCTION_PROJECT_ID", "")
        self.database = os.getenv("APPWRITE_DATABASE_ID", "yav")
        self.table = os.getenv("APPWRITE_PROVIDER_TELEMETRY_TABLE_ID", "")
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.project and self.database and self.table and self.api_key)

    @property
    def _rows_url(self) -> str:
        return f"{self.endpoint}/tablesdb/{self.database}/tables/{self.table}/rows"

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Appwrite-Project": self.project, "X-Appwrite-Key": self.api_key}

    @staticmethod
    def _row_id(period: str, window: str) -> str:
        return hashlib.sha256(f"sightengine:operations:{period}:{window}".encode()).hexdigest()[:36]

    async def record_sightengine_operations(self, operations: int) -> None:
        if not self.configured or isinstance(operations, bool) or not isinstance(operations, int) or operations < 0:
            return
        now = datetime.now(timezone.utc)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                for period in ("day", "month"):
                    window = _window(now, period)
                    row_id = self._row_id(period, window.key)
                    payload = {
                        "provider": "sightengine",
                        "metric": "operations",
                        "unit": "operations",
                        "period": period,
                        "window_start": window.key,
                        "count": operations,
                    }
                    created = await client.post(
                        self._rows_url,
                        headers=self._headers,
                        json={"rowId": row_id, "data": payload, "permissions": []},
                    )
                    if created.status_code in (200, 201):
                        continue
                    if created.status_code != 409:
                        raise RuntimeError("telemetry create failed")
                    incremented = await client.patch(
                        f"{self._rows_url}/{row_id}/count/increment",
                        headers=self._headers,
                        json={"value": operations},
                    )
                    if incremented.status_code != 200:
                        raise RuntimeError("telemetry increment failed")
        except (httpx.HTTPError, RuntimeError):
            # Metadata only: neither provider payload nor credentials are ever
            # logged, and a telemetry outage cannot change analysis output.
            logger.warning("provider_telemetry_write_failed provider=sightengine")

    async def sightengine_usage(self) -> dict[str, Any]:
        base = {
            "provider": "sightengine",
            "source": "provider",
            "unit": "operations",
            "metric_scope": "successful_provider_response_operations",
        }
        if not self.configured:
            return {**base, "status": "unavailable", "reason": "telemetry_not_configured"}
        now = datetime.now(timezone.utc)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                values: dict[str, int] = {}
                for period in ("day", "month"):
                    window = _window(now, period)
                    response = await client.get(
                        f"{self._rows_url}/{self._row_id(period, window.key)}",
                        headers=self._headers,
                    )
                    if response.status_code == 404:
                        values[period] = 0
                        continue
                    if response.status_code != 200:
                        return {**base, "status": "unavailable", "reason": "telemetry_unavailable"}
                    body = response.json()
                    count = body.get("count") if isinstance(body, Mapping) else None
                    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                        return {**base, "status": "error", "reason": "telemetry_invalid_response"}
                    values[period] = count
        except (httpx.HTTPError, TypeError, ValueError):
            return {**base, "status": "unavailable", "reason": "telemetry_unavailable"}
        return {
            **base,
            "status": "ok",
            "usage": {"daily": values["day"], "monthly": values["month"]},
            "quota": None,
            "remaining": None,
        }


async def record_sightengine_operations(operations: int) -> None:
    """Record one successful provider response in the request's runtime scope."""
    await AppwriteProviderTelemetryStore(_runtime_api_key.get()).record_sightengine_operations(operations)
