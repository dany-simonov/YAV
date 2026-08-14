"""Request-scoped provider operation budget for BE-04."""
from __future__ import annotations

import os
from contextvars import ContextVar, Token
from typing import Awaitable, Callable
from dataclasses import dataclass

from core.exceptions import ProviderInfrastructureError
from src.rate_limit import RateLimitError


class ProviderUnavailableError(Exception):
    code = "provider_temporarily_unavailable"
    detail = "Сервис анализа временно перегружен. Попробуйте позже."
    status_code = 503


@dataclass
class ProviderOperationBudget:
    limit: int
    used: int = 0

    @classmethod
    def from_environment(cls) -> "ProviderOperationBudget":
        try:
            limit = int(os.getenv("PROVIDER_REQUEST_OPS_MAX", "12"))
        except ValueError as exc:
            raise ProviderUnavailableError() from exc
        if limit <= 0:
            raise ProviderUnavailableError()
        return cls(limit)

    def consume(self) -> None:
        if self.used >= self.limit:
            raise ProviderUnavailableError()
        self.used += 1


_budget: ContextVar[ProviderOperationBudget | None] = ContextVar("provider_budget", default=None)
_guard: ContextVar[Callable[..., Awaitable[None]] | None] = ContextVar("provider_guard", default=None)
_prepaid: ContextVar[dict[str, int] | None] = ContextVar("provider_prepaid", default=None)


def begin_provider_budget(
    guard: Callable[..., Awaitable[None]] | None = None, prepaid: dict[str, int] | None = None,
) -> tuple[Token[ProviderOperationBudget | None], Token[Callable[..., Awaitable[None]] | None], Token[dict[str, int] | None]]:
    return (
        _budget.set(ProviderOperationBudget.from_environment()), _guard.set(guard),
        _prepaid.set(None if prepaid is None else dict(prepaid)),
    )


def end_provider_budget(tokens: tuple[Token[ProviderOperationBudget | None], Token[Callable[..., Awaitable[None]] | None], Token[dict[str, int] | None]]) -> None:
    _budget.reset(tokens[0]); _guard.reset(tokens[1]); _prepaid.reset(tokens[2])


async def admit_provider_operation(provider: str, units: int = 1) -> None:
    if not isinstance(units, int) or isinstance(units, bool) or units <= 0:
        raise ProviderUnavailableError()
    budget = _budget.get()
    if budget is not None:
        try:
            budget.consume()
        except ProviderUnavailableError as exc:
            raise ProviderInfrastructureError(provider, "capacity") from exc
    prepaid = _prepaid.get()
    if prepaid is not None and prepaid.get(provider, 0) >= units:
        prepaid[provider] -= units
        return
    guard = _guard.get()
    if guard is not None:
        try:
            # One-argument guards are the retired request-minute guard used by
            # older unit paths.  Production admission always supplies prepaid
            # units and therefore receives the exact provider units here.
            if prepaid is None:
                await guard(provider)
            else:
                await guard(provider, units)
        except RateLimitError as exc:
            # Only trusted guard codes are capacity infrastructure failures.
            # Do not infer semantics from an exception message.
            if exc.code in {"provider_temporarily_unavailable", "rate_limit_unavailable"}:
                raise ProviderInfrastructureError(provider, "capacity") from exc
            raise
