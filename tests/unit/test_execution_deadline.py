"""Tests for the shared synchronous-analysis execution budget."""

import asyncio
import time

import pytest

from src.execution_deadline import (
    ExecutionDeadline,
    ExecutionDeadlineExceeded,
    bounded_persistence_timeout,
    bounded_timeout,
    reset_execution_deadline,
    set_execution_deadline,
)


def test_absolute_deadline_uses_monotonic_request_start_and_reserves_margin():
    deadline = ExecutionDeadline.from_execution_timeout(30.0, 5.0, 1.0, request_start=100.0)

    assert deadline.request_start == 100.0
    assert deadline.root_absolute_deadline == 130.0
    assert deadline.persistence_deadline == 129.0
    assert deadline.analysis_deadline == 124.0


@pytest.mark.asyncio
async def test_expired_stage_is_cancelled_as_deadline_exceeded():
    now = time.monotonic()
    deadline = ExecutionDeadline(
        request_start=now,
        root_absolute_deadline=now + 0.02,
        analysis_deadline=now + 0.01,
        persistence_deadline=now + 0.015,
    )

    with pytest.raises(ExecutionDeadlineExceeded):
        await deadline.run(asyncio.sleep(0.1))


@pytest.mark.asyncio
async def test_expired_stage_closes_an_unstarted_coroutine():
    now = time.monotonic()
    deadline = ExecutionDeadline(
        request_start=now,
        root_absolute_deadline=now,
        analysis_deadline=now,
        persistence_deadline=now,
    )
    started = False

    async def operation():
        nonlocal started
        started = True

    coroutine = operation()
    with pytest.raises(ExecutionDeadlineExceeded):
        await deadline.run(coroutine)

    assert started is False
    assert coroutine.cr_frame is None


def test_stage_timeout_cannot_exceed_remaining_execution_budget():
    now = time.monotonic()
    deadline = ExecutionDeadline(
        request_start=now,
        root_absolute_deadline=now + 0.12,
        analysis_deadline=now + 0.1,
        persistence_deadline=now + 0.11,
    )
    token = set_execution_deadline(deadline)
    try:
        assert 0 < bounded_timeout(60.0) <= 0.1
    finally:
        reset_execution_deadline(token)


def test_persistence_timeout_uses_its_later_budget_but_keeps_response_reserve():
    now = time.monotonic()
    deadline = ExecutionDeadline(
        request_start=now,
        root_absolute_deadline=now + 0.3,
        analysis_deadline=now + 0.1,
        persistence_deadline=now + 0.2,
    )
    token = set_execution_deadline(deadline)
    try:
        analysis_remaining = bounded_timeout(60.0)
        persistence_remaining = bounded_persistence_timeout(60.0)
        assert 0 < analysis_remaining <= 0.1
        assert analysis_remaining < persistence_remaining <= 0.2
    finally:
        reset_execution_deadline(token)


def test_final_stage_uses_the_remaining_root_budget():
    now = time.monotonic()
    deadline = ExecutionDeadline(
        request_start=now,
        root_absolute_deadline=now + 0.1,
        analysis_deadline=now + 0.01,
        persistence_deadline=now + 0.05,
    )

    assert deadline.run_final_stage(lambda: "response") == "response"


def test_expired_final_stage_fails_closed_without_running_response_work():
    now = time.monotonic()
    deadline = ExecutionDeadline(
        request_start=now,
        root_absolute_deadline=now,
        analysis_deadline=now,
        persistence_deadline=now,
    )
    ran = False

    def response_work():
        nonlocal ran
        ran = True

    with pytest.raises(ExecutionDeadlineExceeded):
        deadline.run_final_stage(response_work)

    assert ran is False


@pytest.mark.asyncio
async def test_persistence_budget_remains_usable_after_analysis_budget_expires():
    now = time.monotonic()
    deadline = ExecutionDeadline(
        request_start=now,
        root_absolute_deadline=now + 0.15,
        analysis_deadline=now + 0.01,
        persistence_deadline=now + 0.1,
    )

    await asyncio.sleep(0.02)
    with pytest.raises(ExecutionDeadlineExceeded):
        await deadline.run(asyncio.sleep(0))
    await deadline.run_persistence(asyncio.sleep(0))
