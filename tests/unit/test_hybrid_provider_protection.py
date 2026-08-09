"""Regression coverage for g4f as a guarded external provider call site."""

from unittest.mock import AsyncMock, patch

import pytest

from core.analyzer import HybridTextAnalyzer
from core.exceptions import ProviderInfrastructureError
from src.provider_protection import begin_provider_budget, end_provider_budget
from src.rate_limit import RateLimitError


@pytest.mark.asyncio
async def test_g4f_call_uses_request_budget_and_global_provider_guard():
    guard = AsyncMock()
    tokens = begin_provider_budget(guard)
    try:
        with patch("core.analyzer.g4f.ChatCompletion.create", return_value='{"fact_checks": []}') as call:
            result = await HybridTextAnalyzer()._call_g4f("gpt-4.1-nano", "text")
    finally:
        end_provider_budget(tokens)

    assert result == {"fact_checks": []}
    guard.assert_awaited_once_with("g4f")
    call.assert_called_once()


@pytest.mark.asyncio
async def test_g4f_guard_denial_prevents_outbound_call():
    async def deny(_provider: str) -> None:
        raise RateLimitError("provider_temporarily_unavailable", "safe", 503)

    tokens = begin_provider_budget(deny)
    try:
        with patch("core.analyzer.g4f.ChatCompletion.create") as call:
            with pytest.raises(ProviderInfrastructureError) as raised:
                await HybridTextAnalyzer()._call_g4f("gpt-4.1-nano", "text")
    finally:
        end_provider_budget(tokens)

    assert raised.value.kind == "capacity"
    call.assert_not_called()


@pytest.mark.asyncio
async def test_g4f_budget_denial_prevents_thirteenth_outbound_call(monkeypatch):
    monkeypatch.setenv("PROVIDER_REQUEST_OPS_MAX", "1")
    guard = AsyncMock()
    tokens = begin_provider_budget(guard)
    try:
        with patch("core.analyzer.g4f.ChatCompletion.create", return_value='{"fact_checks": []}') as call:
            analyzer = HybridTextAnalyzer()
            await analyzer._call_g4f("gpt-4.1-nano", "text")
            with pytest.raises(ProviderInfrastructureError) as raised:
                await analyzer._call_g4f("gpt-oss-120b", "text")
    finally:
        end_provider_budget(tokens)

    assert raised.value.kind == "capacity"
    assert call.call_count == 1
