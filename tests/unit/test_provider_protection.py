import pytest

from core.exceptions import ProviderInfrastructureError
from src.provider_protection import (
    ProviderOperationBudget,
    ProviderUnavailableError,
    admit_provider_operation,
    begin_provider_budget,
    end_provider_budget,
)


def test_provider_budget_allows_exactly_twelve_operations(monkeypatch):
    monkeypatch.setenv("PROVIDER_REQUEST_OPS_MAX", "12")
    budget = ProviderOperationBudget.from_environment()
    for _ in range(12):
        budget.consume()
    with pytest.raises(ProviderUnavailableError):
        budget.consume()
    assert budget.used == 12


@pytest.mark.asyncio
async def test_cross_provider_fallback_attempts_share_one_twelve_operation_budget(monkeypatch):
    """Every primary/fallback attempt consumes the production request budget."""
    monkeypatch.setenv("PROVIDER_REQUEST_OPS_MAX", "12")
    tokens = begin_provider_budget()
    try:
        for provider in ["aiornot", "sapling", "sightengine", "huggingface"] * 3:
            await admit_provider_operation(provider)
        with pytest.raises(ProviderInfrastructureError) as raised:
            await admit_provider_operation("sightengine")
    finally:
        end_provider_budget(tokens)
    assert (raised.value.service, raised.value.kind) == ("sightengine", "capacity")
