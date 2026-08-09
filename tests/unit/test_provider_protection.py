import pytest

from src.provider_protection import ProviderOperationBudget, ProviderUnavailableError


def test_provider_budget_allows_exactly_twelve_operations(monkeypatch):
    monkeypatch.setenv("PROVIDER_REQUEST_OPS_MAX", "12")
    budget = ProviderOperationBudget.from_environment()
    for _ in range(12):
        budget.consume()
    with pytest.raises(ProviderUnavailableError):
        budget.consume()
    assert budget.used == 12
