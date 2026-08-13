"""Monotonic execution budget for synchronous Appwrite analysis requests."""

from __future__ import annotations

import asyncio
import contextvars
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar


class ExecutionDeadlineExceeded(Exception):
    """Raised when analysis work would consume the persistence safety margin."""


@dataclass(frozen=True)
class ExecutionDeadline:
    """Absolute monotonic deadline reserved for analysis work.

    ``execution_timeout_seconds`` is the Function's configured synchronous
    execution limit.  The deadline intentionally ends ``safety_margin_seconds``
    earlier so normalization, persistence and response construction still have
    time to complete.
    """

    request_start: float
    root_absolute_deadline: float
    analysis_deadline: float
    persistence_deadline: float

    @classmethod
    def from_execution_timeout(
        cls,
        execution_timeout_seconds: float,
        persistence_reserve_seconds: float,
        response_safety_margin_seconds: float,
        *,
        request_start: float | None = None,
    ) -> "ExecutionDeadline":
        if (
            execution_timeout_seconds <= 0
            or persistence_reserve_seconds <= 0
            or response_safety_margin_seconds <= 0
        ):
            raise ValueError("execution timeout and reserves must be configured")
        if persistence_reserve_seconds + response_safety_margin_seconds >= execution_timeout_seconds:
            raise ValueError("analysis and response reserves exceed execution timeout")
        request_start = time.monotonic() if request_start is None else request_start
        root_absolute_deadline = request_start + execution_timeout_seconds
        persistence_deadline = root_absolute_deadline - response_safety_margin_seconds
        return cls(
            request_start=request_start,
            root_absolute_deadline=root_absolute_deadline,
            analysis_deadline=persistence_deadline - persistence_reserve_seconds,
            persistence_deadline=persistence_deadline,
        )

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ExecutionDeadlineExceeded()
        return remaining

    def remaining_analysis_time(self) -> float:
        return self._remaining(self.analysis_deadline)

    def remaining_persistence_time(self) -> float:
        return self._remaining(self.persistence_deadline)

    def remaining_root_time(self) -> float:
        return self._remaining(self.root_absolute_deadline)

    def run_final_stage(self, operation: Callable[[], "_T"]) -> "_T":
        """Run synchronous response work inside the original root budget."""
        self.remaining_root_time()
        result = operation()
        self.remaining_root_time()
        return result

    async def run(self, awaitable: Awaitable["_T"]) -> "_T":
        """Run an authentication, admission, storage or provider stage."""
        try:
            remaining = self.remaining_analysis_time()
        except ExecutionDeadlineExceeded:
            _close_unstarted_awaitable(awaitable)
            raise
        try:
            async with asyncio.timeout(remaining):
                return await awaitable
        except TimeoutError as exc:
            raise ExecutionDeadlineExceeded() from exc

    async def run_persistence(self, awaitable: Awaitable["_T"]) -> "_T":
        """Run persistence while retaining the response safety margin."""
        try:
            remaining = self.remaining_persistence_time()
        except ExecutionDeadlineExceeded:
            _close_unstarted_awaitable(awaitable)
            raise
        try:
            async with asyncio.timeout(remaining):
                return await awaitable
        except TimeoutError as exc:
            raise ExecutionDeadlineExceeded() from exc


_T = TypeVar("_T")


def _close_unstarted_awaitable(awaitable: Awaitable[object]) -> None:
    """Dispose of a coroutine that cannot be started because time is exhausted."""
    close = getattr(awaitable, "close", None)
    if callable(close):
        close()


_CURRENT_DEADLINE: contextvars.ContextVar[ExecutionDeadline | None] = contextvars.ContextVar(
    "current_execution_deadline", default=None
)


def set_execution_deadline(deadline: ExecutionDeadline):
    return _CURRENT_DEADLINE.set(deadline)


def reset_execution_deadline(token: contextvars.Token[ExecutionDeadline | None]) -> None:
    _CURRENT_DEADLINE.reset(token)


def current_execution_deadline() -> ExecutionDeadline | None:
    return _CURRENT_DEADLINE.get()


def bounded_timeout(default_seconds: float) -> float:
    """Return a stage timeout that never exceeds the request's remaining time."""
    deadline = current_execution_deadline()
    if deadline is None:
        return default_seconds
    return min(default_seconds, deadline.remaining_analysis_time())


def bounded_persistence_timeout(default_seconds: float) -> float:
    """Return a persistence timeout while retaining response construction time."""
    deadline = current_execution_deadline()
    if deadline is None:
        return default_seconds
    return min(default_seconds, deadline.remaining_persistence_time())
