"""Admin panel backend contracts: listing, telemetry, reset and audit."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.admin import AppwriteAdminStore, _operation_id
from src.main import _execute_request
from src.validation import SecurityValidationError


def _response(status: int, body=None):
    response = MagicMock(status_code=status)
    response.json.return_value = body or {}
    return response


def _store(monkeypatch) -> AppwriteAdminStore:
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project")
    monkeypatch.setenv("APPWRITE_DATABASE_ID", "yav")
    monkeypatch.setenv("APPWRITE_USERS_TABLE_ID", "users")
    monkeypatch.setenv("APPWRITE_RATE_LIMITS_TABLE_ID", "rate_limits")
    monkeypatch.setenv("APPWRITE_QUOTA_RESERVATIONS_TABLE_ID", "quota_reservations")
    monkeypatch.setenv(
        "APPWRITE_USER_QUOTA_GENERATIONS_TABLE_ID", "user_quota_generations"
    )
    monkeypatch.setenv("APPWRITE_ADMIN_AUDIT_TABLE_ID", "admin_audit_log")
    return AppwriteAdminStore("runtime-key")


def _client(get=None, post=None, patch_method=None):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=get)
    client.post = AsyncMock(side_effect=post)
    client.patch = AsyncMock(side_effect=patch_method)
    return client


@pytest.mark.asyncio
async def test_admin_list_is_paginated_and_returns_lightweight_summaries(monkeypatch):
    store = _store(monkeypatch)
    rows = [
        {
            "$id": "user-1",
            "$createdAt": "2026-01-02T00:00:00+00:00",
            "$updatedAt": "2026-01-03T00:00:00+00:00",
            "email": "one@example.test",
            "name": "One",
            "email_verified": True,
            "subscription": "pro",
            "quota_overrides": '{"checks":12}',
            "secret": "must-not-leak",
        },
        {
            "$id": "user-2",
            "$createdAt": "2026-01-01T00:00:00+00:00",
            "$updatedAt": "2026-01-01T00:00:00+00:00",
            "email": "two@example.test",
            "name": "Two",
            "email_verified": False,
            "subscription": "free",
            "quota_overrides": "{}",
        },
    ]
    client = _client(get=[_response(200, {"rows": rows})])

    with patch("src.admin.httpx.AsyncClient", return_value=client):
        result = await store.list_users(page_size=2, cursor="cursor-1")

    assert result["next_cursor"] == "user-2"
    assert result["users"][0] == {
        "user_id": "user-1",
        "email": "one@example.test",
        "display_name": "One",
        "email_verified": True,
        "subscription": "pro",
        "has_quota_overrides": True,
        "created_at": "2026-01-02T00:00:00+00:00",
        "updated_at": "2026-01-03T00:00:00+00:00",
    }
    assert "secret" not in result["users"][0]
    queries = [
        value
        for key, value in client.get.await_args.kwargs["params"]
        if key == "queries[]"
    ]
    assert "limit(2)" in queries
    assert 'orderDesc("$sequence")' in queries
    assert 'cursorAfter("cursor-1")' in queries


@pytest.mark.asyncio
async def test_admin_detail_separates_user_usage_from_ip_and_provider_scopes(
    monkeypatch,
):
    store = _store(monkeypatch)
    profile = {
        "$id": "target-1",
        "email": "target@example.test",
        "name": "Target",
        "email_verified": True,
        "subscription": "free",
        "quota_overrides": '{"checks":7}',
        "quota_usage_generations": "{}",
    }

    async def get(url, **_kwargs):
        if url.endswith("/users/rows/target-1"):
            return _response(200, profile)
        if "/user_quota_generations/" in url:
            return _response(404)
        if "/rate_limits/" in url:
            return _response(404)
        if url.endswith("/quota_reservations/rows"):
            return _response(
                200,
                {
                    "rows": [
                        {
                            "$id": "reservation-1",
                            "quota_dimension": "admission",
                            "window_start": "2026-01-01",
                            "$createdAt": "2026-01-01T00:00:00+00:00",
                        }
                    ]
                },
            )
        raise AssertionError(url)

    client = _client(get=get)
    with patch("src.admin.httpx.AsyncClient", return_value=client):
        result = await store.get_user_details("target-1")

    assert result["subscription_details"]["defaults"]["checks"]["limit"] == 3
    assert result["subscription_details"]["effective"]["checks"]["limit"] == 7
    assert result["usage"]["user_quotas"]["checks"]["used"] == 0
    assert result["usage"]["user_quotas"]["checks"]["reserved"] is None
    assert result["usage"]["user_quotas"]["checks"]["remaining"] == 7
    assert result["usage"]["active_reservations"]["count"] == 1
    assert result["usage"]["ip_limits"]["attributable_to_target_user"] is False
    assert result["usage"]["provider_budgets"]["attributable_to_target_user"] is False
    assert result["usage"]["provider_budgets"]["quotas"]["resemble_daily"][
        "dimension"
    ] == "global_resemble_daily"
    assert result["usage"]["provider_budgets"]["quotas"]["huggingface_daily"][
        "dimension"
    ] == "global_huggingface_daily"


@pytest.mark.asyncio
async def test_reset_uses_generation_and_never_mutates_old_counter_or_reservations(
    monkeypatch,
):
    store = _store(monkeypatch)
    profile = {
        "$id": "target-1",
        "subscription": "free",
        "quota_overrides": "{}",
        "quota_usage_generations": "{}",
    }
    reset_complete = False

    async def get(url, **_kwargs):
        if url.endswith("/users/rows/target-1"):
            return _response(200, profile)
        if "/user_quota_generations/" in url:
            if reset_complete and url.endswith(
                store.generation_row_id("target-1", "checks")
            ):
                return _response(
                    200, {"user_id": "target-1", "quota_key": "checks", "generation": 3}
                )
            return _response(404)
        raise AssertionError(url)

    async def patch_method(url, **_kwargs):
        nonlocal reset_complete
        assert "/user_quota_generations/" in url
        assert url.endswith("/generation/increment")
        reset_complete = True
        return _response(200, {"generation": 3})

    client = _client(get=get, post=[_response(201)], patch_method=patch_method)
    with patch("src.admin.httpx.AsyncClient", return_value=client):
        policy = await store.reset_user_quota_usage("admin-1", "target-1", "checks")

    assert policy.generation("checks") == 3
    assert client.patch.await_count == 1
    assert all(
        "rate_limits" not in call.args[0] and "quota_reservations" not in call.args[0]
        for call in client.patch.await_args_list
    )
    audit_payload = client.post.await_args.kwargs["json"]["data"]
    assert audit_payload["action"] == "quota_usage_reset"
    assert '"generation":2' in audit_payload["old_value"]
    assert '"generation":3' in audit_payload["new_value"]


@pytest.mark.asyncio
async def test_reset_all_only_advances_user_quota_generations(monkeypatch):
    store = _store(monkeypatch)
    profile = {
        "$id": "target-1",
        "subscription": "free",
        "quota_overrides": "{}",
        "quota_usage_generations": "{}",
    }

    async def get(url, **_kwargs):
        if url.endswith("/users/rows/target-1"):
            return _response(200, profile)
        if "/user_quota_generations/" in url:
            return _response(404)
        raise AssertionError(url)

    client = _client(
        get=get,
        post=[_response(201), _response(201), _response(201)],
        patch_method=[_response(404), _response(404)],
    )
    with patch("src.admin.httpx.AsyncClient", return_value=client):
        await store.reset_all_user_usage("admin-1", "target-1")

    assert client.patch.await_count == 2
    assert all(
        "/user_quota_generations/" in call.args[0]
        for call in client.patch.await_args_list
    )
    audit_payload = client.post.await_args_list[-1].kwargs["json"]["data"]
    assert audit_payload["action"] == "all_quota_usage_reset"


@pytest.mark.asyncio
async def test_reset_marks_legacy_profile_so_its_new_generation_is_used(monkeypatch):
    store = _store(monkeypatch)
    profile = {"$id": "target-1", "plan": "free", "quota_overrides": "{}"}

    async def get(url, **_kwargs):
        if url.endswith("/users/rows/target-1"):
            return _response(200, profile)
        if url.endswith(store.generation_row_id("target-1", "checks")):
            return _response(
                200, {"user_id": "target-1", "quota_key": "checks", "generation": 1}
            )
        if "/user_quota_generations/" in url:
            return _response(404)
        raise AssertionError(url)

    async def patch_method(url, **kwargs):
        if url.endswith("/users/rows/target-1"):
            assert kwargs["json"] == {"data": {"quota_usage_generations": "{}"}}
            profile["quota_usage_generations"] = "{}"
            return _response(200, profile)
        assert url.endswith("/generation/increment")
        return _response(200, {"generation": 1})

    client = _client(get=get, post=[_response(201)], patch_method=patch_method)
    with patch("src.admin.httpx.AsyncClient", return_value=client):
        policy = await store.reset_user_quota_usage("admin-1", "target-1", "checks")

    assert policy.generation("checks") == 1
    assert client.patch.await_args_list[0].args[0].endswith("/users/rows/target-1")


@pytest.mark.asyncio
async def test_reset_retry_with_same_idempotency_key_never_increments_twice(
    monkeypatch,
):
    store = _store(monkeypatch)
    profile = {
        "$id": "target-1",
        "subscription": "free",
        "quota_overrides": "{}",
        "quota_usage_generations": "{}",
    }
    operation_id = _operation_id(
        "admin-1", "quota_usage_reset:checks", "target-1", "request-key-0001"
    )
    operation = {"exists": False, "state": "pending", "generation": 0}

    async def get(url, **_kwargs):
        if url.endswith("/users/rows/target-1"):
            return _response(200, profile)
        if url.endswith(f"/admin_audit_log/rows/{operation_id}"):
            if not operation["exists"]:
                return _response(404)
            return _response(
                200,
                {
                    "actor_user_id": "admin-1",
                    "action": "quota_usage_reset",
                    "target_user_id": "target-1",
                    "operation_key": operation_id,
                    "state": operation["state"],
                },
            )
        if url.endswith(store.generation_row_id("target-1", "checks")):
            if operation["generation"]:
                return _response(
                    200,
                    {"user_id": "target-1", "quota_key": "checks", "generation": 1},
                )
            return _response(404)
        if "/user_quota_generations/" in url:
            return _response(404)
        raise AssertionError(url)

    async def post(url, **_kwargs):
        assert url.endswith("/admin_audit_log/rows")
        operation["exists"] = True
        return _response(201)

    async def patch_method(url, **_kwargs):
        if url.endswith("/generation/increment"):
            operation["generation"] = 1
            return _response(200, {"generation": 1})
        assert url.endswith(f"/admin_audit_log/rows/{operation_id}")
        operation["state"] = "completed"
        return _response(200)

    client = _client(get=get, post=post, patch_method=patch_method)
    with patch("src.admin.httpx.AsyncClient", return_value=client):
        await store.reset_user_quota_usage(
            "admin-1", "target-1", "checks", idempotency_key="request-key-0001"
        )
        policy = await store.reset_user_quota_usage(
            "admin-1", "target-1", "checks", idempotency_key="request-key-0001"
        )

    assert policy.generation("checks") == 1
    assert (
        sum(
            call.args[0].endswith("/generation/increment")
            for call in client.patch.await_args_list
        )
        == 1
    )


@pytest.mark.asyncio
async def test_reset_all_stages_both_generations_in_one_transaction(monkeypatch):
    store = _store(monkeypatch)
    profile = {
        "$id": "target-1",
        "subscription": "free",
        "quota_overrides": "{}",
        "quota_usage_generations": "{}",
    }
    operation_id = _operation_id(
        "admin-1", "all_quota_usage_reset:all", "target-1", "request-key-0002"
    )
    state = {"audit": False, "committed": False}

    async def get(url, **_kwargs):
        if url.endswith("/users/rows/target-1"):
            return _response(200, profile)
        if url.endswith(f"/admin_audit_log/rows/{operation_id}"):
            return _response(404) if not state["audit"] else _response(200, {})
        if "/user_quota_generations/" in url:
            row_id = url.rsplit("/", 1)[-1]
            if not state["committed"]:
                return _response(404)
            quota_key = (
                "checks"
                if row_id == store.generation_row_id("target-1", "checks")
                else "heavy_media_checks"
            )
            return _response(
                200,
                {"user_id": "target-1", "quota_key": quota_key, "generation": 1},
            )
        raise AssertionError(url)

    async def post(url, **_kwargs):
        if url.endswith("/admin_audit_log/rows"):
            state["audit"] = True
            return _response(201)
        if url.endswith("/tablesdb/transactions"):
            return _response(201, {"$id": "tx-1"})
        assert url.endswith("/user_quota_generations/rows")
        return _response(201)

    async def patch_method(url, **_kwargs):
        if url.endswith("/tablesdb/transactions/tx-1"):
            state["committed"] = True
            return _response(200)
        assert url.endswith(f"/admin_audit_log/rows/{operation_id}")
        return _response(200)

    client = _client(get=get, post=post, patch_method=patch_method)
    with patch("src.admin.httpx.AsyncClient", return_value=client):
        policy = await store.reset_all_user_usage(
            "admin-1", "target-1", idempotency_key="request-key-0002"
        )

    assert policy.generation("checks") == 1
    generation_creates = [
        call
        for call in client.post.await_args_list
        if call.args[0].endswith("/user_quota_generations/rows")
    ]
    assert len(generation_creates) == 2
    assert all(
        call.kwargs["json"]["transactionId"] == "tx-1" for call in generation_creates
    )


def test_reset_actions_require_a_bounded_idempotency_key():
    from src.validation import validate_request_payload

    with pytest.raises(SecurityValidationError):
        validate_request_payload(
            {
                "action": "admin_reset_user_quota_usage",
                "targetUserId": "target-1",
                "quotaKey": "checks",
            }
        )
    request = validate_request_payload(
        {
            "action": "admin_reset_all_user_usage",
            "targetUserId": "target-1",
            "idempotencyKey": "request-key-0001",
        }
    )
    assert request.idempotency_key == "request-key-0001"


@pytest.mark.asyncio
async def test_audit_list_is_paginated_and_target_filtered(monkeypatch):
    store = _store(monkeypatch)
    client = _client(
        get=[
            _response(
                200,
                {
                    "rows": [
                        {
                            "$id": "event-1",
                            "actor_user_id": "admin-1",
                            "target_user_id": "target-1",
                            "action": "subscription_changed",
                            "old_value": '{"subscription":"free"}',
                            "new_value": '{"subscription":"pro"}',
                            "created_at": "2026-01-01T00:00:00+00:00",
                        }
                    ]
                },
            )
        ]
    )

    with patch("src.admin.httpx.AsyncClient", return_value=client):
        result = await store.list_audit_events(page_size=1, target_user_id="target-1")

    assert result["events"][0]["action"] == "subscription_changed"
    assert result["next_cursor"] == "event-1"
    queries = [
        value
        for key, value in client.get.await_args.kwargs["params"]
        if key == "queries[]"
    ]
    assert 'equal("target_user_id",["target-1"])' in queries
    assert 'orderDesc("$sequence")' in queries


@pytest.mark.asyncio
async def test_normal_user_cannot_list_users_or_audit(monkeypatch):
    monkeypatch.setenv("SYSTEM_ADMIN_USER_IDS", "admin-1")
    for action in (
        {"action": "admin_list_users"},
        {"action": "admin_list_audit_events"},
    ):
        with (
            patch(
                "src.main.get_authenticated_account",
                new=AsyncMock(
                    return_value={
                        "$id": "user-1",
                        "emailVerification": True,
                    }
                ),
            ),
            patch(
                "src.main.ensure_user_profile",
                new=AsyncMock(return_value={"plan": "free"}),
            ),
        ):
            with pytest.raises(SecurityValidationError) as raised:
                await _execute_request(action, "runtime-key", "user-1", "runtime-jwt")
        assert raised.value.code == "admin_access_denied"


@pytest.mark.asyncio
async def test_admin_list_action_uses_target_only_as_filter_not_identity(monkeypatch):
    monkeypatch.setenv("SYSTEM_ADMIN_USER_IDS", "admin-1")
    store = type(
        "Store",
        (),
        {
            "list_audit_events": AsyncMock(
                return_value={"events": [], "next_cursor": None, "page_size": 25}
            )
        },
    )()
    with (
        patch(
            "src.main.get_authenticated_account",
            new=AsyncMock(
                return_value={
                    "$id": "admin-1",
                    "emailVerification": True,
                }
            ),
        ),
        patch(
            "src.main.ensure_user_profile", new=AsyncMock(return_value={"plan": "free"})
        ),
        patch(
            "src.main.AppwriteAdminStore",
            return_value=store,
        ),
    ):
        result = await _execute_request(
            {"action": "admin_list_audit_events", "targetUserId": "target-1"},
            "runtime-key",
            "admin-1",
            "runtime-jwt",
        )

    assert result["events"] == []
    store.list_audit_events.assert_awaited_once_with(
        page_size=25, cursor=None, target_user_id="target-1"
    )


def test_invalid_admin_quota_reset_input_is_rejected_before_store_access():
    with pytest.raises(SecurityValidationError):
        from src.validation import validate_request_payload

        validate_request_payload(
            {
                "action": "admin_reset_user_quota_usage",
                "targetUserId": "target-1",
                "quotaKey": -1,
            }
        )
