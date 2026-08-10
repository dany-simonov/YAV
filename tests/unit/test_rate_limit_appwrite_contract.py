"""HTTP request-shape checks for the TablesDB quota transaction client."""
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from src.rate_limit import AppwriteTablesRateLimitStore, QuotaReservation, RateLimitError, enforce_admission


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


@pytest.mark.asyncio
async def test_only_structured_row_max_maps_to_daily_quota(monkeypatch):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=[_response(200, {"plan": "free"}), _response(200)])
    client.post = AsyncMock(return_value=_response(201, {"$id": "tx-1"}))
    client.patch = AsyncMock(return_value=_response(400, {"type": "row_max_exceeded"}))
    with patch("src.rate_limit.httpx.AsyncClient", return_value=client):
        with pytest.raises(RateLimitError) as raised:
            await _store(monkeypatch).reserve_quota("trusted-user")
    assert raised.value.code == "daily_quota_exceeded"
    assert raised.value.retry_after and raised.value.retry_after > 0


@pytest.mark.asyncio
async def test_arbitrary_staged_400_fails_closed_not_as_quota(monkeypatch):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=[_response(200, {"plan": "free"}), _response(200)])
    client.post = AsyncMock(return_value=_response(201, {"$id": "tx-1"}))
    client.patch = AsyncMock(return_value=_response(400, {"type": "row_invalid_structure"}))
    with patch("src.rate_limit.httpx.AsyncClient", return_value=client):
        with pytest.raises(RateLimitError) as raised:
            await _store(monkeypatch).reserve_quota("trusted-user")
    assert raised.value.code == "rate_limit_unavailable"


@pytest.mark.asyncio
async def test_commit_conflict_retries_and_then_returns_one_reservation(monkeypatch):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=[_response(200, {"plan": "free"}), _response(200), _response(200)])
    client.post = AsyncMock(side_effect=[_response(201, {"$id": "tx-1"}), _response(201), _response(201, {"$id": "tx-2"}), _response(201)])
    client.patch = AsyncMock(side_effect=[_response(200), _response(409, {"type": "transaction_conflict"}), _response(200), _response(200)])
    with patch("src.rate_limit.httpx.AsyncClient", return_value=client):
        reservation = await _store(monkeypatch).reserve_quota("trusted-user")
    assert reservation.state == "reserved"
    assert sum(call.kwargs["json"] == {"commit": True} for call in client.patch.call_args_list) == 2


@pytest.mark.asyncio
async def test_ambiguous_commit_timeout_fails_closed_without_retry(monkeypatch):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=[_response(200, {"plan": "free"}), _response(404)])
    client.post = AsyncMock(side_effect=[_response(201, {"$id": "tx-1"}), _response(201), _response(201)])
    client.patch = AsyncMock(side_effect=httpx.TimeoutException("commit timeout"))
    with patch("src.rate_limit.httpx.AsyncClient", return_value=client):
        with pytest.raises(RateLimitError) as raised:
            await _store(monkeypatch).reserve_quota("trusted-user")
    assert raised.value.code == "rate_limit_unavailable"
    assert client.post.await_count == 3


@pytest.mark.asyncio
async def test_refund_stages_state_and_atomic_decrement_in_same_transaction(monkeypatch):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=_response(201, {"$id": "tx-1"}))
    client.get = AsyncMock(return_value=_response(200, {"state": "reserved"}))
    client.patch = AsyncMock(side_effect=[_response(200), _response(200), _response(200)])
    reservation = QuotaReservation("reservation", "trusted-user", "quota_daily", "2026-08-09", "reserved")
    with patch("src.rate_limit.httpx.AsyncClient", return_value=client):
        await _store(monkeypatch).transition_quota(reservation, "refunded")
    update, decrement, commit = client.patch.call_args_list
    assert update.kwargs["json"] == {"data": {"state": "refunded"}, "transactionId": "tx-1"}
    assert decrement.args[0].endswith("/count/decrement")
    assert decrement.kwargs["json"] == {"value": 1, "min": 0, "transactionId": "tx-1"}
    assert commit.kwargs["json"] == {"commit": True}


@pytest.mark.asyncio
async def test_consumed_reservation_is_idempotent_and_never_decrements(monkeypatch):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=_response(201, {"$id": "tx-1"}))
    client.get = AsyncMock(return_value=_response(200, {"state": "consumed"}))
    client.patch = AsyncMock(return_value=_response(200))
    reservation = QuotaReservation("reservation", "trusted-user", "quota_daily", "2026-08-09", "reserved")
    with patch("src.rate_limit.httpx.AsyncClient", return_value=client):
        await _store(monkeypatch).transition_quota(reservation, "refunded")
    assert client.patch.await_args.kwargs["json"] == {"rollback": True}


@pytest.mark.asyncio
async def test_trusted_plan_backend_failure_is_fail_closed(monkeypatch):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=_response(503))
    with patch("src.rate_limit.httpx.AsyncClient", return_value=client):
        with pytest.raises(RateLimitError) as raised:
            await _store(monkeypatch).trusted_plan("trusted-user")
    assert raised.value.code == "rate_limit_unavailable"


@pytest.mark.asyncio
async def test_abuse_counter_backend_failure_is_fail_closed(monkeypatch):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=_response(503))
    store = _store(monkeypatch)
    with patch("src.rate_limit.httpx.AsyncClient", return_value=client):
        with pytest.raises(RateLimitError) as raised:
            await enforce_admission(store, "trusted-user", "192.0.2.1")
    assert raised.value.code == "rate_limit_unavailable"


@pytest.mark.asyncio
async def test_admission_create_uses_runtime_key_and_cloud_schema_shape(monkeypatch):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=_response(201))
    store = _store(monkeypatch)
    with patch("src.rate_limit.httpx.AsyncClient", return_value=client):
        await store.consume("ip_minute", store.ip_subject("192.0.2.1"), "minute", 10)

    call = client.post.await_args
    assert call.args[0] == "https://appwrite.example/v1/tablesdb/database/tables/rate_limits/rows"
    assert call.kwargs["headers"] == {"X-Appwrite-Project": "project", "X-Appwrite-Key": "server-key"}
    payload = call.kwargs["json"]
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,35}", payload["rowId"])
    assert payload["data"]["dimension"] == "ip_minute"
    assert len(payload["data"]["subject"]) == 48
    assert "192.0.2.1" not in payload["rowId"] and "192.0.2.1" not in payload["data"]["subject"]
    assert payload["data"]["window_start"] == "2026-08-09T00:00"
    assert len(payload["data"]["window_start"]) <= 16
    assert datetime.fromisoformat(payload["data"]["window_end"]).tzinfo is not None
    assert payload["data"]["count"] == 1 and isinstance(payload["data"]["count"], int)


@pytest.mark.asyncio
async def test_admission_create_failure_logs_only_safe_appwrite_metadata(monkeypatch, caplog):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=_response(400, {"type": "row_invalid_structure", "code": 400}))
    store = _store(monkeypatch)
    with patch("src.rate_limit.httpx.AsyncClient", return_value=client), pytest.raises(RateLimitError) as raised:
        await store.consume("ip_minute", store.ip_subject("192.0.2.1"), "minute", 10)
    assert raised.value.code == "rate_limit_unavailable"
    assert "operation=rate_limits.create" in caplog.text
    assert "status_code=400" in caplog.text
    assert "appwrite_type=row_invalid_structure" in caplog.text
    assert "appwrite_code=400" in caplog.text
    assert "server-key" not in caplog.text
    assert "test-secret" not in caplog.text
    assert "192.0.2.1" not in caplog.text


@pytest.mark.asyncio
async def test_unstructured_increment_400_is_diagnostic_unavailable_not_false_limit(monkeypatch, caplog):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=_response(409))
    client.patch = AsyncMock(return_value=_response(400, {"type": "row_invalid_structure", "code": 400}))
    store = _store(monkeypatch)
    with patch("src.rate_limit.httpx.AsyncClient", return_value=client), pytest.raises(RateLimitError) as raised:
        await store.consume("ip_minute", store.ip_subject("192.0.2.1"), "minute", 10)
    assert raised.value.code == "rate_limit_unavailable"
    assert "operation=rate_limits.increment" in caplog.text
