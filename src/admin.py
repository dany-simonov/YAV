"""Admin-only Appwrite services for subscription operations and telemetry."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import quote

import httpx

from core.config import settings
from src.rate_limit import _window
from src.subscriptions import (
    QUOTA_KEYS,
    AppwriteSubscriptionStore,
    EffectiveQuotaPolicy,
    SubscriptionPersistenceError,
    SubscriptionValidationError,
    effective_quota_policy,
    overrides_from_profile,
    subscription_from_profile,
    validate_user_id,
)

logger = logging.getLogger(__name__)
_MAX_PAGE_SIZE = 100
_MAX_AUDIT_VALUE_BYTES = 1024
_MAX_IDEMPOTENCY_KEY_LENGTH = 64


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


def _query(function: str, *arguments: Any) -> str:
    return f"{function}({','.join(json.dumps(argument, ensure_ascii=False, separators=(',', ':')) for argument in arguments)})"


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

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key)
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
        queries = [_query("limit", page_size), _query("orderDesc", "$sequence")]
        if cursor:
            queries.append(_query("cursorAfter", cursor))
        if search:
            if not isinstance(search, str) or len(search) > 320:
                raise SubscriptionValidationError("invalid user search")
            if "@" in search:
                queries.append(_query("equal", "email", [search.strip().lower()]))
            else:
                queries.append(_query("equal", "$id", [validate_user_id(search)]))
        response = await self._list_rows(self._rows_url, queries)
        rows = response.get("rows")
        if not isinstance(rows, list):
            raise AdminPersistenceError("user list decode failed")
        summaries = [self._user_summary(row) for row in rows if isinstance(row, dict)]
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
        queries = [_query("limit", page_size), _query("orderDesc", "$sequence")]
        if cursor:
            queries.append(_query("cursorAfter", cursor))
        if target_user_id:
            queries.append(_query("equal", "target_user_id", [target_user_id]))
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

    async def _active_reservations(self, user_id: str) -> dict[str, Any]:
        response = await self._list_rows(
            self._reservations_url,
            [
                _query("equal", "user_id", [user_id]),
                _query("equal", "state", ["reserved"]),
                _query("limit", 50),
                _query("orderDesc", "$createdAt"),
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
                "aiornot_words_daily",
                "global_aiornot_words_daily",
                "day",
                settings.global_aiornot_words_daily,
            ),
            (
                "aiornot_words_monthly",
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

    async def _list_rows(self, url: str, queries: list[str]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    url,
                    headers=self._headers,
                    params=[("queries[]", query) for query in queries],
                )
        except httpx.HTTPError as exc:
            raise AdminPersistenceError("Appwrite list failed") from exc
        if response.status_code != 200:
            raise AdminPersistenceError("Appwrite list failed")
        try:
            body = response.json()
        except (TypeError, ValueError) as exc:
            raise AdminPersistenceError("Appwrite list decode failed") from exc
        if not isinstance(body, dict):
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
        return {
            "user_id": user_id,
            "email": self._safe_string(profile.get("email"), 320),
            "display_name": self._safe_string(profile.get("name"), 128),
            "email_verified": profile.get("email_verified") is True,
            "subscription": subscription_from_profile(profile),
            "has_quota_overrides": bool(overrides),
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
