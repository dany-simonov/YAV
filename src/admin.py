"""Admin-only Appwrite services for subscription operations and telemetry."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import quote

import httpx

from core.config import settings
from src.rate_limit import _window
from src.provider_external_usage import ProviderExternalUsageService
from src.subscriptions import (
    QUOTA_KEYS,
    AppwriteSubscriptionStore,
    EffectiveQuotaPolicy,
    SubscriptionPersistenceError,
    SubscriptionValidationError,
    effective_quota_policy,
    overrides_from_profile,
    provider_overrides_from_profile,
    subscription_from_profile,
    validate_user_id,
)

logger = logging.getLogger(__name__)
_MAX_PAGE_SIZE = 100
_MAX_AUDIT_VALUE_BYTES = 1024
_MAX_IDEMPOTENCY_KEY_LENGTH = 64
_SAFE_APPWRITE_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
_SAFE_APPWRITE_MESSAGE = re.compile(
    r"(?i)(?:authorization|x-appwrite-key|cookie|token|jwt|password|api[_-]?key)\s*[:=]\s*(?:bearer\s+)?\S+"
)
_EMAIL_IN_MESSAGE = re.compile(r"(?i)\b[^\s@]+@[^\s@]+\b")


class AdminPersistenceError(RuntimeError):
    """A bounded server-side Appwrite administration failure."""


class AdminUserNotFoundError(AdminPersistenceError):
    """The requested profile does not exist."""


class AdminAuditPersistenceError(AdminPersistenceError):
    """A mutation may have completed but its audit operation is unresolved."""


class QuotaResetConflictError(AdminPersistenceError):
    """An atomic generation reset could not be completed safely."""


class AdminOperationPendingError(AdminPersistenceError):
    """A retry found an unresolved idempotent administrative operation."""


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


def _bounded_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    if len(encoded.encode("utf-8")) > _MAX_AUDIT_VALUE_BYTES:
        raise AdminPersistenceError("admin audit value is too large")
    return encoded


def _operation_id(
    actor_user_id: str, action: str, target_user_id: str, key: str
) -> str:
    if (
        not isinstance(key, str)
        or not 16 <= len(key) <= _MAX_IDEMPOTENCY_KEY_LENGTH
        or not all(char.isascii() and (char.isalnum() or char in "._-") for char in key)
    ):
        raise SubscriptionValidationError("invalid idempotency key")
    material = f"admin-operation:{actor_user_id}:{action}:{target_user_id}:{key}"
    return hashlib.sha256(material.encode()).hexdigest()[:36]


class AppwriteAdminStore(AppwriteSubscriptionStore):
    """Server-key-only administrative reads and mutations.

    User/profile updates remain in ``AppwriteSubscriptionStore``. This class
    adds bounded listing, telemetry, audit rows, and generation-based reset;
    it does not create a parallel quota implementation.
    """

    def __init__(
        self,
        api_key: str,
        *,
        diagnostic_log: Any = None,
        diagnostic_error_log: Any = None,
        correlation_id: str = "",
    ) -> None:
        super().__init__(api_key)
        self._diagnostic_log = diagnostic_log
        self._diagnostic_error_log = diagnostic_error_log
        self._correlation_id = (
            correlation_id
            if isinstance(correlation_id, str)
            and re.fullmatch(r"[a-f0-9]{32}", correlation_id)
            else uuid.uuid4().hex
        )
        self.checks_table = os.getenv("APPWRITE_CHECKS_TABLE_ID", "checks")
        self.rate_limits_table = os.getenv(
            "APPWRITE_RATE_LIMITS_TABLE_ID", "rate_limits"
        )
        self.reservations_table = os.getenv(
            "APPWRITE_QUOTA_RESERVATIONS_TABLE_ID", "quota_reservations"
        )
        self.audit_table = os.getenv("APPWRITE_ADMIN_AUDIT_TABLE_ID", "admin_audit_log")
        if (
            not self.rate_limits_table
            or not self.reservations_table
            or not self.audit_table
            or not self.checks_table
        ):
            raise AdminPersistenceError("missing Appwrite administration configuration")

    def _table_rows_url(self, table_id: str) -> str:
        return f"{self.endpoint}/tablesdb/{self.database}/tables/{table_id}/rows"

    @property
    def _rate_limits_url(self) -> str:
        return self._table_rows_url(self.rate_limits_table)

    @property
    def _reservations_url(self) -> str:
        return self._table_rows_url(self.reservations_table)

    @property
    def _audit_url(self) -> str:
        return self._table_rows_url(self.audit_table)

    @property
    def _checks_url(self) -> str:
        return self._table_rows_url(self.checks_table)

    async def get_profile(self, user_id: str) -> dict[str, Any]:
        try:
            return await super().get_profile(user_id)
        except SubscriptionPersistenceError as exc:
            # The base store intentionally treats all read failures equally for
            # analysis. Admin APIs may safely expose the typed 404 distinction.
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(
                        f"{self._rows_url}/{quote(validate_user_id(user_id), safe='')}",
                        headers=self._headers,
                    )
            except httpx.HTTPError:
                raise exc
            if response.status_code == 404:
                raise AdminUserNotFoundError("user profile was not found") from exc
            raise exc

    async def list_users(
        self, *, page_size: int, cursor: str | None = None, search: str | None = None
    ) -> dict[str, Any]:
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= _MAX_PAGE_SIZE
        ):
            raise SubscriptionValidationError("invalid page size")
        if cursor is not None:
            cursor = validate_user_id(cursor)
        queries = [
            _tablesdb_query("limit", values=[page_size]),
            _tablesdb_query("orderDesc", attribute="$sequence"),
        ]
        query_types = ["limit", "orderDesc"]
        if cursor:
            queries.append(_tablesdb_query("cursorAfter", values=[cursor]))
            query_types.append("cursorAfter")
        if search:
            if not isinstance(search, str) or len(search) > 320:
                raise SubscriptionValidationError("invalid user search")
            if "@" in search:
                queries.append(
                    _tablesdb_query(
                        "equal",
                        attribute="email",
                        values=[search.strip().lower()],
                    )
                )
                query_types.append("equal:email")
            else:
                queries.append(
                    _tablesdb_query(
                        "equal",
                        attribute="$id",
                        values=[validate_user_id(search)],
                    )
                )
                query_types.append("equal:$id")
        response = await self._list_rows(
            self._rows_url,
            queries,
            operation="admin_list_users.rows_list",
            resource="users.rows",
            query_types=tuple(query_types),
        )
        rows = response.get("rows")
        if not isinstance(rows, list):
            self._observe_error(
                operation="admin_list_users.decode",
                category="malformed_response",
                exception_class="AdminPersistenceError",
            )
            raise AdminPersistenceError("user list decode failed")
        try:
            summaries = [self._user_summary(row) for row in rows if isinstance(row, dict)]
        except (SubscriptionPersistenceError, SubscriptionValidationError, ValueError) as exc:
            self._observe_error(
                operation="admin_list_users.row_decode",
                category="malformed_response",
                exception_class=type(exc).__name__,
            )
            # Preserve the pre-existing client error mapping; this branch adds
            # observability only and must not alter the action contract.
            raise
        next_cursor = summaries[-1]["user_id"] if len(summaries) == page_size else None
        return {"users": summaries, "next_cursor": next_cursor, "page_size": page_size}

    async def get_user_details(self, user_id: str) -> dict[str, Any]:
        user_id = validate_user_id(user_id)
        profile = await self.get_profile(user_id)
        policy = await self.effective_policy_for_profile(profile, user_id)
        defaults = effective_quota_policy(policy.subscription)
        usage = await self._user_usage(user_id, policy)
        return {
            **self._policy_response(user_id, policy),
            "user": {
                "user_id": user_id,
                "email": self._safe_string(profile.get("email"), 320),
                "display_name": self._safe_string(profile.get("name"), 128),
                "email_verified": profile.get("email_verified") is True,
            },
            "subscription_details": {
                "plan": policy.subscription,
                "defaults": self._limits_response(defaults),
                "overrides": dict(policy.overrides),
                "effective": self._limits_response(policy),
            },
            "usage": usage,
        }

    async def change_subscription(
        self, actor_user_id: str, target_user_id: str, subscription: Any
    ) -> EffectiveQuotaPolicy:
        target_user_id = validate_user_id(target_user_id)
        old = await self.get_effective_policy(target_user_id)
        updated = await self.update_subscription(target_user_id, subscription)
        await self._audit_or_raise(
            actor_user_id,
            "subscription_changed",
            target_user_id,
            {"subscription": old.subscription},
            {"subscription": updated.subscription},
        )
        return updated

    async def change_quota_overrides(
        self, actor_user_id: str, target_user_id: str, overrides: Any
    ) -> EffectiveQuotaPolicy:
        target_user_id = validate_user_id(target_user_id)
        old = await self.get_effective_policy(target_user_id)
        updated = await self.update_quota_overrides(target_user_id, overrides)
        await self._audit_or_raise(
            actor_user_id,
            "quota_overrides_set",
            target_user_id,
            {"overrides": dict(old.overrides)},
            {"overrides": dict(updated.overrides)},
        )
        return updated

    async def change_provider_quota_overrides(
        self, actor_user_id: str, target_user_id: str, overrides: Any
    ) -> EffectiveQuotaPolicy:
        target_user_id = validate_user_id(target_user_id)
        profile = await self.get_profile(target_user_id)
        old = provider_overrides_from_profile(profile)
        updated = await self.update_provider_quota_overrides(target_user_id, overrides)
        await self._audit_or_raise(
            actor_user_id,
            "provider_quota_overrides_set",
            target_user_id,
            {"provider_overrides": old},
            {"provider_overrides": dict(updated.provider_overrides)},
        )
        return updated

    async def remove_override(
        self, actor_user_id: str, target_user_id: str, quota_key: Any
    ) -> EffectiveQuotaPolicy:
        target_user_id = validate_user_id(target_user_id)
        old = await self.get_effective_policy(target_user_id)
        updated = await self.remove_quota_override(target_user_id, quota_key)
        await self._audit_or_raise(
            actor_user_id,
            "quota_override_removed",
            target_user_id,
            {"overrides": dict(old.overrides)},
            {"overrides": dict(updated.overrides)},
        )
        return updated

    async def reset_overrides(
        self, actor_user_id: str, target_user_id: str
    ) -> EffectiveQuotaPolicy:
        target_user_id = validate_user_id(target_user_id)
        old = await self.get_effective_policy(target_user_id)
        updated = await self.reset_quota_overrides(target_user_id)
        await self._audit_or_raise(
            actor_user_id,
            "quota_overrides_reset",
            target_user_id,
            {"overrides": dict(old.overrides)},
            {"overrides": {}},
        )
        return updated

    async def reset_user_quota_usage(
        self,
        actor_user_id: str,
        target_user_id: str,
        quota_key: Any,
        *,
        idempotency_key: str | None = None,
    ) -> EffectiveQuotaPolicy:
        target_user_id = validate_user_id(target_user_id)
        if quota_key not in QUOTA_KEYS:
            raise SubscriptionValidationError("invalid quota key")
        # Confirm target existence before creating a durable reset generation.
        profile = await self.get_profile(target_user_id)
        await self._ensure_generation_marker(target_user_id, profile)
        operation_id = await self._begin_reset_operation(
            actor_user_id,
            "quota_usage_reset",
            target_user_id,
            idempotency_key,
            scope=str(quota_key),
        )
        if operation_id is None:
            return await self.get_effective_policy(target_user_id)
        old_generation, new_generation = await self._increment_generation(
            target_user_id, str(quota_key)
        )
        old_value = {"quota_key": quota_key, "generation": old_generation}
        new_value = {"quota_key": quota_key, "generation": new_generation}
        if operation_id:
            await self._complete_reset_operation(operation_id, old_value, new_value)
        else:
            await self._audit_or_raise(
                actor_user_id, "quota_usage_reset", target_user_id, old_value, new_value
            )
        return await self.get_effective_policy(target_user_id)

    async def reset_all_user_usage(
        self,
        actor_user_id: str,
        target_user_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> EffectiveQuotaPolicy:
        target_user_id = validate_user_id(target_user_id)
        profile = await self.get_profile(target_user_id)
        await self._ensure_generation_marker(target_user_id, profile)
        operation_id = await self._begin_reset_operation(
            actor_user_id,
            "all_quota_usage_reset",
            target_user_id,
            idempotency_key,
            scope="all",
        )
        if operation_id is None:
            return await self.get_effective_policy(target_user_id)
        if operation_id:
            previous, current = await self._increment_all_generations_atomically(
                target_user_id
            )
        else:
            previous, current = {}, {}
            for quota_key in sorted(QUOTA_KEYS):
                (
                    previous[quota_key],
                    current[quota_key],
                ) = await self._increment_generation(target_user_id, quota_key)
        old_value = {"generations": previous}
        new_value = {"generations": current}
        if operation_id:
            await self._complete_reset_operation(operation_id, old_value, new_value)
        else:
            await self._audit_or_raise(
                actor_user_id,
                "all_quota_usage_reset",
                target_user_id,
                old_value,
                new_value,
            )
        return await self.get_effective_policy(target_user_id)

    async def _ensure_generation_marker(
        self, user_id: str, profile: Mapping[str, Any]
    ) -> None:
        """Migrate an old profile before a reset makes generations authoritative."""
        if "quota_usage_generations" not in profile:
            await self._patch(user_id, {"quota_usage_generations": "{}"})

    async def _begin_reset_operation(
        self,
        actor_user_id: str,
        action: str,
        target_user_id: str,
        idempotency_key: str | None,
        *,
        scope: str,
    ) -> str | None:
        """Persist intent before a non-idempotent generation increment.

        ``None`` means this exact logical request has already completed. A
        pending record intentionally blocks retry: the caller must inspect the
        operation instead of risking a second reset after an uncertain write.
        An empty ID is retained for direct service callers from older code;
        the Function contract always supplies a validated key.
        """
        if idempotency_key is None:
            return ""
        actor_user_id = validate_user_id(actor_user_id)
        target_user_id = validate_user_id(target_user_id)
        event_id = _operation_id(
            actor_user_id, f"{action}:{scope}", target_user_id, idempotency_key
        )
        existing = await self._get_optional_row(self._audit_url, event_id)
        if existing is not None:
            if (
                existing.get("actor_user_id") != actor_user_id
                or existing.get("action") != action
                or existing.get("target_user_id") != target_user_id
                or existing.get("operation_key") != event_id
            ):
                raise AdminPersistenceError("invalid stored administrative operation")
            if existing.get("state") == "completed":
                return None
            raise AdminOperationPendingError("administrative operation is pending")
        try:
            await self._create_audit_event(
                actor_user_id,
                action,
                target_user_id,
                {},
                {},
                event_id=event_id,
                operation_key=event_id,
                state="pending",
            )
        except AdminPersistenceError:
            # A timed-out create can have reached Appwrite. Read the stable
            # row ID before deciding whether it is safe to perform a reset.
            existing = await self._get_optional_row(self._audit_url, event_id)
            if existing is not None:
                raise AdminOperationPendingError("administrative operation is pending")
            raise
        return event_id

    async def _complete_reset_operation(
        self,
        event_id: str,
        old_value: Mapping[str, Any],
        new_value: Mapping[str, Any],
    ) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.patch(
                    f"{self._audit_url}/{event_id}",
                    headers=self._headers,
                    json={
                        "data": {
                            "old_value": _bounded_json(old_value),
                            "new_value": _bounded_json(new_value),
                            "state": "completed",
                        }
                    },
                )
        except httpx.HTTPError as exc:
            raise AdminAuditPersistenceError(
                "admin reset audit finalize failed"
            ) from exc
        if response.status_code != 200:
            raise AdminAuditPersistenceError("admin reset audit finalize failed")

    async def list_audit_events(
        self,
        *,
        page_size: int,
        cursor: str | None = None,
        target_user_id: str | None = None,
    ) -> dict[str, Any]:
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= _MAX_PAGE_SIZE
        ):
            raise SubscriptionValidationError("invalid page size")
        if cursor is not None:
            cursor = validate_user_id(cursor)
        if target_user_id is not None:
            target_user_id = validate_user_id(target_user_id)
        queries = [
            _tablesdb_query("limit", values=[page_size]),
            _tablesdb_query("orderDesc", attribute="$sequence"),
        ]
        if cursor:
            queries.append(_tablesdb_query("cursorAfter", values=[cursor]))
        if target_user_id:
            queries.append(
                _tablesdb_query(
                    "equal", attribute="target_user_id", values=[target_user_id]
                )
            )
        response = await self._list_rows(self._audit_url, queries)
        rows = response.get("rows")
        if not isinstance(rows, list):
            raise AdminPersistenceError("audit list decode failed")
        events = [self._audit_event(row) for row in rows if isinstance(row, dict)]
        next_cursor = events[-1]["id"] if len(events) == page_size else None
        return {"events": events, "next_cursor": next_cursor, "page_size": page_size}

    async def _user_usage(
        self, user_id: str, policy: EffectiveQuotaPolicy
    ) -> dict[str, Any]:
        quotas: dict[str, Any] = {}
        for quota_key, quota in policy.limits.items():
            generation = policy.generation(quota_key)
            subject = user_id if generation == 0 else f"{user_id}:g{generation}"
            dimension = (
                "subscription_checks"
                if quota_key == "checks"
                else "subscription_heavy_media"
            )
            dimension = f"{dimension}_{quota.period}"
            window = _window(datetime.now(timezone.utc), quota.period)
            row_id = self._counter_row_id(dimension, subject, window.key)
            counter = await self._get_optional_row(self._rate_limits_url, row_id)
            used = 0
            reset_at = window.end.isoformat()
            active_window = window.key
            if counter is not None:
                count = counter.get("count")
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise AdminPersistenceError("quota counter decode failed")
                used = count
                if isinstance(counter.get("window_start"), str):
                    active_window = counter["window_start"]
                if isinstance(counter.get("window_end"), str):
                    reset_at = counter["window_end"]
            quotas[quota_key] = {
                "limit": quota.limit,
                "used": used,
                # Admission reservations do not identify a single quota key;
                # assigning them here would create false user attribution.
                "reserved": None,
                "remaining": max(0, quota.limit - used),
                "window": active_window,
                "reset_at": reset_at,
                "generation": generation,
            }
        return {
            "user_quotas": quotas,
            "provider_quotas": await self._provider_quota_usage(user_id, policy),
            # A completed check has one persisted provider/model. This is
            # actual historical usage, unlike provider budgets which count
            # internal API units and belong to the whole project.
            "model_checks_month": await self._monthly_model_usage(user_id),
            "active_reservations": await self._active_reservations(user_id),
            # IP counters are keyed with an HMAC of the request IP, and a
            # profile has no authoritative relation to an IP address.
            "ip_limits": {
                "scope": "hashed_ip",
                "attributable_to_target_user": False,
                "current_usage": None,
            },
            "provider_budgets": {
                "scope": "global",
                "attributable_to_target_user": False,
                "quotas": await self._provider_budget_usage(),
            },
        }

    async def _provider_quota_usage(
        self, user_id: str, policy: EffectiveQuotaPolicy
    ) -> dict[str, Any]:
        """Read optional individual provider limits without assigning global use."""
        window = _window(datetime.now(timezone.utc), "month")
        quotas: dict[str, Any] = {}
        for provider, limit in policy.provider_overrides.items():
            dimension = f"user_provider_{provider}_monthly"
            counter = await self._get_optional_row(
                self._rate_limits_url,
                self._counter_row_id(dimension, user_id, window.key),
            )
            used = self._counter_used(counter)
            quotas[provider] = {
                "limit": limit,
                "used": used,
                "remaining": max(0, limit - used),
                "window": counter.get("window_start", window.key) if counter else window.key,
                "reset_at": counter.get("window_end", window.end.isoformat()) if counter else window.end.isoformat(),
            }
        return quotas

    async def _monthly_model_usage(self, user_id: str) -> dict[str, Any]:
        """Aggregate completed checks by the model recorded in history.

        This intentionally reports usage only. There is no per-model quota in
        the subscription policy yet, so attaching a made-up denominator here
        would incorrectly suggest that an individual model is enforced.
        """
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        next_month = (
            month_start.replace(year=month_start.year + 1, month=1)
            if month_start.month == 12
            else month_start.replace(month=month_start.month + 1)
        )
        queries = [
            _tablesdb_query("equal", attribute="user_id", values=[user_id]),
            _tablesdb_query("equal", attribute="status", values=["completed"]),
            _tablesdb_query(
                "greaterThanEqual",
                attribute="$createdAt",
                values=[month_start.isoformat()],
            ),
            _tablesdb_query("limit", values=[100]),
            _tablesdb_query("orderDesc", attribute="$sequence"),
        ]
        counts: dict[tuple[str, str], int] = {}
        cursor: str | None = None
        total = 0
        # A hard ceiling prevents one profile with pathological history from
        # making an administrative request unbounded. The response marks it.
        truncated = False
        for _ in range(10):
            page_queries = [*queries]
            if cursor:
                page_queries.append(_tablesdb_query("cursorAfter", values=[cursor]))
            response = await self._list_rows(self._checks_url, page_queries)
            rows = response.get("rows")
            if not isinstance(rows, list):
                raise AdminPersistenceError("model usage list decode failed")
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                provider = self._safe_string(row.get("provider"), 64) or "unknown"
                model = self._safe_string(row.get("model"), 128) or "unknown"
                counts[(provider, model)] = counts.get((provider, model), 0) + 1
                total += 1
            if len(rows) < 100:
                break
            last = rows[-1]
            next_cursor = last.get("$id") if isinstance(last, Mapping) else None
            if not isinstance(next_cursor, str) or not next_cursor:
                raise AdminPersistenceError("model usage cursor decode failed")
            cursor = next_cursor
        else:
            truncated = True
        models = [
            {"provider": provider, "model": model, "used": used}
            for (provider, model), used in counts.items()
        ]
        models.sort(key=lambda item: (-item["used"], item["provider"], item["model"]))
        return {
            "scope": "completed_checks",
            "period": "month",
            "window": month_start.strftime("%Y-%m"),
            "reset_at": next_month.isoformat(),
            "total": total,
            "truncated": truncated,
            "models": models,
        }

    async def _active_reservations(self, user_id: str) -> dict[str, Any]:
        response = await self._list_rows(
            self._reservations_url,
            [
                _tablesdb_query("equal", attribute="user_id", values=[user_id]),
                _tablesdb_query("equal", attribute="state", values=["reserved"]),
                _tablesdb_query("limit", values=[50]),
                _tablesdb_query("orderDesc", attribute="$createdAt"),
            ],
        )
        rows = response.get("rows")
        if not isinstance(rows, list):
            raise AdminPersistenceError("reservation list decode failed")
        items = [
            {
                "id": self._safe_string(row.get("$id"), 36),
                "quota_dimension": self._safe_string(row.get("quota_dimension"), 32),
                "window_start": self._safe_string(row.get("window_start"), 32),
                "created_at": self._safe_string(row.get("$createdAt"), 64),
            }
            for row in rows
            if isinstance(row, dict)
        ]
        total = response.get("total")
        count = total if isinstance(total, int) and total >= 0 else len(items)
        return {"count": count, "items": items, "truncated": count > len(items)}

    async def _provider_budget_usage(self) -> dict[str, Any]:
        """Read global counters without falsely assigning them to a user."""
        now = datetime.now(timezone.utc)
        definitions = (
            (
                "gemini_operations",
                "global_gemini_daily",
                "day",
                settings.global_gemini_operations_daily,
            ),
            (
                "sightengine_daily",
                "global_sightengine_daily",
                "day",
                settings.global_sightengine_daily,
            ),
            (
                "sightengine_monthly",
                "global_sightengine_monthly",
                "month",
                settings.global_sightengine_monthly,
            ),
            (
                "aiornot_words_legacy_daily",
                "global_aiornot_words_daily",
                "day",
                settings.global_aiornot_words_daily,
            ),
            (
                "aiornot_words_legacy_monthly",
                "global_aiornot_words_monthly",
                "month",
                settings.global_aiornot_words_monthly,
            ),
            (
                "sapling_chars_daily",
                "global_sapling_chars_daily",
                "day",
                settings.global_sapling_chars_daily,
            ),
            (
                "sapling_chars_monthly",
                "global_sapling_chars_monthly",
                "month",
                settings.global_sapling_chars_monthly,
            ),
            (
                "resemble_daily",
                "global_resemble_daily",
                "day",
                settings.global_resemble_daily,
            ),
            (
                "resemble_monthly",
                "global_resemble_monthly",
                "month",
                settings.global_resemble_monthly,
            ),
            (
                "huggingface_daily",
                "global_huggingface_daily",
                "day",
                settings.global_huggingface_daily,
            ),
            (
                "huggingface_monthly",
                "global_huggingface_monthly",
                "month",
                settings.global_huggingface_monthly,
            ),
        )
        quotas: dict[str, Any] = {}
        for key, dimension, period, limit in definitions:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise AdminPersistenceError("provider budget configuration is invalid")
            window = _window(now, period)
            counter = await self._get_optional_row(
                self._rate_limits_url,
                self._counter_row_id(dimension, "global", window.key),
            )
            used = self._counter_used(counter)
            quotas[key] = {
                "dimension": dimension,
                "limit": limit,
                "used": used,
                "remaining": max(0, limit - used),
                "window": counter.get("window_start", window.key)
                if counter
                else window.key,
                "reset_at": counter.get("window_end", window.end.isoformat())
                if counter
                else window.end.isoformat(),
            }
        return quotas

    async def provider_budget_overview(self) -> dict[str, Any]:
        """Return project-wide provider counters without loading any profile.

        ``rate_limits`` is the sole source here.  In particular this avoids
        tying global telemetry to the presence or ordering of user rows.
        """
        now = datetime.now(timezone.utc)
        definitions: tuple[tuple[str, str, str, str, int], ...] = (
            ("gemini", "operations", "day", "global_gemini_daily", settings.global_gemini_operations_daily),
            ("sightengine", "operations", "day", "global_sightengine_daily", settings.global_sightengine_daily),
            ("sightengine", "operations", "month", "global_sightengine_monthly", settings.global_sightengine_monthly),
            ("aiornot", "words", "day", "global_aiornot_text_words_daily", settings.global_aiornot_text_words_daily),
            ("aiornot", "words", "month", "global_aiornot_text_words_monthly", settings.global_aiornot_text_words_monthly),
            ("aiornot", "image_checks", "day", "global_aiornot_image_daily", settings.global_aiornot_image_daily),
            ("aiornot", "image_checks", "month", "global_aiornot_image_monthly", settings.global_aiornot_image_monthly),
            ("sapling", "characters", "day", "global_sapling_chars_daily", settings.global_sapling_chars_daily),
            ("sapling", "characters", "month", "global_sapling_chars_monthly", settings.global_sapling_chars_monthly),
            ("resemble", "operations", "day", "global_resemble_daily", settings.global_resemble_daily),
            ("resemble", "operations", "month", "global_resemble_monthly", settings.global_resemble_monthly),
            ("huggingface", "operations", "day", "global_huggingface_daily", settings.global_huggingface_daily),
            ("huggingface", "operations", "month", "global_huggingface_monthly", settings.global_huggingface_monthly),
        )
        grouped: dict[str, dict[str, Any]] = {}
        for provider, unit, period, dimension, limit in definitions:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise AdminPersistenceError("provider budget configuration is invalid")
            window = _window(now, period)
            counter = await self._get_optional_row(
                self._rate_limits_url,
                self._counter_row_id(dimension, "global", window.key),
            )
            entry = grouped.setdefault(
                provider,
                {
                    "provider": provider,
                    "source": "yav_internal",
                    "unit": "multiple" if provider == "aiornot" else unit,
                    "daily": None,
                    "monthly": None,
                    # AI or Not has two real units.  The legacy word rows are
                    # text-only; no historical row is interpreted as image use.
                    "metrics": [],
                    "legacy_dimensions": ["global_aiornot_words_daily", "global_aiornot_words_monthly"] if provider == "aiornot" else [],
                },
            )
            used = self._counter_used(counter)
            value = {"used": used, "limit": limit, "remaining": max(0, limit - used)}
            metric = next((item for item in entry["metrics"] if item["unit"] == unit), None)
            if metric is None:
                metric = {"unit": unit, "daily": None, "monthly": None}
                entry["metrics"].append(metric)
            metric["daily" if period == "day" else "monthly"] = value
            if provider != "aiornot":
                entry["daily" if period == "day" else "monthly"] = value
        return {"providers": [grouped[key] for key in ("gemini", "sightengine", "aiornot", "sapling", "resemble", "huggingface")]}

    async def provider_external_usage(self) -> dict[str, Any]:
        """Read explicitly supported provider-side telemetry as an admin."""
        return await ProviderExternalUsageService(self.api_key).get_usage()

    async def provider_usage_history(self, provider: str, days: int) -> dict[str, Any]:
        """Return persisted global daily counters for one provider.

        The project records provider-specific units, not a universal token
        value: for example, AIOrNot stores words and Sapling stores chars.
        """
        definitions: Mapping[str, tuple[str, int, str]] = {
            "gemini": ("global_gemini_daily", settings.global_gemini_operations_daily, "операции"),
            "sightengine": ("global_sightengine_daily", settings.global_sightengine_daily, "операции"),
            "aiornot": ("global_aiornot_text_words_daily", settings.global_aiornot_text_words_daily, "слова"),
            "aiornot_image": ("global_aiornot_image_daily", settings.global_aiornot_image_daily, "проверки изображений"),
            "sapling": ("global_sapling_chars_daily", settings.global_sapling_chars_daily, "символы"),
            "resemble": ("global_resemble_daily", settings.global_resemble_daily, "операции"),
            "huggingface": ("global_huggingface_daily", settings.global_huggingface_daily, "операции"),
        }
        if provider not in definitions or isinstance(days, bool) or not 7 <= days <= 90:
            raise SubscriptionValidationError("invalid provider history request")
        dimension, limit, unit = definitions[provider]
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise AdminPersistenceError("provider budget configuration is invalid")
        now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start = now - timedelta(days=days - 1)
        response = await self._list_rows(
            self._rate_limits_url,
            [
                _tablesdb_query("equal", attribute="dimension", values=[dimension]),
                _tablesdb_query(
                    "greaterThanEqual",
                    attribute="window_start",
                    values=[start.strftime("%Y-%m-%d")],
                ),
                _tablesdb_query("limit", values=[100]),
                _tablesdb_query("orderAsc", attribute="window_start"),
            ],
        )
        rows = response.get("rows")
        if not isinstance(rows, list):
            raise AdminPersistenceError("provider history list decode failed")
        counts: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            day = row.get("window_start")
            count = row.get("count")
            if isinstance(day, str) and len(day) == 10 and isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                counts[day] = count
        points = []
        for offset in range(days):
            day = start + timedelta(days=offset)
            key = day.strftime("%Y-%m-%d")
            points.append({"date": key, "used": counts.get(key, 0)})
        return {"provider": provider, "unit": unit, "daily_limit": limit, "points": points}

    async def _increment_generation(
        self, user_id: str, quota_key: str
    ) -> tuple[int, int]:
        row_id = self.generation_row_id(user_id, quota_key)
        row_url = f"{self._generations_url}/{row_id}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                incremented = await client.patch(
                    row_url + "/generation/increment",
                    headers=self._headers,
                    json={"value": 1},
                )
                if incremented.status_code == 404:
                    created = await client.post(
                        self._generations_url,
                        headers=self._headers,
                        json={
                            "rowId": row_id,
                            "data": {
                                "user_id": user_id,
                                "quota_key": quota_key,
                                "generation": 1,
                            },
                            "permissions": [],
                        },
                    )
                    if created.status_code in (200, 201):
                        return 0, 1
                    if created.status_code != 409:
                        raise QuotaResetConflictError(
                            "quota generation creation failed"
                        )
                    incremented = await client.patch(
                        row_url + "/generation/increment",
                        headers=self._headers,
                        json={"value": 1},
                    )
                if incremented.status_code != 200:
                    raise QuotaResetConflictError("quota generation increment failed")
                body = incremented.json()
        except httpx.HTTPError as exc:
            raise QuotaResetConflictError("quota generation transport failed") from exc
        generation = body.get("generation") if isinstance(body, dict) else None
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
        ):
            raise QuotaResetConflictError("quota generation increment decode failed")
        return generation - 1, generation

    async def _increment_all_generations_atomically(
        self, user_id: str
    ) -> tuple[dict[str, int], dict[str, int]]:
        """Advance every resettable quota in one Appwrite transaction.

        A full reset must not expose a half-reset profile if the second key
        fails. Transaction conflicts retry; ambiguous transport/commit errors
        remain pending under the idempotency record and never get replayed.
        """
        transactions_url = f"{self.endpoint}/tablesdb/transactions"
        user_id = validate_user_id(user_id)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                for _attempt in range(3):
                    created = await client.post(
                        transactions_url, headers=self._headers, json={"ttl": 30}
                    )
                    if created.status_code not in (200, 201):
                        raise QuotaResetConflictError(
                            "quota reset transaction create failed"
                        )
                    try:
                        transaction_id = created.json().get("$id")
                    except (AttributeError, TypeError, ValueError) as exc:
                        raise QuotaResetConflictError(
                            "quota reset transaction decode failed"
                        ) from exc
                    if not isinstance(transaction_id, str) or not transaction_id:
                        raise QuotaResetConflictError(
                            "quota reset transaction decode failed"
                        )

                    previous: dict[str, int] = {}
                    current: dict[str, int] = {}
                    conflict = False
                    for quota_key in sorted(QUOTA_KEYS):
                        row_id = self.generation_row_id(user_id, quota_key)
                        row_url = f"{self._generations_url}/{row_id}"
                        row = await client.get(
                            row_url,
                            headers=self._headers,
                            params={"transactionId": transaction_id},
                        )
                        if row.status_code == 404:
                            staged = await client.post(
                                self._generations_url,
                                headers=self._headers,
                                json={
                                    "rowId": row_id,
                                    "data": {
                                        "user_id": user_id,
                                        "quota_key": quota_key,
                                        "generation": 1,
                                    },
                                    "permissions": [],
                                    "transactionId": transaction_id,
                                },
                            )
                            previous[quota_key], current[quota_key] = 0, 1
                        elif row.status_code == 200:
                            body = row.json()
                            if (
                                not isinstance(body, dict)
                                or body.get("user_id") != user_id
                                or body.get("quota_key") != quota_key
                                or isinstance(body.get("generation"), bool)
                                or not isinstance(body.get("generation"), int)
                                or body["generation"] < 0
                            ):
                                await self._rollback_transaction(
                                    client, transactions_url, transaction_id
                                )
                                raise QuotaResetConflictError(
                                    "quota generation decode failed"
                                )
                            previous[quota_key] = body["generation"]
                            staged = await client.patch(
                                row_url + "/generation/increment",
                                headers=self._headers,
                                json={"value": 1, "transactionId": transaction_id},
                            )
                        else:
                            await self._rollback_transaction(
                                client, transactions_url, transaction_id
                            )
                            raise QuotaResetConflictError(
                                "quota generation read failed"
                            )

                        if staged.status_code == 409:
                            conflict = True
                            break
                        if staged.status_code not in (200, 201):
                            await self._rollback_transaction(
                                client, transactions_url, transaction_id
                            )
                            raise QuotaResetConflictError(
                                "quota generation stage failed"
                            )
                        if quota_key not in current:
                            staged_body = staged.json()
                            generation = (
                                staged_body.get("generation")
                                if isinstance(staged_body, dict)
                                else None
                            )
                            if (
                                isinstance(generation, bool)
                                or not isinstance(generation, int)
                                or generation != previous[quota_key] + 1
                            ):
                                await self._rollback_transaction(
                                    client, transactions_url, transaction_id
                                )
                                raise QuotaResetConflictError(
                                    "quota generation increment decode failed"
                                )
                            current[quota_key] = generation

                    if conflict:
                        await self._rollback_transaction(
                            client, transactions_url, transaction_id
                        )
                        continue
                    committed = await client.patch(
                        f"{transactions_url}/{transaction_id}",
                        headers=self._headers,
                        json={"commit": True},
                    )
                    if committed.status_code == 200:
                        return previous, current
                    if committed.status_code == 409:
                        continue
                    raise QuotaResetConflictError(
                        "quota reset transaction commit failed"
                    )
        except httpx.HTTPError as exc:
            raise QuotaResetConflictError(
                "quota reset transaction transport failed"
            ) from exc
        raise QuotaResetConflictError("quota reset transaction conflicted")

    async def _rollback_transaction(
        self, client: Any, transactions_url: str, transaction_id: str
    ) -> None:
        try:
            await client.patch(
                f"{transactions_url}/{transaction_id}",
                headers=self._headers,
                json={"rollback": True},
            )
        except httpx.HTTPError:
            pass

    async def _audit_or_raise(
        self,
        actor_user_id: str,
        action: str,
        target_user_id: str,
        old_value: Mapping[str, Any],
        new_value: Mapping[str, Any],
    ) -> None:
        try:
            await self._create_audit_event(
                actor_user_id, action, target_user_id, old_value, new_value
            )
        except (AdminPersistenceError, SubscriptionPersistenceError) as exc:
            logger.error(
                "admin_audit_persistence_failed action=%s actor_id_length=%s target_id_length=%s",
                action,
                len(actor_user_id),
                len(target_user_id),
            )
            raise AdminAuditPersistenceError(
                "admin mutation audit could not be stored"
            ) from exc

    async def _create_audit_event(
        self,
        actor_user_id: str,
        action: str,
        target_user_id: str,
        old_value: Mapping[str, Any],
        new_value: Mapping[str, Any],
        *,
        event_id: str | None = None,
        operation_key: str | None = None,
        state: str = "completed",
    ) -> None:
        actor_user_id = validate_user_id(actor_user_id)
        target_user_id = validate_user_id(target_user_id)
        if state not in {"pending", "completed"}:
            raise AdminPersistenceError("invalid administrative operation state")
        event_id = event_id or uuid.uuid4().hex
        operation_key = operation_key or event_id
        data = {
            "actor_user_id": actor_user_id,
            "action": action,
            "target_user_id": target_user_id,
            "old_value": _bounded_json(old_value),
            "new_value": _bounded_json(new_value),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "operation_key": operation_key,
            "state": state,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self._audit_url,
                    headers=self._headers,
                    json={
                        "rowId": event_id,
                        "data": data,
                        "permissions": [],
                    },
                )
        except httpx.HTTPError as exc:
            raise AdminPersistenceError("admin audit create failed") from exc
        if response.status_code not in (200, 201):
            raise AdminPersistenceError("admin audit create failed")

    def _observe(self, message: str, *, error: bool = False) -> None:
        callback = self._diagnostic_error_log if error else self._diagnostic_log
        if not callable(callback):
            return
        try:
            callback(message)
        except Exception:
            pass

    @staticmethod
    def _safe_appwrite_value(value: Any) -> str:
        if not isinstance(value, str) or not _SAFE_APPWRITE_TOKEN.fullmatch(value):
            return "unknown"
        return value

    @staticmethod
    def _safe_appwrite_message(value: Any) -> str:
        if not isinstance(value, str):
            return "omitted"
        message = value.replace("\r", " ").replace("\n", " ")
        message = _SAFE_APPWRITE_MESSAGE.sub("<redacted>", message)
        message = _EMAIL_IN_MESSAGE.sub("<redacted-email>", message)
        return message[:160] if message else "omitted"

    @staticmethod
    def _appwrite_error_metadata(response: Any) -> tuple[str, str, str]:
        try:
            body = response.json()
        except (TypeError, ValueError, AttributeError):
            body = None
        if not isinstance(body, Mapping):
            return "unknown", "unknown", "omitted"
        error_type = AppwriteAdminStore._safe_appwrite_value(body.get("type"))
        code = body.get("code")
        error_code = str(code) if isinstance(code, int) and not isinstance(code, bool) else "unknown"
        return error_type, error_code, AppwriteAdminStore._safe_appwrite_message(body.get("message"))

    @staticmethod
    def _appwrite_category(status_code: int | None, error_type: str) -> str:
        if status_code in (401, 403):
            return "permission_or_scope_denied"
        categories = {
            "database_not_found": "database_not_found",
            "table_not_found": "table_not_found",
            "attribute_not_found": "missing_column",
            "column_not_found": "missing_column",
            "index_not_found": "missing_index",
            "general_query_invalid": "invalid_query",
            "query_invalid": "invalid_query",
        }
        return categories.get(error_type, "unknown")

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
            "admin_appwrite_error "
            f"correlation_id={self._correlation_id} operation={operation} "
            f"category={category} status_code={status_code if status_code is not None else 'none'} "
            f"appwrite_type={appwrite_type} appwrite_code={appwrite_code} "
            f"appwrite_message={appwrite_message} exception_class={exception_class}",
            error=True,
        )

    async def _list_rows(
        self,
        url: str,
        queries: list[str],
        *,
        operation: str = "admin.rows_list",
        resource: str = "table.rows",
        query_types: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if operation == "admin_list_users.rows_list":
            self._observe(
                "admin_appwrite_request "
                f"correlation_id={self._correlation_id} operation={operation} "
                f"method=GET resource={resource} query_types={','.join(query_types) or 'none'}"
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    url,
                    headers=self._headers,
                    params=[
                        (f"queries[{index}]", query)
                        for index, query in enumerate(queries)
                    ],
                )
        except httpx.TimeoutException as exc:
            self._observe_error(
                operation=operation,
                category="timeout",
                exception_class=type(exc).__name__,
            )
            raise AdminPersistenceError("Appwrite list failed") from exc
        except httpx.TransportError as exc:
            self._observe_error(
                operation=operation,
                category="transport_error",
                exception_class=type(exc).__name__,
            )
            raise AdminPersistenceError("Appwrite list failed") from exc
        if response.status_code != 200:
            error_type, error_code, error_message = self._appwrite_error_metadata(response)
            self._observe_error(
                operation=operation,
                category=self._appwrite_category(response.status_code, error_type),
                exception_class="AdminPersistenceError",
                status_code=response.status_code,
                appwrite_type=error_type,
                appwrite_code=error_code,
                appwrite_message=error_message,
            )
            raise AdminPersistenceError("Appwrite list failed")
        try:
            body = response.json()
        except (TypeError, ValueError) as exc:
            self._observe_error(
                operation=operation,
                category="malformed_response",
                exception_class=type(exc).__name__,
                status_code=response.status_code,
            )
            raise AdminPersistenceError("Appwrite list decode failed") from exc
        if not isinstance(body, dict):
            self._observe_error(
                operation=operation,
                category="malformed_response",
                exception_class="AdminPersistenceError",
                status_code=response.status_code,
            )
            raise AdminPersistenceError("Appwrite list decode failed")
        return body

    async def _get_optional_row(
        self, rows_url: str, row_id: str
    ) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{rows_url}/{row_id}", headers=self._headers
                )
        except httpx.HTTPError as exc:
            raise AdminPersistenceError("Appwrite counter read failed") from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise AdminPersistenceError("Appwrite counter read failed")
        body = response.json()
        if not isinstance(body, dict):
            raise AdminPersistenceError("Appwrite counter decode failed")
        return body

    @staticmethod
    def _counter_row_id(dimension: str, subject: str, window: str) -> str:
        import hashlib

        return hashlib.sha256(f"{dimension}:{subject}:{window}".encode()).hexdigest()[
            :36
        ]

    @staticmethod
    def _counter_used(counter: Mapping[str, Any] | None) -> int:
        if counter is None:
            return 0
        count = counter.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise AdminPersistenceError("quota counter decode failed")
        return count

    @staticmethod
    def _safe_string(value: Any, limit: int) -> str:
        return value[:limit] if isinstance(value, str) else ""

    def _user_summary(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        user_id = validate_user_id(profile.get("$id"))
        overrides = overrides_from_profile(profile)
        provider_overrides = provider_overrides_from_profile(profile)
        return {
            "user_id": user_id,
            "email": self._safe_string(profile.get("email"), 320),
            "display_name": self._safe_string(profile.get("name"), 128),
            "email_verified": profile.get("email_verified") is True,
            "subscription": subscription_from_profile(profile),
            "has_quota_overrides": bool(overrides),
            "has_provider_quota_overrides": bool(provider_overrides),
            "created_at": self._safe_string(profile.get("$createdAt"), 64),
            "updated_at": self._safe_string(profile.get("$updatedAt"), 64),
        }

    @staticmethod
    def _limits_response(policy: EffectiveQuotaPolicy) -> dict[str, Any]:
        return {
            key: {"period": quota.period, "limit": quota.limit}
            for key, quota in policy.limits.items()
        }

    def _policy_response(
        self, user_id: str, policy: EffectiveQuotaPolicy
    ) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "subscription": policy.subscription,
            "overrides": dict(policy.overrides),
            "effective_limits": self._limits_response(policy),
        }

    def _audit_event(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": self._safe_string(row.get("$id"), 36),
            "actor_user_id": self._safe_string(row.get("actor_user_id"), 36),
            "action": self._safe_string(row.get("action"), 64),
            "target_user_id": self._safe_string(row.get("target_user_id"), 36),
            "old_value": self._safe_string(
                row.get("old_value"), _MAX_AUDIT_VALUE_BYTES
            ),
            "new_value": self._safe_string(
                row.get("new_value"), _MAX_AUDIT_VALUE_BYTES
            ),
            "state": self._safe_string(row.get("state"), 16),
            "created_at": self._safe_string(
                row.get("created_at") or row.get("$createdAt"), 64
            ),
        }
