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
_guard: ContextVar[Callable[[str], Awaitable[None]] | None] = ContextVar("provider_guard", default=None)


def begin_provider_budget(guard: Callable[[str], Awaitable[None]] | None = None) -> tuple[Token[ProviderOperationBudget | None], Token[Callable[[str], Awaitable[None]] | None]]:
    return _budget.set(ProviderOperationBudget.from_environment()), _guard.set(guard)


def end_provider_budget(tokens: tuple[Token[ProviderOperationBudget | None], Token[Callable[[str], Awaitable[None]] | None]]) -> None:
    _budget.reset(tokens[0]); _guard.reset(tokens[1])


async def admit_provider_operation(provider: str) -> None:
    budget = _budget.get()
    if budget is not None:
        try:
            budget.consume()
        except ProviderUnavailableError as exc:
            raise ProviderInfrastructureError(provider, "capacity") from exc
    guard = _guard.get()
    if guard is not None:
        try:
            await guard(provider)
        except RateLimitError as exc:
            # Only trusted guard codes are capacity infrastructure failures.
            # Do not infer semantics from an exception message.
            if exc.code in {"provider_temporarily_unavailable", "rate_limit_unavailable"}:
                raise ProviderInfrastructureError(provider, "capacity") from exc
            raise
