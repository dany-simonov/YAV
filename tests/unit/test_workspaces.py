"""Workspace server-side authorization and transaction coverage."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.main import _execute_request
from src.validation import (
    SecurityValidationError,
    canonicalize_email,
    validate_request_payload,
)
from src.workspaces import AppwriteWorkspaceStore, WorkspaceAccess, WorkspaceError


def _store(monkeypatch) -> AppwriteWorkspaceStore:
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project")
    monkeypatch.setenv("APPWRITE_DATABASE_ID", "yav")
    monkeypatch.setenv("APPWRITE_USERS_TABLE_ID", "users")
    return AppwriteWorkspaceStore("runtime-key")


def _http_client(**operations):
    client = MagicMock()
    for method in ("get", "post", "patch"):
        result = operations.get(method)
        if isinstance(result, BaseException):
            call = AsyncMock(side_effect=result)
        else:
            call = AsyncMock(return_value=result)
        setattr(client, method, call)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _query_objects(queries):
    return [json.loads(query) for query in queries]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["get", "create", "patch", "list", "transaction_create", "commit"]
)
async def test_workspace_http_timeouts_use_one_unavailable_contract(monkeypatch, operation):
    method = "get" if operation in {"get", "list"} else "patch" if operation in {"patch", "commit"} else "post"
    client = _http_client(**{method: httpx.ReadTimeout("timed out")})
    store = _store(monkeypatch)

    with patch("src.workspaces.httpx.AsyncClient", return_value=client), pytest.raises(
        WorkspaceError
    ) as raised:
        if operation == "get":
            await store._get_optional(store._workspaces_url, "workspace-1")
        elif operation == "create":
            await store._create(store._invitations_url, "invitation-1", {})
        elif operation == "patch":
            await store._patch(store._invitations_url, "invitation-1", {})
        elif operation == "list":
            await store._list(store._memberships_url, [])
        elif operation == "transaction_create":
            await store._create_transaction()
        else:
            await store._commit("transaction-1")

    assert (raised.value.code, raised.value.status_code) == (
        "workspace_unavailable",
        503,
    )


@pytest.mark.parametrize("ttl", [59, 3601])
def test_workspace_rejects_out_of_range_transaction_ttl_before_http(monkeypatch, ttl):
    monkeypatch.setattr(AppwriteWorkspaceStore, "TRANSACTION_TTL_SECONDS", ttl)
    client = _http_client()

    with patch("src.workspaces.httpx.AsyncClient", return_value=client), pytest.raises(
        WorkspaceError
    ) as raised:
        _store(monkeypatch)

    assert (raised.value.code, raised.value.status_code) == (
        "workspace_unavailable",
        503,
    )
    client.post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl", [60, 3600])
async def test_workspace_transaction_ttl_uses_valid_appwrite_value(monkeypatch, ttl):
    monkeypatch.setattr(AppwriteWorkspaceStore, "TRANSACTION_TTL_SECONDS", ttl)
    created = MagicMock(status_code=201)
    created.json.return_value = {"$id": "transaction-1"}
    client = _http_client(post=created)
    store = _store(monkeypatch)

    with patch("src.workspaces.httpx.AsyncClient", return_value=client):
        assert await store._create_transaction() == "transaction-1"

    assert client.post.await_args.kwargs["json"] == {"ttl": ttl}


@pytest.mark.asyncio
async def test_workspace_create_uses_valid_transaction_ttl(monkeypatch):
    created = MagicMock(status_code=201)
    created.json.return_value = {"$id": "transaction-1"}
    staged = MagicMock(status_code=201)
    committed = MagicMock(status_code=200)
    client = _http_client(patch=committed)
    client.post = AsyncMock(side_effect=[created, staged, staged])
    store = _store(monkeypatch)

    with patch("src.workspaces.httpx.AsyncClient", return_value=client):
        result = await store.create_workspace("owner-1", "Команда")

    assert result["role"] == "owner"
    assert client.post.await_args_list[0].kwargs["json"] == {"ttl": 60}


@pytest.mark.asyncio
async def test_workspace_5xx_and_non_json_error_response_are_controlled(monkeypatch):
    response = MagicMock(status_code=500)
    response.json.side_effect = ValueError("not json")
    client = _http_client(get=response)
    store = _store(monkeypatch)

    with patch("src.workspaces.httpx.AsyncClient", return_value=client), pytest.raises(
        WorkspaceError
    ) as raised:
        await store._get_optional(store._workspaces_url, "workspace-1")

    assert (raised.value.code, raised.value.status_code) == (
        "workspace_unavailable",
        503,
    )


@pytest.mark.asyncio
async def test_workspace_malformed_success_json_is_controlled(monkeypatch):
    response = MagicMock(status_code=200)
    response.json.side_effect = ValueError("not json")
    client = _http_client(get=response)
    store = _store(monkeypatch)

    with patch("src.workspaces.httpx.AsyncClient", return_value=client), pytest.raises(
        WorkspaceError
    ) as raised:
        await store._list(store._memberships_url, [])

    assert (raised.value.code, raised.value.status_code) == (
        "workspace_unavailable",
        503,
    )


@pytest.mark.asyncio
async def test_workspace_observability_is_safe_and_classifies_list_failure(monkeypatch):
    log, error = MagicMock(), MagicMock()
    store = _store(monkeypatch)
    store._diagnostic_log = log
    store._diagnostic_error_log = error
    store._action = "workspace_get"
    store._correlation_id = "a" * 32
    response = MagicMock(status_code=400)
    response.json.return_value = {
        "type": "general_query_invalid",
        "code": 400,
        "message": "Invalid query for member@example.test; Authorization: Bearer super-secret",
    }
    client = _http_client(get=response)

    with patch("src.workspaces.httpx.AsyncClient", return_value=client), pytest.raises(
        WorkspaceError
    ):
        await store._list(
            store._memberships_url,
            ['{"method":"equal","attribute":"email","values":["member@example.test"]}'],
        )

    request_log = log.call_args.args[0]
    error_log = error.call_args.args[0]
    assert "action=workspace_get" in request_log
    assert "operation=workspace.rows.list" in request_log
    assert "method=GET resource=workspace_memberships.rows" in request_log
    assert "query_types=equal" in request_log
    assert "member@example.test" not in request_log
    assert "correlation_id=" + "a" * 32 in request_log
    assert client.get.await_args.kwargs["params"] == [
        ("queries[0]", '{"method":"equal","attribute":"email","values":["member@example.test"]}')
    ]
    assert "category=invalid_query" in error_log
    assert "upstream_status=400" in error_log
    assert "appwrite_type=general_query_invalid appwrite_code=400" in error_log
    assert "<redacted-email>" in error_log
    for sensitive_value in ("member@example.test", "super-secret", "runtime-key", "Bearer"):
        assert sensitive_value not in error_log


@pytest.mark.asyncio
async def test_workspace_action_start_log_and_callbacks_are_safe(monkeypatch):
    monkeypatch.setenv("APPWRITE_DATABASE_ID", "yav")
    monkeypatch.setenv("APPWRITE_WORKSPACES_TABLE_ID", "workspaces")
    monkeypatch.setenv("APPWRITE_WORKSPACE_MEMBERSHIPS_TABLE_ID", "workspace_memberships")
    monkeypatch.setenv("APPWRITE_WORKSPACE_INVITATIONS_TABLE_ID", "workspace_invitations")
    log, error = MagicMock(), MagicMock()
    store = type(
        "Store",
        (),
        {"get_my_workspaces": AsyncMock(return_value={"workspaces": [], "next_cursor": None, "page_size": 1})},
    )()

    with (
        patch(
            "src.main.get_authenticated_account",
            new=AsyncMock(return_value={"$id": "member-1", "email": "member@example.test", "emailVerification": True}),
        ),
        patch("src.main.ensure_user_profile", new=AsyncMock(return_value={})),
        patch("src.main.AppwriteWorkspaceStore", return_value=store) as store_class,
    ):
        result = await _execute_request(
            {"action": "workspace_get", "pageSize": 1},
            "runtime-key",
            "member-1",
            "runtime-jwt",
            diagnostic_log=log,
            diagnostic_error_log=error,
        )

    assert result["workspaces"] == []
    start_log = log.call_args.args[0]
    assert "workspace action=workspace_get" in start_log
    assert "database_id=yav workspaces_table_id=workspaces" in start_log
    assert "memberships_table_id=workspace_memberships" in start_log
    assert "invitations_table_id=workspace_invitations" in start_log
    assert "correlation_id=" in start_log
    for sensitive_value in ("member@example.test", "runtime-key", "runtime-jwt"):
        assert sensitive_value not in start_log
    error.assert_not_called()
    assert callable(store_class.call_args.kwargs["diagnostic_log"])
    assert callable(store_class.call_args.kwargs["diagnostic_error_log"])


@pytest.mark.asyncio
async def test_workspace_expected_404_and_member_limit_remain_domain_errors(monkeypatch):
    missing = MagicMock(status_code=404)
    missing.json.return_value = {}
    store = _store(monkeypatch)
    client = _http_client(get=missing)

    with patch("src.workspaces.httpx.AsyncClient", return_value=client):
        assert await store._get_optional(store._workspaces_url, "workspace-1") is None

    store._create_transaction = AsyncMock(return_value="transaction-1")
    store._get_in_transaction = AsyncMock(
        side_effect=[
            {
                "$id": "invitation-1",
                "status": "pending",
                "email": "member@example.test",
                "expires_at": "2999-01-01T00:00:00+00:00",
            },
            None,
            {"$id": "workspace-1"},
        ]
    )
    store._increment_member_count = AsyncMock(return_value="capacity")
    store._rollback = AsyncMock()
    with pytest.raises(WorkspaceError) as raised:
        await store.accept_invitation("member-1", "member@example.test", "workspace-1")

    assert (raised.value.code, raised.value.status_code) == (
        "workspace_member_limit",
        409,
    )


def test_workspace_requests_reject_client_supplied_identity():
    with pytest.raises(SecurityValidationError):
        validate_request_payload(
            {
                "action": "workspace_accept_invitation",
                "workspaceId": "workspace-1",
                "userId": "another-user",
            }
        )


def test_workspace_request_contract_selects_expected_action():
    request = validate_request_payload(
        {
            "action": "workspace_invite_member",
            "workspaceId": "workspace-1",
            "email": "A@EXAMPLE.TEST",
        }
    )

    assert request.workspace_id == "workspace-1"
    assert request.email == "A@EXAMPLE.TEST"


def test_workspace_history_request_uses_explicit_cursor_after_and_rejects_identity_filter():
    request = validate_request_payload(
        {
            "action": "workspace_list_history",
            "workspaceId": "workspace-1",
            "pageSize": 2,
            "cursorAfter": "check-1",
        }
    )

    assert request.workspace_id == "workspace-1"
    assert request.page_size == 2
    assert request.cursor_after == "check-1"
    with pytest.raises(SecurityValidationError) as raised:
        validate_request_payload(
            {
                "action": "workspace_list_history",
                "workspaceId": "workspace-1",
                "userId": "another-member",
            }
        )
    assert raised.value.code == "invalid_request"


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "workspace_list_history", "workspaceId": "workspace-1", "pageSize": 0},
        {
            "action": "workspace_list_history",
            "workspaceId": "workspace-1",
            "cursorAfter": "bad/id",
        },
    ],
)
def test_workspace_history_rejects_invalid_pagination(payload):
    with pytest.raises(SecurityValidationError) as raised:
        validate_request_payload(payload)

    assert raised.value.code == "invalid_request"


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("User@Test.com", "user@test.com"),
        ("user@test.com", "user@test.com"),
        (" user@test.com ", "user@test.com"),
    ],
)
def test_canonicalize_email_has_one_representation(raw, canonical):
    assert canonicalize_email(raw) == canonical


@pytest.mark.asyncio
async def test_accept_uses_email_from_runtime_account(monkeypatch):
    store = type(
        "Store",
        (),
        {
            "accept_invitation": AsyncMock(
                return_value={"workspace_id": "workspace-1", "status": "accepted"}
            )
        },
    )()
    with (
        patch(
            "src.main.get_authenticated_account",
            new=AsyncMock(
                return_value={
                    "$id": "member-1",
                    "email": "member@example.test",
                    "emailVerification": True,
                }
            ),
        ),
        patch("src.main.ensure_user_profile", new=AsyncMock(return_value={})),
        patch("src.main.AppwriteWorkspaceStore", return_value=store),
    ):
        result = await _execute_request(
            {"action": "workspace_accept_invitation", "workspaceId": "workspace-1"},
            "runtime-key",
            "member-1",
            "runtime-jwt",
        )

    assert result == {"workspace_id": "workspace-1", "status": "accepted"}
    store.accept_invitation.assert_awaited_once_with(
        "member-1", "member@example.test", "workspace-1"
    )


@pytest.mark.asyncio
async def test_history_dispatches_only_explicit_workspace_and_cursor_after(monkeypatch):
    store = type(
        "Store",
        (),
        {
            "list_history": AsyncMock(
                return_value={"checks": [], "next_cursor": None, "page_size": 2}
            )
        },
    )()
    with (
        patch(
            "src.main.get_authenticated_account",
            new=AsyncMock(
                return_value={"$id": "member-1", "emailVerification": True}
            ),
        ),
        patch("src.main.ensure_user_profile", new=AsyncMock(return_value={})),
        patch("src.main.AppwriteWorkspaceStore", return_value=store),
    ):
        result = await _execute_request(
            {
                "action": "workspace_list_history",
                "workspaceId": "workspace-1",
                "pageSize": 2,
                "cursorAfter": "check-1",
            },
            "runtime-key",
            "member-1",
            "runtime-jwt",
        )

    assert result == {"checks": [], "next_cursor": None, "page_size": 2}
    store.list_history.assert_awaited_once_with(
        "member-1", "workspace-1", page_size=2, cursor_after="check-1"
    )


@pytest.mark.asyncio
async def test_create_workspace_stages_owner_membership_in_same_transaction(
    monkeypatch,
):
    store = _store(monkeypatch)
    store._run_transaction = AsyncMock()

    result = await store.create_workspace("owner-1", "Команда")

    operations = store._run_transaction.await_args.args[0]
    assert result["role"] == "owner"
    assert operations[0][1] == store._workspaces_url
    assert operations[1][1] == store._memberships_url
    assert operations[1][3]["user_id"] == "owner-1"
    assert operations[1][3]["role"] == "owner"
    assert operations[1][3]["workspace_id"] == result["workspace_id"]


@pytest.mark.asyncio
async def test_owner_can_read_only_own_workspace(monkeypatch):
    store = _store(monkeypatch)
    store._list = AsyncMock(
        return_value={
            "rows": [
                {"workspace_id": "workspace-1", "role": "owner", "status": "active"}
            ]
        }
    )
    store._get_optional = AsyncMock(
        return_value={
            "$id": "workspace-1",
            "name": "Команда",
            "owner_user_id": "owner-1",
            "member_count": 0,
        }
    )

    result = await store.get_my_workspaces("owner-1")

    assert result == {
        "workspaces": [
            {
                "workspace_id": "workspace-1",
                "name": "Команда",
                "owner_user_id": "owner-1",
                "member_count": 0,
                "role": "owner",
                "provider_quota_overrides": {},
            }
        ],
        "next_cursor": None,
        "page_size": 25,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["owner", "member"])
async def test_resolve_workspace_access_allows_active_owner_and_member(monkeypatch, role):
    store = _store(monkeypatch)
    workspace = {"$id": "workspace-1", "owner_user_id": "owner-1", "name": "Команда"}
    store._get_optional = AsyncMock(
        side_effect=[
            {"workspace_id": "workspace-1", "user_id": "actor-1", "role": role, "status": "active"},
            workspace,
        ]
    )

    access = await store.resolve_workspace_access("actor-1", "workspace-1")

    assert access.workspace_id == "workspace-1"
    assert access.actor_user_id == "actor-1"
    assert access.role == role
    assert dict(access.workspace) == workspace
    with pytest.raises(TypeError):
        access.workspace["name"] = "Изменено"


@pytest.mark.asyncio
async def test_resolve_workspace_access_uses_only_explicit_workspace_id(monkeypatch):
    store = _store(monkeypatch)
    workspace_id = "workspace-a"
    membership_id = store.membership_id(workspace_id, "actor-1")
    store._get_optional = AsyncMock(
        side_effect=[
            {"workspace_id": workspace_id, "user_id": "actor-1", "role": "member", "status": "active"},
            {"$id": workspace_id},
        ]
    )

    access = await store.resolve_workspace_access("actor-1", workspace_id)

    assert access.workspace_id == workspace_id
    assert store._get_optional.await_args_list[0].args == (
        store._memberships_url,
        membership_id,
    )
    assert store._get_optional.await_args_list[1].args == (
        store._workspaces_url,
        workspace_id,
    )


@pytest.mark.asyncio
async def test_resolve_workspace_access_rejects_missing_workspace(monkeypatch):
    store = _store(monkeypatch)
    store._get_optional = AsyncMock(
        side_effect=[
            {"workspace_id": "workspace-1", "role": "member", "status": "active"},
            None,
        ]
    )

    with pytest.raises(WorkspaceError) as raised:
        await store.resolve_workspace_access("actor-1", "workspace-1")

    assert raised.value.code == "workspace_not_found"
    assert raised.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("membership", [None, {"role": "member", "status": "inactive"}])
async def test_resolve_workspace_access_rejects_non_active_membership(monkeypatch, membership):
    store = _store(monkeypatch)
    store._get_optional = AsyncMock(return_value=membership)

    with pytest.raises(WorkspaceError) as raised:
        await store.resolve_workspace_access("actor-1", "workspace-1")

    assert raised.value.code == "workspace_access_denied"
    assert raised.value.status_code == 403
    assert store._get_optional.await_count == 1


@pytest.mark.asyncio
async def test_owner_history_queries_only_requested_workspace_and_returns_all_actors(monkeypatch):
    store = _store(monkeypatch)
    store._get_optional = AsyncMock(
        side_effect=[
            {"role": "owner", "status": "active"},
            {"$id": "workspace-a", "owner_user_id": "owner-1"},
        ]
    )
    store._list = AsyncMock(
        return_value={
            "rows": [
                {
                    "$id": "check-1",
                    "workspace_id": "workspace-a",
                    "user_id": "member-a",
                    "media_type": "text",
                    "status": "completed",
                    "verdict": "REAL",
                    "provider": "gemini",
                    "model": "gemini_text_verification",
                    "processing_ms": 5,
                    "$createdAt": "2026-09-28T10:00:00+00:00",
                    "$permissions": ["must-not-leak"],
                },
                {
                    "$id": "check-2",
                    "workspace_id": "workspace-a",
                    "user_id": "member-b",
                    "media_type": "image",
                    "status": "completed",
                    "verdict": "FAKE",
                    "$createdAt": "2026-09-28T09:00:00+00:00",
                },
            ]
        }
    )

    result = await store.list_history("owner-1", "workspace-a", page_size=2)

    queries = _query_objects(store._list.await_args.args[1])
    assert {"method": "equal", "attribute": "workspace_id", "values": ["workspace-a"]} in queries
    assert not any(query.get("attribute") == "user_id" for query in queries)
    assert {"method": "orderDesc", "attribute": "$sequence"} in queries
    assert result["checks"][0]["user_id"] == "member-a"
    assert result["checks"][1]["user_id"] == "member-b"
    assert "$permissions" not in result["checks"][0]


@pytest.mark.asyncio
async def test_member_history_queries_only_actor_rows_in_requested_workspace(monkeypatch):
    store = _store(monkeypatch)
    store._get_optional = AsyncMock(
        side_effect=[
            {"role": "member", "status": "active"},
            {"$id": "workspace-a"},
        ]
    )
    store._list = AsyncMock(
        return_value={
            "rows": [
                {
                    "$id": "check-1",
                    "workspace_id": "workspace-a",
                    "user_id": "member-a",
                    "media_type": "text",
                    "status": "completed",
                    "verdict": "REAL",
                    "$createdAt": "2026-09-28T10:00:00+00:00",
                }
            ]
        }
    )

    result = await store.list_history(
        "member-a", "workspace-a", page_size=1, cursor_after="check-before"
    )

    queries = _query_objects(store._list.await_args.args[1])
    assert {"method": "equal", "attribute": "workspace_id", "values": ["workspace-a"]} in queries
    assert {"method": "equal", "attribute": "user_id", "values": ["member-a"]} in queries
    assert {"method": "cursorAfter", "values": ["check-before"]} in queries
    assert result["checks"] == [
        {
            "check_id": "check-1",
            "workspace_id": "workspace-a",
            "user_id": "member-a",
            "media_type": "text",
            "status": "completed",
            "verdict": "REAL",
            "provider": "",
            "model": "",
            "ai_probability": None,
            "decision_confidence": None,
            "authenticity_index": None,
            "processing_ms": None,
            "source_label": "",
            "created_at": "2026-09-28T10:00:00+00:00",
        }
    ]
    assert result["next_cursor"] == "check-1"


@pytest.mark.asyncio
async def test_removed_member_cannot_query_workspace_history(monkeypatch):
    store = _store(monkeypatch)
    store._get_optional = AsyncMock(
        return_value={"role": "member", "status": "inactive"}
    )
    store._list = AsyncMock()

    with pytest.raises(WorkspaceError) as raised:
        await store.list_history("member-a", "workspace-a")

    assert raised.value.code == "workspace_access_denied"
    assert raised.value.status_code == 403
    store._list.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_workspace_owner_can_manage_invitation(monkeypatch):
    store = _store(monkeypatch)
    store._get_optional = AsyncMock(
        side_effect=[
            {"role": "member", "status": "active"},
            {"owner_user_id": "owner-1"},
        ]
    )

    with pytest.raises(WorkspaceError) as raised:
        await store._require_owner("member-1", "workspace-1")

    assert raised.value.code == "workspace_owner_required"


@pytest.mark.asyncio
async def test_invite_rejects_email_of_existing_member(monkeypatch):
    store = _store(monkeypatch)
    store._require_owner = AsyncMock()
    store._list = AsyncMock(return_value={"rows": [{"user_id": "member-1"}]})
    store._get_optional = AsyncMock(return_value={"email": "Member@Example.Test"})

    with pytest.raises(WorkspaceError) as raised:
        await store.invite_member("owner-1", "workspace-1", "member@example.test")

    assert raised.value.code == "workspace_member_exists"


@pytest.mark.asyncio
async def test_owner_can_invite_registered_user_who_is_not_member(monkeypatch):
    store = _store(monkeypatch)
    store._require_owner = AsyncMock()
    store._list = AsyncMock(return_value={"rows": []})
    store._get_optional = AsyncMock(return_value=None)
    store._create = AsyncMock()

    result = await store.invite_member(
        "owner-1", "workspace-1", "Future.Member@Example.test"
    )

    assert result["email"] == "future.member@example.test"
    assert result["status"] == "pending"
    assert store._create.await_args.args[2]["email"] == "future.member@example.test"


@pytest.mark.asyncio
async def test_owner_can_invite_email_without_an_account(monkeypatch):
    store = _store(monkeypatch)
    store._require_owner = AsyncMock()
    store._list = AsyncMock(return_value={"rows": []})
    store._get_optional = AsyncMock(return_value=None)
    store._create = AsyncMock()

    result = await store.invite_member("owner-1", "workspace-1", "new@example.test")

    assert result["email"] == "new@example.test"
    store._create.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_can_cancel_pending_invitation(monkeypatch):
    store = _store(monkeypatch)
    store._require_owner = AsyncMock()
    invitation_id = store.invitation_id("workspace-1", "member@example.test")
    store._get_optional = AsyncMock(
        return_value={
            "$id": invitation_id,
            "status": "pending",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
    )
    store._patch = AsyncMock()

    result = await store.cancel_invitation(
        "owner-1", "workspace-1", "member@example.test"
    )

    assert result["status"] == "cancelled"
    store._patch.assert_awaited_once_with(
        store._invitations_url, invitation_id, {"status": "cancelled"}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "rejected", "expired"])
async def test_terminal_invitation_can_be_reinvited(monkeypatch, status):
    store = _store(monkeypatch)
    store._require_owner = AsyncMock()
    store._list = AsyncMock(return_value={"rows": []})
    invitation_id = store.invitation_id("workspace-1", "member@example.test")
    store._get_optional = AsyncMock(
        return_value={
            "$id": invitation_id,
            "status": status,
            "expires_at": "2000-01-01T00:00:00+00:00",
        }
    )
    store._patch = AsyncMock()

    result = await store.invite_member("owner-1", "workspace-1", "member@example.test")

    assert result["status"] == "pending"
    assert store._patch.await_args.args[1] == invitation_id
    assert store._patch.await_args.args[2]["status"] == "pending"


@pytest.mark.asyncio
async def test_expired_pending_invitation_is_hidden_from_recipient_list(monkeypatch):
    store = _store(monkeypatch)
    invitation_id = store.invitation_id("workspace-1", "member@example.test")
    store._list = AsyncMock(
        return_value={
            "rows": [
                {
                    "$id": invitation_id,
                    "workspace_id": "workspace-1",
                    "email": "member@example.test",
                    "status": "pending",
                    "expires_at": "2000-01-01T00:00:00+00:00",
                }
            ]
        }
    )
    store._patch = AsyncMock()

    result = await store.list_my_invitations("member@example.test", page_size=25)

    assert result["invitations"] == []
    store._patch.assert_awaited_once_with(
        store._invitations_url, invitation_id, {"status": "expired"}
    )


@pytest.mark.asyncio
async def test_expired_invitation_cannot_be_accepted(monkeypatch):
    store = _store(monkeypatch)
    invitation = {
        "$id": store.invitation_id("workspace-1", "member@example.test"),
        "status": "pending",
        "email": "member@example.test",
        "expires_at": "2000-01-01T00:00:00+00:00",
    }
    store._create_transaction = AsyncMock(return_value="tx-1")
    store._get_in_transaction = AsyncMock(return_value=invitation)
    store._stage_patch = AsyncMock()
    store._commit = AsyncMock(return_value=True)

    with pytest.raises(WorkspaceError) as raised:
        await store.accept_invitation("member-1", "member@example.test", "workspace-1")

    assert raised.value.code == "invitation_expired"
    store._stage_patch.assert_awaited_once()


@pytest.mark.asyncio
async def test_accept_stops_at_member_capacity_and_rolls_back(monkeypatch):
    store = _store(monkeypatch)
    invitation = {
        "$id": store.invitation_id("workspace-1", "member@example.test"),
        "status": "pending",
        "email": "member@example.test",
        "expires_at": "2999-01-01T00:00:00+00:00",
    }
    store._create_transaction = AsyncMock(return_value="tx-1")
    store._get_in_transaction = AsyncMock(
        side_effect=[invitation, None, {"$id": "workspace-1"}]
    )
    store._increment_member_count = AsyncMock(return_value="capacity")
    store._rollback = AsyncMock()
    store._stage_create = AsyncMock()
    store._stage_patch = AsyncMock()

    with pytest.raises(WorkspaceError) as raised:
        await store.accept_invitation("member-1", "member@example.test", "workspace-1")

    assert raised.value.code == "workspace_member_limit"
    store._stage_create.assert_not_awaited()
    assert store._rollback.await_count >= 1


@pytest.mark.asyncio
async def test_repeated_accept_is_idempotent_without_duplicate_membership(monkeypatch):
    store = _store(monkeypatch)
    store._create_transaction = AsyncMock(return_value="tx-1")
    store._get_in_transaction = AsyncMock(
        return_value={"status": "accepted", "email": "member@example.test"}
    )
    store._rollback = AsyncMock()
    store._stage_create = AsyncMock()
    store._increment_member_count = AsyncMock()

    result = await store.accept_invitation(
        "member-1", "member@example.test", "workspace-1"
    )

    assert result == {"workspace_id": "workspace-1", "status": "accepted"}
    store._stage_create.assert_not_awaited()
    store._increment_member_count.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_cannot_accept_another_email_invitation(monkeypatch):
    store = _store(monkeypatch)
    store._create_transaction = AsyncMock(return_value="tx-1")
    store._get_in_transaction = AsyncMock(return_value=None)
    store._rollback = AsyncMock()

    with pytest.raises(WorkspaceError) as raised:
        await store.accept_invitation(
            "other-user-1", "other@example.test", "workspace-1"
        )

    assert raised.value.code == "invitation_not_found"


@pytest.mark.asyncio
async def test_cancelled_invitation_cannot_be_accepted(monkeypatch):
    store = _store(monkeypatch)
    store._create_transaction = AsyncMock(return_value="tx-1")
    store._get_in_transaction = AsyncMock(
        return_value={"status": "cancelled", "email": "member@example.test"}
    )
    store._rollback = AsyncMock()

    with pytest.raises(WorkspaceError) as raised:
        await store.accept_invitation("member-1", "member@example.test", "workspace-1")

    assert raised.value.code == "invitation_not_pending"


@pytest.mark.asyncio
async def test_member_count_increment_is_bounded_inside_transaction(monkeypatch):
    store = _store(monkeypatch)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.patch = AsyncMock(return_value=MagicMock(status_code=200))

    with patch("src.workspaces.httpx.AsyncClient", return_value=client):
        result = await store._increment_member_count("workspace-1", "tx-1")

    assert result == "ok"
    assert client.patch.await_args.kwargs["json"] == {
        "value": 1,
        "max": 10,
        "transactionId": "tx-1",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    ["row_max_exceeded", "attribute_limit_exceeded", "column_limit_exceeded"],
)
async def test_appwrite_bound_errors_are_member_limit(monkeypatch, error_type):
    store = _store(monkeypatch)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    response = MagicMock(status_code=400)
    response.json.return_value = {"type": error_type}
    client.patch = AsyncMock(return_value=response)

    with patch("src.workspaces.httpx.AsyncClient", return_value=client):
        result = await store._increment_member_count("workspace-1", "tx-1")

    assert result == "capacity"


def test_invalid_workspace_id_is_a_controlled_request_error():
    with pytest.raises(SecurityValidationError) as raised:
        validate_request_payload(
            {"action": "workspace_accept_invitation", "workspaceId": "bad/id"}
        )

    assert raised.value.code == "invalid_request"


@pytest.mark.asyncio
async def test_member_cannot_list_members_via_public_dispatcher(monkeypatch):
    store = _store(monkeypatch)
    store._get_optional = AsyncMock(return_value={"role": "member", "status": "active"})
    with (
        patch(
            "src.main.get_authenticated_account",
            new=AsyncMock(
                return_value={
                    "$id": "member-1",
                    "email": "member@example.test",
                    "emailVerification": True,
                }
            ),
        ),
        patch("src.main.ensure_user_profile", new=AsyncMock(return_value={})),
        patch("src.main.AppwriteWorkspaceStore", return_value=store),
    ):
        with pytest.raises(SecurityValidationError) as raised:
            await _execute_request(
                {"action": "workspace_list_members", "workspaceId": "workspace-1"},
                "runtime-key",
                "member-1",
                "runtime-jwt",
            )

    assert raised.value.code == "workspace_owner_required"


@pytest.mark.asyncio
async def test_invitation_list_uses_cursor_pagination(monkeypatch):
    store = _store(monkeypatch)
    store._require_owner = AsyncMock()
    store._list = AsyncMock(
        return_value={
            "rows": [
                {
                    "$id": "invite-1",
                    "workspace_id": "workspace-1",
                    "email": "member@example.test",
                    "status": "pending",
                    "expires_at": "2999-01-01T00:00:00+00:00",
                }
            ]
        }
    )

    result = await store.list_invitations(
        "owner-1", "workspace-1", page_size=1, cursor="cursor-1"
    )

    assert result["next_cursor"] == "invite-1"
    assert _query_objects(store._list.await_args.args[1]) == [
        {"method": "equal", "attribute": "workspace_id", "values": ["workspace-1"]},
        {"method": "limit", "values": [1]},
        {"method": "orderDesc", "attribute": "$sequence"},
        {"method": "cursorAfter", "values": ["cursor-1"]},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["owner", "member"])
async def test_workspace_history_sequence_query_keeps_role_scope_and_cursor(monkeypatch, role):
    store = _store(monkeypatch)
    store.resolve_workspace_access = AsyncMock(
        return_value=WorkspaceAccess(
            workspace_id="workspace-a",
            actor_user_id="member-a",
            role=role,
            workspace={},
        )
    )
    store._list = AsyncMock(return_value={"rows": []})

    await store.list_history("member-a", "workspace-a", page_size=1, cursor_after="check-1")

    expected = [
        {"method": "equal", "attribute": "workspace_id", "values": ["workspace-a"]},
    ]
    if role == "member":
        expected.append(
            {"method": "equal", "attribute": "user_id", "values": ["member-a"]}
        )
    expected.extend(
        [
            {"method": "limit", "values": [1]},
            {"method": "orderDesc", "attribute": "$sequence"},
            {"method": "cursorAfter", "values": ["check-1"]},
        ]
    )
    assert _query_objects(store._list.await_args.args[1]) == expected


@pytest.mark.asyncio
async def test_workspace_and_invitation_lists_use_sequence_cursor_queries(monkeypatch):
    store = _store(monkeypatch)
    store._list = AsyncMock(return_value={"rows": []})
    store._require_owner = AsyncMock()

    await store.get_my_workspaces("member-1", page_size=1, cursor="membership-1")
    membership_queries = store._list.await_args.args[1]
    await store.list_my_invitations("member@example.test", page_size=1, cursor="invite-1")
    my_invitation_queries = store._list.await_args.args[1]
    await store.list_invitations("owner-1", "workspace-1", page_size=1, cursor="invite-2")
    owner_invitation_queries = store._list.await_args.args[1]

    assert _query_objects(membership_queries) == [
        {"method": "equal", "attribute": "user_id", "values": ["member-1"]},
        {"method": "equal", "attribute": "status", "values": ["active"]},
        {"method": "limit", "values": [1]},
        {"method": "orderDesc", "attribute": "$sequence"},
        {"method": "cursorAfter", "values": ["membership-1"]},
    ]
    assert _query_objects(my_invitation_queries) == [
        {"method": "equal", "attribute": "email", "values": ["member@example.test"]},
        {"method": "equal", "attribute": "status", "values": ["pending"]},
        {"method": "limit", "values": [1]},
        {"method": "orderDesc", "attribute": "$sequence"},
        {"method": "cursorAfter", "values": ["invite-1"]},
    ]
    assert _query_objects(owner_invitation_queries) == [
        {"method": "equal", "attribute": "workspace_id", "values": ["workspace-1"]},
        {"method": "limit", "values": [1]},
        {"method": "orderDesc", "attribute": "$sequence"},
        {"method": "cursorAfter", "values": ["invite-2"]},
    ]


@pytest.mark.asyncio
async def test_workspace_member_list_uses_multiple_indexed_json_queries(monkeypatch):
    store = _store(monkeypatch)
    store._require_owner = AsyncMock()
    store._list = AsyncMock(return_value={"rows": []})

    await store.list_members("owner-1", "workspace-1")

    assert _query_objects(store._list.await_args.args[1]) == [
        {"method": "equal", "attribute": "workspace_id", "values": ["workspace-1"]},
        {"method": "equal", "attribute": "status", "values": ["active"]},
        {"method": "limit", "values": [11]},
        {"method": "orderAsc", "attribute": "$createdAt"},
    ]


@pytest.mark.asyncio
async def test_accept_stages_membership_and_invitation_in_one_transaction(monkeypatch):
    store = _store(monkeypatch)
    invitation = {
        "$id": store.invitation_id("workspace-1", "member@example.test"),
        "status": "pending",
        "email": "member@example.test",
        "expires_at": "2999-01-01T00:00:00+00:00",
    }
    store._create_transaction = AsyncMock(return_value="tx-1")
    store._get_in_transaction = AsyncMock(
        side_effect=[invitation, None, {"$id": "workspace-1"}]
    )
    store._increment_member_count = AsyncMock(return_value="ok")
    store._stage_create = AsyncMock()
    store._stage_patch = AsyncMock()
    store._commit = AsyncMock(return_value=True)

    result = await store.accept_invitation(
        "member-1", "member@example.test", "workspace-1"
    )

    assert result == {"workspace_id": "workspace-1", "status": "accepted"}
    assert store._stage_create.await_args.args[3] == "tx-1"
    assert store._stage_patch.await_args.args[3] == "tx-1"
    assert store._stage_create.await_args.args[2]["user_id"] == "member-1"
