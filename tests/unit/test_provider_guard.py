"""Focused tests for the production provider-minute guard primitive."""
from datetime import datetime, timezone
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.rate_limit import AppwriteTablesRateLimitStore, RateLimitError


def _store(monkeypatch):
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project")
    monkeypatch.setenv("RATE_LIMIT_IP_HMAC_KEY", "test-secret")
    monkeypatch.setenv("PROVIDER_SIGHTENGINE_PER_MINUTE", "5")
    return AppwriteTablesRateLimitStore("key", now=datetime(2026, 8, 9, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_provider_guard_uses_provider_minute_dimension_and_configured_limit(monkeypatch):
    store = _store(monkeypatch)
    store.consume = AsyncMock(return_value=0)
    await store.guard_provider("sightengine")
    assert store.consume.await_args.args == ("provider_minute", "sightengine", "minute", 5)


@pytest.mark.asyncio
async def test_provider_guard_denial_is_safe_503(monkeypatch):
    store = _store(monkeypatch)
    store.consume = AsyncMock(return_value=30)
    with pytest.raises(RateLimitError) as raised:
        await store.guard_provider("sightengine")
    assert (raised.value.status_code, raised.value.code) == (503, "provider_temporarily_unavailable")


@pytest.mark.asyncio
async def test_provider_guard_backend_failure_propagates_fail_closed(monkeypatch):
    store = _store(monkeypatch)
    store.consume = AsyncMock(side_effect=RateLimitError("rate_limit_unavailable", "x", 503))
    with pytest.raises(RateLimitError) as raised:
        await store.guard_provider("sightengine")
    assert raised.value.code == "rate_limit_unavailable"


@pytest.mark.asyncio
async def test_providers_use_isolated_subjects(monkeypatch):
    store = _store(monkeypatch)
    store.consume = AsyncMock(return_value=0)
    await store.guard_provider("sightengine")
    await store.guard_provider("huggingface")
    assert [call.args[1] for call in store.consume.await_args_list] == ["sightengine", "huggingface"]


@pytest.mark.asyncio
async def test_twenty_concurrent_provider_admissions_allow_exactly_five(monkeypatch):
    store = _store(monkeypatch)
    backend = _AtomicProviderMinuteBackend(limit=5)
    outbound_calls = 0

    async def admit():
        nonlocal outbound_calls
        try:
            await store.guard_provider("sightengine")
            # This represents the provider HTTP call. It is deliberately after
            # the production guard: denied admissions must never reach it.
            outbound_calls += 1
            return "allowed"
        except RateLimitError as exc:
            assert exc.code == "provider_temporarily_unavailable"
            return "denied"

    with patch("src.rate_limit.httpx.AsyncClient", side_effect=backend.client):
        outcomes = await asyncio.gather(*(admit() for _ in range(20)))
    assert outcomes.count("allowed") == 5
    assert outcomes.count("denied") == 15
    assert backend.count == 5
    assert outbound_calls == 5


class _AtomicProviderMinuteBackend:
    """A concurrent TablesDB double implementing the real create/increment API."""

    def __init__(self, *, limit: int) -> None:
        self.limit = limit
        self.count = 0
        self._exists = False
        self._lock = asyncio.Lock()

    def client(self, **_kwargs):
        return _AtomicProviderMinuteClient(self)

    async def create(self, payload):
        assert payload["data"]["dimension"] == "provider_minute"
        assert payload["data"]["subject"] == "sightengine"
        async with self._lock:
            if self._exists:
                return SimpleNamespace(status_code=409)
            self._exists = True
            self.count = 1
            return SimpleNamespace(status_code=201)

    async def increment(self, payload):
        assert payload == {"value": 1, "max": self.limit}
        async with self._lock:
            if self.count >= self.limit:
                return SimpleNamespace(status_code=409)
            self.count += 1
            return SimpleNamespace(status_code=200)


class _AtomicProviderMinuteClient:
    def __init__(self, backend: _AtomicProviderMinuteBackend) -> None:
        self.backend = backend

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, _url, *, headers, json):
        assert headers["X-Appwrite-Key"] == "key"
        return await self.backend.create(json)

    async def patch(self, _url, *, headers, json):
        assert headers["X-Appwrite-Key"] == "key"
        return await self.backend.increment(json)
