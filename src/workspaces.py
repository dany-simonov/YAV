"""Server-side workspace, membership and invitation persistence for Appwrite."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any, Mapping

import httpx

from src.appwrite_store import check_history_response
from src.subscriptions import (
    SubscriptionValidationError,
    normalize_provider_quota_overrides,
    validate_user_id,
)
from src.validation import EmailCanonicalizationError, canonicalize_email

_MAX_MEMBERS = 10
_INVITATION_TTL_DAYS = 7
_MAX_PAGE_SIZE = 100
_SAFE_LOG_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SENSITIVE_LOG_VALUE = re.compile(
    r"(?i)(?:authorization|x-appwrite-key|cookie|token|jwt|password|api[_-]?key)\s*[:=]\s*(?:bearer\s+)?\S+"
)
_EMAIL_IN_MESSAGE = re.compile(r"(?i)\b[^\s@]+@[^\s@]+\b")


class WorkspaceError(RuntimeError):
    """A typed workspace error that never includes Appwrite response data."""

    def __init__(self, code: str, detail: str, status_code: int = 400) -> None:
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class WorkspaceAccess:
    """Trusted workspace context resolved for one authenticated actor."""

    workspace_id: str
    actor_user_id: str
    role: str
    workspace: Mapping[str, Any]


def _workspace_id(value: Any) -> str:
    try:
        return validate_user_id(value)
    except SubscriptionValidationError as exc:
        raise WorkspaceError(
            "invalid_workspace_id", "Некорректный идентификатор workspace."
        ) from exc


def _canonical_email(value: Any) -> str:
    try:
        return canonicalize_email(value)
    except EmailCanonicalizationError as exc:
        raise WorkspaceError(
            "invalid_invitation_email", "Некорректный email приглашения."
        ) from exc


def _tablesdb_query(
    method: str,
    *,
    attribute: str | None = None,
    values: list[Any] | None = None,
) -> str:
    """Serialize a TablesDB query exactly as the current Appwrite SDK does."""
    query: dict[str, Any] = {"method": method}
    if attribute is not None:
        query["attribute"] = attribute
    if values is not None:
        query["values"] = values
    return json.dumps(query, ensure_ascii=False, separators=(",", ":"))


def _row_id(namespace: str, *parts: str) -> str:
    material = ":".join((namespace, *parts))
    return hashlib.sha256(material.encode()).hexdigest()[:36]


class AppwriteWorkspaceStore:
    """Function-key-only workspace operations with transaction-based acceptance."""

    def __init__(
        self,
        api_key: str,
        *,
        diagnostic_log: Any = None,
        diagnostic_error_log: Any = None,
        action: str = "workspace_unknown",
        correlation_id: str = "",
    ) -> None:
        if not isinstance(api_key, str) or not api_key:
            raise WorkspaceError(
                "workspace_unavailable", "Workspace временно недоступен.", 503
            )
        self.endpoint = os.getenv("APPWRITE_FUNCTION_API_ENDPOINT", "").rstrip("/")
        self.project = os.getenv("APPWRITE_FUNCTION_PROJECT_ID", "")
        self.database = os.getenv("APPWRITE_DATABASE_ID", "yav")
        self.workspaces_table = os.getenv("APPWRITE_WORKSPACES_TABLE_ID", "workspaces")
        self.memberships_table = os.getenv(
            "APPWRITE_WORKSPACE_MEMBERSHIPS_TABLE_ID", "workspace_memberships"
        )
        self.invitations_table = os.getenv(
            "APPWRITE_WORKSPACE_INVITATIONS_TABLE_ID", "workspace_invitations"
        )
        self.checks_table = os.getenv("APPWRITE_CHECKS_TABLE_ID", "checks")
        self.users_table = os.getenv("APPWRITE_USERS_TABLE_ID", "users")
        self.api_key = api_key
        self._diagnostic_log = diagnostic_log
        self._diagnostic_error_log = diagnostic_error_log
        self._action = (
            action
            if isinstance(action, str) and re.fullmatch(r"workspace_[a-z_]{1,96}", action)
            else "workspace_unknown"
        )
        self._correlation_id = (
            correlation_id
            if isinstance(correlation_id, str)
            and re.fullmatch(r"[a-f0-9]{32}", correlation_id)
            else uuid.uuid4().hex
        )
        if not all(
            (
                self.endpoint,
                self.project,
                self.database,
                self.workspaces_table,
                self.memberships_table,
                self.invitations_table,
                self.checks_table,
                self.users_table,
            )
        ):
            raise WorkspaceError(
                "workspace_unavailable", "Workspace временно недоступен.", 503
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Appwrite-Project": self.project, "X-Appwrite-Key": self.api_key}

    @staticmethod
    def _unavailable() -> WorkspaceError:
        """Return the single public contract for Appwrite infrastructure faults."""
        return WorkspaceError(
            "workspace_unavailable", "Workspace временно недоступен.", 503
        )

    def _observe(self, message: str, *, error: bool = False) -> None:
        sink = self._diagnostic_error_log if error else self._diagnostic_log
        if not callable(sink):
            return
        try:
            sink(message)
        except Exception:
            pass

    @staticmethod
    def _safe_token(value: Any, default: str = "unknown") -> str:
        return value if isinstance(value, str) and _SAFE_LOG_TOKEN.fullmatch(value) else default

    def _resource(self, url: str) -> str:
        for table, base_url in (
            ("workspaces", self._workspaces_url),
            ("workspace_memberships", self._memberships_url),
            ("workspace_invitations", self._invitations_url),
            ("checks", self._checks_url),
            ("users", self._users_url),
        ):
            if url.startswith(base_url):
                return f"{table}.rows"
        if url.startswith(f"{self.endpoint}/tablesdb/transactions"):
            return "tablesdb.transactions"
        return "unknown"

    @staticmethod
    def _query_type(query: str) -> str:
        try:
            body = json.loads(query)
        except (TypeError, ValueError):
            return "invalid"
        return (
            body["method"]
            if isinstance(body, Mapping)
            and isinstance(body.get("method"), str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", body["method"])
            else "invalid"
        )

    @staticmethod
    def _sanitize_message(value: Any, sensitive_values: tuple[str, ...] = ()) -> str:
        if not isinstance(value, str):
            return "omitted"
        sanitized = value.replace("\r", " ").replace("\n", " ")
        for sensitive_value in sensitive_values:
            if sensitive_value:
                sanitized = sanitized.replace(sensitive_value, "<redacted>")
        sanitized = _SENSITIVE_LOG_VALUE.sub("<redacted-sensitive>", sanitized)
        sanitized = _EMAIL_IN_MESSAGE.sub("<redacted-email>", sanitized)
        return sanitized[:300]

    def _appwrite_error_metadata(self, response: Any) -> tuple[str, str, str]:
        error_type, error_code, message = "unknown", "unknown", "omitted"
        try:
            body = response.json()
        except (AttributeError, TypeError, ValueError):
            return error_type, error_code, message
        if not isinstance(body, Mapping):
            return error_type, error_code, message
        error_type = self._safe_token(body.get("type"))
        code = body.get("code")
        error_code = str(code) if isinstance(code, (int, str)) else "unknown"
        message = self._sanitize_message(body.get("message"), (self.api_key,))
        return error_type, error_code, message

    @staticmethod
    def _category(status_code: int | None, error_type: str) -> str:
        if status_code in {401, 403}:
            return "permission_or_scope_denied"
        return {
            "table_not_found": "table_not_found",
            "attribute_not_found": "missing_column",
            "column_not_found": "missing_column",
            "index_not_found": "missing_index",
            "general_query_invalid": "invalid_query",
            "query_invalid": "invalid_query",
        }.get(error_type, "unknown")

    def _observe_error(
        self,
        *,
        operation: str,
        category: str,
        exception_class: str,
        status_code: int | None = None,
        appwrite_type: str = "unknown",
        appwrite_code: str = "unknown",
        appwrite_message: str = "omitted",
    ) -> None:
        self._observe(
            "workspace_appwrite_error "
            f"correlation_id={self._correlation_id} action={self._action} "
            f"operation={operation} category={category} "
            f"upstream_status={status_code if status_code is not None else 'none'} "
            f"appwrite_type={appwrite_type} appwrite_code={appwrite_code} "
            f"appwrite_message={appwrite_message} exception_class={exception_class}",
            error=True,
        )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        query_types: tuple[str, ...] = (),
        **kwargs: Any,
    ) -> Any:
        """Execute one TablesDB request without exposing transport details."""
        safe_method = method.upper() if method.lower() in {"get", "post", "patch"} else "unknown"
        self._observe(
            "workspace_appwrite_request "
            f"correlation_id={self._correlation_id} action={self._action} "
            f"operation={operation} method={safe_method} resource={self._resource(url)} "
            f"query_types={','.join(query_types) or 'none'}"
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await getattr(client, method)(url, **kwargs)
        except httpx.TimeoutException as exc:
            self._observe_error(
                operation=operation,
                category="timeout",
                exception_class=type(exc).__name__,
            )
            raise self._unavailable() from exc
        except httpx.TransportError as exc:
            self._observe_error(
                operation=operation,
                category="transport_error",
                exception_class=type(exc).__name__,
            )
            raise self._unavailable() from exc
        except httpx.HTTPError as exc:
            self._observe_error(
                operation=operation,
                category="transport_error",
                exception_class=type(exc).__name__,
            )
            raise self._unavailable() from exc
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int) and status_code >= 400:
            error_type, error_code, error_message = self._appwrite_error_metadata(response)
            self._observe_error(
                operation=operation,
                category=self._category(status_code, error_type),
                exception_class="HTTPResponse",
                status_code=status_code,
                appwrite_type=error_type,
                appwrite_code=error_code,
                appwrite_message=error_message,
            )
        return response

    def _json_object(self, response: Any, *, operation: str) -> Mapping[str, Any]:
        """Decode a required Appwrite object response as infrastructure data."""
        try:
            body = response.json()
        except (AttributeError, TypeError, ValueError) as exc:
            self._observe_error(
                operation=operation,
                category="malformed_response",
                exception_class=type(exc).__name__,
                status_code=getattr(response, "status_code", None),
            )
            raise self._unavailable() from exc
        if not isinstance(body, Mapping):
            self._observe_error(
                operation=operation,
                category="malformed_response",
                exception_class="WorkspaceError",
                status_code=getattr(response, "status_code", None),
            )
            raise self._unavailable()
        return body

    def _rows_url(self, table: str) -> str:
        return f"{self.endpoint}/tablesdb/{self.database}/tables/{table}/rows"

    @property
    def _workspaces_url(self) -> str:
        return self._rows_url(self.workspaces_table)

    @property
    def _memberships_url(self) -> str:
        return self._rows_url(self.memberships_table)

    @property
    def _invitations_url(self) -> str:
        return self._rows_url(self.invitations_table)

    @property
    def _checks_url(self) -> str:
        return self._rows_url(self.checks_table)

    @property
    def _users_url(self) -> str:
        return self._rows_url(self.users_table)

    @staticmethod
    def membership_id(workspace_id: str, user_id: str) -> str:
        return _row_id("workspace-member", workspace_id, user_id)

    @staticmethod
    def invitation_id(workspace_id: str, email: str) -> str:
        return _row_id("workspace-invitation", workspace_id, email)

    async def create_workspace(self, owner_user_id: str, name: str) -> dict[str, Any]:
        owner_user_id = validate_user_id(owner_user_id)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise WorkspaceError(
                "invalid_workspace_name", "Некорректное название workspace."
            )
        workspace_id = uuid.uuid4().hex
        membership_id = self.membership_id(workspace_id, owner_user_id)
        await self._run_transaction(
            [
                (
                    "create",
                    self._workspaces_url,
                    workspace_id,
                    {
                        "owner_user_id": owner_user_id,
                        "name": name.strip(),
                        "member_count": 0,
                    },
                ),
                (
                    "create",
                    self._memberships_url,
                    membership_id,
                    {
                        "workspace_id": workspace_id,
                        "user_id": owner_user_id,
                        "role": "owner",
                        "status": "active",
                    },
                ),
            ]
        )
        return {
            "workspace_id": workspace_id,
            "name": name.strip(),
            "role": "owner",
            "member_count": 0,
        }

    async def get_my_workspaces(
        self, user_id: str, *, page_size: int = 25, cursor: str | None = None
    ) -> dict[str, Any]:
        user_id = validate_user_id(user_id)
        page_size, cursor = self._page(page_size, cursor)
        queries = [
            _tablesdb_query("equal", attribute="user_id", values=[user_id]),
            _tablesdb_query("equal", attribute="status", values=["active"]),
            _tablesdb_query("limit", values=[page_size]),
            _tablesdb_query("orderDesc", attribute="$sequence"),
        ]
        if cursor:
            queries.append(_tablesdb_query("cursorAfter", values=[cursor]))
        memberships = await self._list(
            self._memberships_url,
            queries,
        )
        rows = memberships.get("rows")
        if not isinstance(rows, list):
            raise WorkspaceError(
                "workspace_unavailable", "Workspace временно недоступен.", 503
            )
        results = []
        for membership in rows:
            if not isinstance(membership, dict):
                continue
            workspace_id = membership.get("workspace_id")
            if not isinstance(workspace_id, str):
                continue
            workspace = await self._get_optional(self._workspaces_url, workspace_id)
            if workspace is not None:
                results.append(
                    self._workspace_response(workspace, membership.get("role"))
                )
        return {
            "workspaces": results,
            "next_cursor": self._next_cursor(rows, page_size),
            "page_size": page_size,
        }

    async def list_members(
        self, actor_user_id: str, workspace_id: str
    ) -> dict[str, Any]:
        await self._require_owner(actor_user_id, workspace_id)
        body = await self._list(
            self._memberships_url,
            [
                _tablesdb_query("equal", attribute="workspace_id", values=[workspace_id]),
                _tablesdb_query("equal", attribute="status", values=["active"]),
                _tablesdb_query("limit", values=[_MAX_MEMBERS + 1]),
                _tablesdb_query("orderAsc", attribute="$createdAt"),
            ],
        )
        rows = body.get("rows")
        if not isinstance(rows, list):
            raise WorkspaceError(
                "workspace_unavailable", "Workspace временно недоступен.", 503
            )
        return {
            "members": [
                self._membership_response(row) for row in rows if isinstance(row, dict)
            ]
        }

    async def list_invitations(
        self,
        owner_user_id: str,
        workspace_id: str,
        *,
        page_size: int = 25,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        await self._require_owner(owner_user_id, workspace_id)
        page_size, cursor = self._page(page_size, cursor)
        queries = [
            _tablesdb_query("equal", attribute="workspace_id", values=[workspace_id]),
            _tablesdb_query("limit", values=[page_size]),
            _tablesdb_query("orderDesc", attribute="$sequence"),
        ]
        if cursor:
            queries.append(_tablesdb_query("cursorAfter", values=[cursor]))
        body = await self._list(
            self._invitations_url,
            queries,
        )
        rows = await self._expire_rows(body)
        return {
            "invitations": [self._invitation_response(row) for row in rows],
            "next_cursor": self._next_cursor(rows, page_size),
            "page_size": page_size,
        }

    async def list_history(
        self,
        actor_user_id: str,
        workspace_id: str,
        *,
        page_size: int = 25,
        cursor_after: str | None = None,
    ) -> dict[str, Any]:
        """Read workspace checks under current membership, never row ACLs."""
        access = await self.resolve_workspace_access(actor_user_id, workspace_id)
        page_size, cursor_after = self._page(page_size, cursor_after)
        queries = [
            _tablesdb_query(
                "equal", attribute="workspace_id", values=[access.workspace_id]
            ),
        ]
        if access.role == "member":
            queries.append(
                _tablesdb_query(
                    "equal", attribute="user_id", values=[access.actor_user_id]
                )
            )
        queries.extend(
            [
                _tablesdb_query("limit", values=[page_size]),
                _tablesdb_query("orderDesc", attribute="$sequence"),
            ]
        )
        if cursor_after:
            queries.append(_tablesdb_query("cursorAfter", values=[cursor_after]))
        body = await self._list(self._checks_url, queries)
        rows = body.get("rows")
        if not isinstance(rows, list):
            raise WorkspaceError(
                "workspace_unavailable", "Workspace временно недоступен.", 503
            )
        return {
            "checks": [
                self._history_response(row)
                for row in rows
                if isinstance(row, Mapping)
            ],
            "next_cursor": self._next_cursor(rows, page_size),
            "page_size": page_size,
        }

    async def list_my_invitations(
        self, user_email: str, *, page_size: int = 25, cursor: str | None = None
    ) -> dict[str, Any]:
        email = _canonical_email(user_email)
        page_size, cursor = self._page(page_size, cursor)
        queries = [
            _tablesdb_query("equal", attribute="email", values=[email]),
            _tablesdb_query("equal", attribute="status", values=["pending"]),
            _tablesdb_query("limit", values=[page_size]),
            _tablesdb_query("orderDesc", attribute="$sequence"),
        ]
        if cursor:
            queries.append(_tablesdb_query("cursorAfter", values=[cursor]))
        body = await self._list(
            self._invitations_url,
            queries,
        )
        rows = await self._expire_rows(body)
        pending = [row for row in rows if row.get("status") == "pending"]
        return {
            "invitations": [self._invitation_response(row) for row in pending],
            "next_cursor": self._next_cursor(rows, page_size),
            "page_size": page_size,
        }

    async def invite_member(
        self, owner_user_id: str, workspace_id: str, email: str
    ) -> dict[str, Any]:
        owner_user_id = validate_user_id(owner_user_id)
        workspace_id = _workspace_id(workspace_id)
        email = _canonical_email(email)
        await self._require_owner(owner_user_id, workspace_id)
        if await self._workspace_has_member_email(workspace_id, email):
            raise WorkspaceError(
                "workspace_member_exists", "Пользователь уже состоит в workspace.", 409
            )
        invitation_id = self.invitation_id(workspace_id, email)
        existing = await self._get_optional(self._invitations_url, invitation_id)
        if existing is not None:
            existing = await self._expire_invitation(existing)
        if existing is not None and existing.get("status") == "pending":
            raise WorkspaceError(
                "invitation_already_exists", "Приглашение уже существует.", 409
            )
        now = datetime.now(timezone.utc)
        data = {
            "workspace_id": workspace_id,
            "email": email,
            "inviter_user_id": owner_user_id,
            "status": "pending",
            "expires_at": (now + timedelta(days=_INVITATION_TTL_DAYS)).isoformat(),
        }
        if existing is None:
            await self._create(self._invitations_url, invitation_id, data)
        else:
            await self._patch(self._invitations_url, invitation_id, data)
        return self._invitation_response({"$id": invitation_id, **data})

    async def cancel_invitation(
        self, owner_user_id: str, workspace_id: str, email: str
    ) -> dict[str, Any]:
        owner_user_id = validate_user_id(owner_user_id)
        workspace_id = _workspace_id(workspace_id)
        email = _canonical_email(email)
        await self._require_owner(owner_user_id, workspace_id)
        invitation_id = self.invitation_id(workspace_id, email)
        invitation = await self._get_optional(self._invitations_url, invitation_id)
        if invitation is None:
            raise WorkspaceError("invitation_not_found", "Приглашение не найдено.", 404)
        invitation = await self._expire_invitation(invitation)
        if invitation.get("status") != "pending":
            raise WorkspaceError(
                "invitation_not_pending", "Приглашение уже не ожидает ответа.", 409
            )
        await self._patch(self._invitations_url, invitation_id, {"status": "cancelled"})
        return {"workspace_id": workspace_id, "email": email, "status": "cancelled"}

    async def set_provider_quota_overrides(
        self, owner_user_id: str, workspace_id: str, overrides: Any
    ) -> dict[str, Any]:
        """Set one monthly per-provider limit, shared by all team members."""
        workspace_id = _workspace_id(workspace_id)
        await self._require_owner(owner_user_id, workspace_id)
        try:
            normalized = normalize_provider_quota_overrides(overrides)
        except SubscriptionValidationError as exc:
            raise WorkspaceError("invalid_workspace_quota", "Некорректные лимиты нейросетей.") from exc
        await self._patch(
            self._workspaces_url,
            workspace_id,
            {"provider_quota_overrides": json.dumps(normalized, separators=(",", ":"), sort_keys=True)},
        )
        return {"workspace_id": workspace_id, "provider_quota_overrides": normalized}

    async def reject_invitation(
        self, user_id: str, user_email: str, workspace_id: str
    ) -> dict[str, Any]:
        validate_user_id(user_id)
        workspace_id = _workspace_id(workspace_id)
        invitation = await self._get_invitation_for_email(user_email, workspace_id)
        invitation = await self._expire_invitation(invitation)
        if invitation.get("status") != "pending":
            raise WorkspaceError(
                "invitation_not_pending", "Приглашение уже не ожидает ответа.", 409
            )
        await self._patch(
            self._invitations_url, str(invitation["$id"]), {"status": "rejected"}
        )
        return {"workspace_id": workspace_id, "status": "rejected"}

    async def accept_invitation(
        self, user_id: str, user_email: str, workspace_id: str
    ) -> dict[str, Any]:
        user_id = validate_user_id(user_id)
        workspace_id = _workspace_id(workspace_id)
        email = _canonical_email(user_email)
        invitation_id = self.invitation_id(workspace_id, email)
        membership_id = self.membership_id(workspace_id, user_id)
        for _attempt in range(3):
            transaction_id = await self._create_transaction()
            transaction_committed = False
            try:
                invitation = await self._get_in_transaction(
                    self._invitations_url, invitation_id, transaction_id
                )
                if invitation is None:
                    await self._rollback(transaction_id)
                    raise WorkspaceError(
                        "invitation_not_found", "Приглашение не найдено.", 404
                    )
                if invitation.get("status") == "accepted":
                    await self._rollback(transaction_id)
                    return {"workspace_id": workspace_id, "status": "accepted"}
                if (
                    invitation.get("status") != "pending"
                    or invitation.get("email") != email
                ):
                    await self._rollback(transaction_id)
                    raise WorkspaceError(
                        "invitation_not_pending",
                        "Приглашение уже не ожидает ответа.",
                        409,
                    )
                if self._is_expired(invitation.get("expires_at")):
                    await self._stage_patch(
                        self._invitations_url,
                        invitation_id,
                        {"status": "expired"},
                        transaction_id,
                    )
                    if await self._commit(transaction_id):
                        transaction_committed = True
                        raise WorkspaceError(
                            "invitation_expired",
                            "Срок действия приглашения истёк.",
                            409,
                        )
                    await self._rollback(transaction_id)
                    continue
                if await self._get_in_transaction(
                    self._memberships_url, membership_id, transaction_id
                ):
                    await self._stage_patch(
                        self._invitations_url,
                        invitation_id,
                        {"status": "accepted"},
                        transaction_id,
                    )
                else:
                    workspace = await self._get_in_transaction(
                        self._workspaces_url, workspace_id, transaction_id
                    )
                    if workspace is None:
                        await self._rollback(transaction_id)
                        raise WorkspaceError(
                            "workspace_not_found", "Workspace не найден.", 404
                        )
                    incremented = await self._increment_member_count(
                        workspace_id, transaction_id
                    )
                    if incremented == "capacity":
                        await self._rollback(transaction_id)
                        raise WorkspaceError(
                            "workspace_member_limit",
                            "Лимит участников workspace исчерпан.",
                            409,
                        )
                    if incremented != "ok":
                        await self._rollback(transaction_id)
                        raise WorkspaceError(
                            "workspace_unavailable",
                            "Workspace временно недоступен.",
                            503,
                        )
                    await self._stage_create(
                        self._memberships_url,
                        membership_id,
                        {
                            "workspace_id": workspace_id,
                            "user_id": user_id,
                            "role": "member",
                            "status": "active",
                        },
                        transaction_id,
                    )
                    await self._stage_patch(
                        self._invitations_url,
                        invitation_id,
                        {"status": "accepted"},
                        transaction_id,
                    )
                if await self._commit(transaction_id):
                    return {"workspace_id": workspace_id, "status": "accepted"}
                await self._rollback(transaction_id)
            except WorkspaceError:
                if not transaction_committed:
                    await self._rollback(transaction_id)
                raise
            except httpx.HTTPError as exc:
                await self._rollback(transaction_id)
                raise WorkspaceError(
                    "workspace_unavailable", "Workspace временно недоступен.", 503
                ) from exc
        raise WorkspaceError(
            "workspace_accept_conflict",
            "Не удалось принять приглашение. Повторите попытку.",
            409,
        )

    async def _require_active_member(
        self, user_id: str, workspace_id: str
    ) -> Mapping[str, Any]:
        membership = await self._get_optional(
            self._memberships_url,
            self.membership_id(_workspace_id(workspace_id), validate_user_id(user_id)),
        )
        if membership is None or membership.get("status") != "active":
            raise WorkspaceError(
                "workspace_access_denied", "Нет доступа к workspace.", 403
            )
        return membership

    async def resolve_workspace_access(
        self, actor_user_id: str, workspace_id: str
    ) -> WorkspaceAccess:
        """Resolve an active member's immutable workspace context."""
        actor_user_id = validate_user_id(actor_user_id)
        workspace_id = _workspace_id(workspace_id)
        membership = await self._require_active_member(actor_user_id, workspace_id)
        workspace = await self._get_optional(self._workspaces_url, workspace_id)
        if workspace is None:
            raise WorkspaceError("workspace_not_found", "Workspace не найден.", 404)
        role = membership.get("role")
        if role not in {"owner", "member"}:
            raise WorkspaceError(
                "workspace_access_denied", "Нет доступа к workspace.", 403
            )
        return WorkspaceAccess(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            role=role,
            workspace=MappingProxyType(dict(workspace)),
        )

    async def _require_owner(
        self, user_id: str, workspace_id: str
    ) -> Mapping[str, Any]:
        user_id = validate_user_id(user_id)
        workspace_id = _workspace_id(workspace_id)
        membership = await self._require_active_member(user_id, workspace_id)
        workspace = await self._get_optional(self._workspaces_url, workspace_id)
        if (
            workspace is None
            or membership.get("role") != "owner"
            or workspace.get("owner_user_id") != user_id
        ):
            raise WorkspaceError(
                "workspace_owner_required", "Требуются права владельца workspace.", 403
            )
        return membership

    async def _get_invitation_for_email(
        self, user_email: str, workspace_id: str
    ) -> Mapping[str, Any]:
        email = _canonical_email(user_email)
        invitation = await self._get_optional(
            self._invitations_url,
            self.invitation_id(_workspace_id(workspace_id), email),
        )
        if invitation is None or invitation.get("email") != email:
            raise WorkspaceError("invitation_not_found", "Приглашение не найдено.", 404)
        return invitation

    async def _workspace_has_member_email(self, workspace_id: str, email: str) -> bool:
        """Compare canonical profile emails across the bounded active membership set."""
        body = await self._list(
            self._memberships_url,
            [
                _tablesdb_query("equal", attribute="workspace_id", values=[workspace_id]),
                _tablesdb_query("equal", attribute="status", values=["active"]),
                _tablesdb_query("limit", values=[_MAX_MEMBERS + 1]),
            ],
        )
        rows = body.get("rows")
        if not isinstance(rows, list):
            raise WorkspaceError(
                "workspace_unavailable", "Workspace временно недоступен.", 503
            )
        for membership in rows:
            if not isinstance(membership, Mapping):
                continue
            member_user_id = membership.get("user_id")
            if not isinstance(member_user_id, str):
                continue
            profile = await self._get_optional(self._users_url, member_user_id)
            if profile is None:
                continue
            try:
                if _canonical_email(profile.get("email")) == email:
                    return True
            except WorkspaceError:
                continue
        return False

    async def _expire_invitation(
        self, invitation: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if invitation.get("status") != "pending" or not self._is_expired(
            invitation.get("expires_at")
        ):
            return invitation
        invitation_id = invitation.get("$id")
        if not isinstance(invitation_id, str) or not invitation_id:
            raise WorkspaceError(
                "workspace_unavailable", "Workspace временно недоступен.", 503
            )
        await self._patch(self._invitations_url, invitation_id, {"status": "expired"})
        return {**dict(invitation), "status": "expired"}

    async def _expire_rows(self, body: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        rows = body.get("rows")
        if not isinstance(rows, list):
            raise WorkspaceError(
                "workspace_unavailable", "Workspace временно недоступен.", 503
            )
        return [
            await self._expire_invitation(row)
            for row in rows
            if isinstance(row, Mapping)
        ]

    @staticmethod
    def _page(page_size: int, cursor: str | None) -> tuple[int, str | None]:
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= _MAX_PAGE_SIZE
        ):
            raise WorkspaceError("invalid_page", "Некорректная пагинация.")
        if cursor is not None:
            try:
                cursor = validate_user_id(cursor)
            except SubscriptionValidationError as exc:
                raise WorkspaceError("invalid_page", "Некорректная пагинация.") from exc
        return page_size, cursor

    @staticmethod
    def _next_cursor(rows: list[Any], page_size: int) -> str | None:
        if len(rows) != page_size or not rows:
            return None
        row_id = rows[-1].get("$id") if isinstance(rows[-1], Mapping) else None
        return row_id if isinstance(row_id, str) else None

    async def _run_transaction(
        self, operations: list[tuple[str, str, str, Mapping[str, Any]]]
    ) -> None:
        transaction_id = await self._create_transaction()
        try:
            for action, url, row_id, data in operations:
                if action != "create":
                    raise WorkspaceError(
                        "workspace_unavailable", "Workspace временно недоступен.", 503
                    )
                await self._stage_create(url, row_id, data, transaction_id)
            if not await self._commit(transaction_id):
                raise WorkspaceError(
                    "workspace_create_conflict",
                    "Не удалось создать workspace. Повторите попытку.",
                    409,
                )
        except Exception:
            await self._rollback(transaction_id)
            raise

    async def _create_transaction(self) -> str:
        response = await self._request(
            "post",
            f"{self.endpoint}/tablesdb/transactions",
            operation="workspace.transaction.create",
            headers=self._headers,
            json={"ttl": 30},
        )
        if response.status_code not in (200, 201):
            raise self._unavailable()
        body = self._json_object(response, operation="workspace.transaction.create")
        transaction_id = body.get("$id") if isinstance(body, dict) else None
        if not isinstance(transaction_id, str) or not transaction_id:
            raise self._unavailable()
        return transaction_id

    async def _commit(self, transaction_id: str) -> bool:
        response = await self._request(
            "patch",
            f"{self.endpoint}/tablesdb/transactions/{transaction_id}",
            operation="workspace.transaction.commit",
            headers=self._headers,
            json={"commit": True},
        )
        if response.status_code == 200:
            return True
        if response.status_code == 409:
            return False
        raise self._unavailable()

    async def _rollback(self, transaction_id: str) -> None:
        try:
            await self._request(
                "patch",
                f"{self.endpoint}/tablesdb/transactions/{transaction_id}",
                operation="workspace.transaction.rollback",
                headers=self._headers,
                json={"rollback": True},
            )
        except WorkspaceError:
            pass

    async def _increment_member_count(
        self, workspace_id: str, transaction_id: str
    ) -> str:
        response = await self._request(
            "patch",
            f"{self._workspaces_url}/{workspace_id}/member_count/increment",
            operation="workspace.member_count.increment",
            headers=self._headers,
            json={"value": 1, "max": _MAX_MEMBERS, "transactionId": transaction_id},
        )
        if response.status_code == 200:
            return "ok"
        if response.status_code == 409:
            return "capacity"
        if response.status_code == 400:
            error_type = self._json_object(
                response, operation="workspace.member_count.increment"
            ).get("type")
            if error_type in {
                "row_max_exceeded",
                "attribute_limit_exceeded",
                "column_limit_exceeded",
            }:
                return "capacity"
        return "error"

    async def _get_in_transaction(
        self, url: str, row_id: str, transaction_id: str
    ) -> Mapping[str, Any] | None:
        response = await self._request(
            "get",
            f"{url}/{row_id}",
            operation="workspace.rows.get",
            query_types=("transactionId",),
            headers=self._headers,
            params={"transactionId": transaction_id},
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise self._unavailable()
        return self._json_object(response, operation="workspace.rows.get")

    async def _stage_create(
        self, url: str, row_id: str, data: Mapping[str, Any], transaction_id: str
    ) -> None:
        response = await self._request(
            "post",
            url,
            operation="workspace.rows.stage_create",
            headers=self._headers,
            json={
                "rowId": row_id,
                "data": dict(data),
                "permissions": [],
                "transactionId": transaction_id,
            },
        )
        if response.status_code not in (200, 201):
            raise self._unavailable()

    async def _stage_patch(
        self, url: str, row_id: str, data: Mapping[str, Any], transaction_id: str
    ) -> None:
        response = await self._request(
            "patch",
            f"{url}/{row_id}",
            operation="workspace.rows.stage_patch",
            headers=self._headers,
            json={"data": dict(data), "transactionId": transaction_id},
        )
        if response.status_code != 200:
            raise self._unavailable()

    async def _create(self, url: str, row_id: str, data: Mapping[str, Any]) -> None:
        response = await self._request(
            "post",
            url,
            operation="workspace.rows.create",
            headers=self._headers,
            json={"rowId": row_id, "data": dict(data), "permissions": []},
        )
        if response.status_code == 409:
            raise WorkspaceError(
                "invitation_already_exists", "Приглашение уже существует.", 409
            )
        if response.status_code not in (200, 201):
            raise self._unavailable()

    async def _patch(self, url: str, row_id: str, data: Mapping[str, Any]) -> None:
        response = await self._request(
            "patch",
            f"{url}/{row_id}",
            operation="workspace.rows.patch",
            headers=self._headers,
            json={"data": dict(data)},
        )
        if response.status_code != 200:
            raise self._unavailable()

    async def _get_optional(self, url: str, row_id: str) -> Mapping[str, Any] | None:
        response = await self._request(
            "get",
            f"{url}/{row_id}",
            operation="workspace.rows.get",
            headers=self._headers,
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise self._unavailable()
        return self._json_object(response, operation="workspace.rows.get")

    async def _list(self, url: str, queries: list[str]) -> Mapping[str, Any]:
        response = await self._request(
            "get",
            url,
            operation="workspace.rows.list",
            query_types=tuple(self._query_type(query) for query in queries),
            headers=self._headers,
            params=[
                (f"queries[{index}]", query)
                for index, query in enumerate(queries)
            ],
        )
        if response.status_code != 200:
            raise self._unavailable()
        return self._json_object(response, operation="workspace.rows.list")

    @staticmethod
    def _is_expired(value: Any) -> bool:
        try:
            expires_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return True
        return expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc)

    @staticmethod
    def _workspace_response(row: Mapping[str, Any], role: Any) -> dict[str, Any]:
        raw_provider_overrides = row.get("provider_quota_overrides")
        try:
            provider_overrides = normalize_provider_quota_overrides(
                json.loads(raw_provider_overrides)
                if isinstance(raw_provider_overrides, str) and raw_provider_overrides else {}
            )
        except (TypeError, ValueError, json.JSONDecodeError, SubscriptionValidationError):
            provider_overrides = {}
        return {
            "workspace_id": str(row.get("$id") or ""),
            "name": str(row.get("name") or ""),
            "owner_user_id": str(row.get("owner_user_id") or ""),
            "member_count": row.get("member_count")
            if isinstance(row.get("member_count"), int)
            else 0,
            "role": role if role in {"owner", "member"} else "member",
            "provider_quota_overrides": provider_overrides,
        }

    @staticmethod
    def _membership_response(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "user_id": str(row.get("user_id") or ""),
            "role": str(row.get("role") or ""),
            "status": str(row.get("status") or ""),
            "created_at": str(row.get("created_at") or row.get("$createdAt") or ""),
        }

    @staticmethod
    def _invitation_response(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "invitation_id": str(row.get("$id") or ""),
            "workspace_id": str(row.get("workspace_id") or ""),
            "email": str(row.get("email") or ""),
            "inviter_user_id": str(row.get("inviter_user_id") or ""),
            "status": str(row.get("status") or ""),
            "created_at": str(row.get("$createdAt") or ""),
            "expires_at": str(row.get("expires_at") or ""),
        }

    @staticmethod
    def _history_response(row: Mapping[str, Any]) -> dict[str, Any]:
        """Return only the stored check fields needed by the Function client."""
        return check_history_response(row, include_workspace_id=True)

    def _invitation_rows(self, body: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows = body.get("rows")
        if not isinstance(rows, list):
            raise WorkspaceError(
                "workspace_unavailable", "Workspace временно недоступен.", 503
            )
        return [self._invitation_response(row) for row in rows if isinstance(row, dict)]
