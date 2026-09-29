"""Global provider-budget mapping for canonical dynamic providers."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.hf_audio import HFAudioAdapter
from adapters.hf_image import HFImageAdapter
from adapters.resemble import ResembleAdapter
from core.exceptions import ProviderInfrastructureError
from src.provider_protection import (
    admit_provider_operation,
    begin_provider_budget,
    end_provider_budget,
)
from src.rate_limit import (
    AppwriteTablesRateLimitStore,
    RateLimitError,
    _provider_plan,
)


def _store(monkeypatch) -> AppwriteTablesRateLimitStore:
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project")
    monkeypatch.setenv("RATE_LIMIT_IP_HMAC_KEY", "test-secret")
    return AppwriteTablesRateLimitStore(
        "runtime-key", now=datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    )


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("resemble", {"global_resemble_daily", "global_resemble_monthly"}),
        (
            "huggingface",
            {"global_huggingface_daily", "global_huggingface_monthly"},
        ),
    ],
)
def test_canonical_dynamic_provider_has_global_operation_dimensions(
    monkeypatch, provider, expected
):
    plan = _provider_plan(_store(monkeypatch), provider, 2)

    assert {dimension.dimension for dimension in plan.dimensions} == expected
    assert {dimension.subject for dimension in plan.dimensions} == {"global"}
    assert all(dimension.units == 2 for dimension in plan.dimensions)
    assert plan.provider_units == ((provider, 2),)
    assert plan.create_reservation is False


def test_unknown_provider_keeps_existing_no_global_mapping_semantics(monkeypatch):
    plan = _provider_plan(_store(monkeypatch), "unknown-provider", 1)

    assert plan.dimensions == ()
    assert plan.provider_units == (("unknown-provider", 1),)


@pytest.mark.parametrize(
    ("provider", "environment", "value"),
    [
        ("resemble", "GLOBAL_RESEMBLE_DAILY", "0"),
        ("resemble", "GLOBAL_RESEMBLE_MONTHLY", "-1"),
        ("huggingface", "GLOBAL_HUGGINGFACE_DAILY", "bad"),
        ("huggingface", "GLOBAL_HUGGINGFACE_MONTHLY", "0"),
    ],
)
def test_invalid_new_provider_budget_configuration_fails_closed(
    monkeypatch, provider, environment, value
):
    monkeypatch.setenv(environment, value)

    with pytest.raises(RateLimitError) as raised:
        _provider_plan(_store(monkeypatch), provider, 1)

    assert (raised.value.code, raised.value.status_code) == (
        "rate_limit_unavailable",
        503,
    )


@pytest.mark.asyncio
async def test_dynamic_resemble_and_huggingface_operations_admit_global_plans(monkeypatch):
    store = _store(monkeypatch)
    store.admit = AsyncMock()
    tokens = begin_provider_budget(store.admit_provider_units, {})
    try:
        await admit_provider_operation("resemble")
        await admit_provider_operation("huggingface")
    finally:
        end_provider_budget(tokens)

    plans = [call.args[0] for call in store.admit.await_args_list]
    assert [
        {dimension.dimension for dimension in plan.dimensions} for plan in plans
    ] == [
        {"global_resemble_daily", "global_resemble_monthly"},
        {"global_huggingface_daily", "global_huggingface_monthly"},
    ]
    assert all(
        {dimension.subject for dimension in plan.dimensions} == {"global"}
        for plan in plans
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "adapter", "client_path"),
    [
        ("resemble", ResembleAdapter(), "adapters.resemble.httpx.AsyncClient"),
        ("huggingface", HFImageAdapter(), "adapters.hf_image.httpx.AsyncClient"),
        ("huggingface", HFAudioAdapter(), "adapters.hf_audio.httpx.AsyncClient"),
    ],
)
async def test_dynamic_provider_capacity_denial_blocks_canonical_provider_io(
    provider, adapter, client_path
):
    guard = AsyncMock(
        side_effect=RateLimitError(
            "provider_temporarily_unavailable", "safe", 503
        )
    )
    client = MagicMock()
    client.post = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    tokens = begin_provider_budget(guard, {})
    try:
        with patch(client_path, return_value=client):
            with pytest.raises(ProviderInfrastructureError) as raised:
                await adapter.analyze(b"audio-or-image")
    finally:
        end_provider_budget(tokens)

    assert (raised.value.service, raised.value.kind) == (provider, "capacity")
    client.post.assert_not_awaited()
