"""HTTP request-shape checks for the TablesDB quota transaction client."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.rate_limit import AppwriteTablesRateLimitStore, RateLimitError


def _response(status, body=None):
    response = MagicMock(status_code=status)
    response.json.return_value = body if body is not None else {}
    return response


def _store(monkeypatch):
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project")
    monkeypatch.setenv("APPWRITE_DATABASE_ID", "database")
    monkeypatch.setenv("APPWRITE_RATE_LIMITS_TABLE_ID", "rate_limits")
    monkeypatch.setenv("APPWRITE_QUOTA_RESERVATIONS_TABLE_ID", "quota_reservations")
    monkeypatch.setenv("APPWRITE_USERS_TABLE_ID", "users")
    monkeypatch.setenv("RATE_LIMIT_IP_HMAC_KEY", "test-secret")
    monkeypatch.setenv("FREE_DAILY_LIMIT", "3")
    return AppwriteTablesRateLimitStore("server-key", now=datetime(2026, 8, 9, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_first_quota_reservation_uses_staged_rows_and_commit(monkeypatch):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=[_response(200, {"plan": "free"}), _response(404)])
    client.post = AsyncMock(side_effect=[_response(201, {"$id": "tx-1"}), _response(201), _response(201)])
    client.patch = AsyncMock(return_value=_response(200))
    with patch("src.rate_limit.httpx.AsyncClient", return_value=client):
        reservation = await _store(monkeypatch).reserve_quota("trusted-user")
    assert reservation.state == "reserved"
    transaction_call = client.post.call_args_list[0]
    assert transaction_call.args[0].endswith("/tablesdb/transactions")
    assert "X-Appwrite-Key" in transaction_call.kwargs["headers"]
    quota_call = client.post.call_args_list[1]
    assert quota_call.args[0].endswith("/tables/rate_limits/rows")
    assert quota_call.kwargs["json"]["data"]["count"] == 1
    assert quota_call.kwargs["json"]["transactionId"] == "tx-1"
    reservation_call = client.post.call_args_list[2]
    assert reservation_call.args[0].endswith("/tables/quota_reservations/rows")
    assert reservation_call.kwargs["json"]["data"]["state"] == "reserved"
    assert reservation_call.kwargs["json"]["transactionId"] == "tx-1"
    assert client.patch.call_args.kwargs["json"] == {"commit": True}


@pytest.mark.asyncio
async def test_missing_transaction_id_fails_closed(monkeypatch):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=[_response(200, {"plan": "free"}), _response(404)])
    client.post = AsyncMock(return_value=_response(201, {}))
    with patch("src.rate_limit.httpx.AsyncClient", return_value=client):
        with pytest.raises(RateLimitError) as raised:
            await _store(monkeypatch).reserve_quota("trusted-user")
    assert raised.value.code == "rate_limit_unavailable"
