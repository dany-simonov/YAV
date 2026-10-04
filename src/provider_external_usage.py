"""Sanitized, server-only provider account telemetry for the admin panel.

This module is deliberately separate from request admission.  A provider's
account reporting API is informative and must never replace YAV's own
``rate_limits`` counters or decide whether an analysis may run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

import httpx

from core.config import settings
from src.provider_telemetry import AppwriteProviderTelemetryStore


_TIMEOUT = 10.0
_NO_USAGE_API = {"status": "unavailable", "reason": "no_supported_usage_api"}


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _error(status_code: int | None, *, timeout: bool = False) -> dict[str, str]:
    if timeout:
        return {"status": "timeout", "reason": "provider_timeout"}
    if status_code in (401, 403):
        return {"status": "error", "reason": "provider_auth_error"}
    if status_code == 429:
        return {"status": "error", "reason": "provider_rate_limited"}
    if status_code is not None and status_code >= 500:
        return {"status": "unavailable", "reason": "provider_unavailable"}
    return {"status": "error", "reason": "provider_invalid_response"}


class ProviderExternalUsageService:
    """Fetch only documented, non-secret aggregate/provider-account fields."""

    def __init__(self, appwrite_api_key: str = "") -> None:
        self.appwrite_api_key = appwrite_api_key

    async def get_usage(self) -> dict[str, Any]:
        sapling = await self._sapling()
        resemble = await self._resemble()
        sightengine = await AppwriteProviderTelemetryStore(self.appwrite_api_key).sightengine_usage()
        return {
            "providers": [
                {"provider": "gemini", "source": "provider", **_NO_USAGE_API},
                sightengine,
                {"provider": "aiornot", "source": "provider", **_NO_USAGE_API},
                sapling,
                resemble,
                {"provider": "huggingface", "source": "provider", **_NO_USAGE_API},
            ]
        }

    async def _sapling(self) -> dict[str, Any]:
        base = {"provider": "sapling", "source": "provider", "unit": "characters", "metric_scope": "api_key_reporting"}
        if not settings.sapling_api_key:
            return {**base, "status": "unavailable", "reason": "missing_credentials"}
        headers = {"Authorization": f"Bearer {settings.sapling_api_key}"}
        month_start_days_back = datetime.now(timezone.utc).day - 1
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                usage_response, quota_response = await _gather_pair(
                    client.post("https://api.sapling.ai/api/v1/reporting/api_usage", headers=headers),
                    client.post(
                        "https://api.sapling.ai/api/v1/reporting/api_quota_usage",
                        headers=headers,
                        json={"start_days_back": month_start_days_back, "end_days_back": 0},
                    ),
                )
        except httpx.TimeoutException:
            return {**base, **_error(None, timeout=True)}
        except httpx.HTTPError:
            return {**base, "status": "unavailable", "reason": "provider_unavailable"}
        if usage_response.status_code != 200:
            return {**base, **_error(usage_response.status_code)}
        if quota_response.status_code != 200:
            return {**base, **_error(quota_response.status_code)}
        try:
            usage_body, quota_body = usage_response.json(), quota_response.json()
        except (TypeError, ValueError):
            return {**base, **_error(None)}
        if not isinstance(usage_body, Mapping) or not isinstance(quota_body, Mapping):
            return {**base, **_error(None)}
        # The reporting API has changed field spelling over time.  Accept only
        # documented numeric totals; never infer a score or a missing quota.
        used = _number(usage_body.get("total_characters"))
        if used is None:
            used = _number(usage_body.get("usage"))
        quota = _number(quota_body.get("quota"))
        if quota is None:
            quota = _number(quota_body.get("character_quota"))
        quota_used = _number(quota_body.get("usage"))
        if used is None and quota_used is None:
            return {**base, **_error(None)}
        # ``api_quota_usage`` is the provider-authoritative pairing for the
        # quota field; prefer its period usage when it is present.
        period_used = quota_used if quota_used is not None else used
        remaining = max(0, quota - period_used) if quota is not None else None
        return {
            **base,
            "status": "ok",
            "usage": period_used,
            "quota": quota,
            "remaining": remaining,
            "reporting": {"api_usage": used, "api_quota_usage": quota_used},
        }

    async def _resemble(self) -> dict[str, Any]:
        base = {"provider": "resemble", "source": "provider", "metric_scope": "team_account_not_detect_specific"}
        if not settings.resemble_api_key:
            return {**base, "status": "unavailable", "reason": "missing_credentials"}
        headers = {"Authorization": f"Bearer {settings.resemble_api_key}"}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                teams_response, subscription_response, wallet_response = await _gather_triple(
                    client.get("https://app.resemble.ai/api/v2/account/teams", headers=headers),
                    client.get("https://app.resemble.ai/billing/api/v1/subscription", headers=headers),
                    client.get("https://app.resemble.ai/billing/api/v1/wallet/auto_reload", headers=headers),
                )
        except httpx.TimeoutException:
            return {**base, **_error(None, timeout=True)}
        except httpx.HTTPError:
            return {**base, "status": "unavailable", "reason": "provider_unavailable"}
        if teams_response.status_code != 200:
            return {**base, **_error(teams_response.status_code)}
        try:
            teams_body = teams_response.json()
        except (TypeError, ValueError):
            return {**base, **_error(None)}
        items = teams_body.get("items") if isinstance(teams_body, Mapping) else None
        if not isinstance(items, list):
            return {**base, **_error(None)}
        teams: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            current_usage, limit = _number(item.get("current_usage")), _number(item.get("voice_limit"))
            unit = item.get("units") if isinstance(item.get("units"), str) else None
            if current_usage is not None or limit is not None:
                teams.append({"unit": unit, "usage": current_usage, "quota": limit, "remaining": max(0, limit - current_usage) if limit is not None and current_usage is not None else None})
        billing: dict[str, str] = {
            "subscription": "available" if subscription_response.status_code == 200 else "unavailable",
            "wallet": "available" if wallet_response.status_code == 200 else "unavailable",
        }
        return {
            **base,
            "status": "ok",
            "usage": teams,
            "quota": None,
            "remaining": None,
            "billing": billing,
        }


async def _gather_pair(first: Any, second: Any) -> tuple[httpx.Response, httpx.Response]:
    import asyncio
    result = await asyncio.gather(first, second)
    return result[0], result[1]


async def _gather_triple(first: Any, second: Any, third: Any) -> tuple[httpx.Response, httpx.Response, httpx.Response]:
    import asyncio
    result = await asyncio.gather(first, second, third)
    return result[0], result[1], result[2]
