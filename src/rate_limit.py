"""Cloud-only, fail-closed fixed-window admission using Appwrite TablesDB."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src.validation import SecurityValidationError
from core.config import settings
from core.enums import MediaType


logger = logging.getLogger(__name__)


class RateLimitError(Exception):
    def __init__(self, code: str, detail: str, status_code: int, retry_after: int | None = None) -> None:
        self.code, self.detail, self.status_code, self.retry_after = code, detail, status_code, retry_after
        super().__init__(code)


@dataclass(frozen=True)
class Window:
    key: str
    end: datetime


@dataclass(frozen=True)
class AdmissionDimension:
    """One bounded counter change staged as part of an admission."""

    dimension: str
    subject: str
    window: Window
    units: int
    limit: int
    error_code: str
    error_detail: str


@dataclass(frozen=True)
class AdmissionPlan:
    """All known counter changes for a single analysis request."""

    user_id: str
    dimensions: tuple[AdmissionDimension, ...]
    provider_units: tuple[tuple[str, int], ...] = ()
    create_reservation: bool = True

    def units_for(self, provider: str) -> int:
        return sum(units for name, units in self.provider_units if name == provider)


def is_unlimited_user(authoritative_user_id: str) -> bool:
    """Return a server-configured entitlement for an authenticated Appwrite ID."""
    configured = os.getenv("UNLIMITED_USER_IDS", settings.unlimited_user_ids)
    allowed = {item.strip() for item in configured.split(",") if item.strip()}
    return isinstance(authoritative_user_id, str) and authoritative_user_id in allowed


def normalize_client_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value.strip())
    except (TypeError, ValueError) as exc:
        raise RateLimitError("rate_limit_unavailable", "Сервис временно недоступен. Попробуйте позже.", 503) from exc
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return str(address)


def _window(now: datetime, period: str) -> Window:
    now = now.astimezone(timezone.utc)
    if period == "minute":
        start = now.replace(second=0, microsecond=0); end = start + timedelta(minutes=1); key = start.strftime("%Y-%m-%dT%H:%M")
    elif period == "hour":
        start = now.replace(minute=0, second=0, microsecond=0); end = start + timedelta(hours=1); key = start.strftime("%Y-%m-%dT%H")
    elif period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0); end = start + timedelta(days=1); key = start.strftime("%Y-%m-%d")
    else:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0); end = (start.replace(day=28) + timedelta(days=4)).replace(day=1); key = start.strftime("%Y-%m")
    return Window(key, end)


class AppwriteTablesRateLimitStore:
    TRANSACTION_TTL_SECONDS = 60

    def __init__(self, api_key: str, *, now: datetime | None = None) -> None:
        self.enabled = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
        self.endpoint = os.getenv("APPWRITE_FUNCTION_API_ENDPOINT", "").rstrip("/")
        self.project = os.getenv("APPWRITE_FUNCTION_PROJECT_ID", "")
        self.database = os.getenv("APPWRITE_DATABASE_ID", "yav")
        self.table = os.getenv("APPWRITE_RATE_LIMITS_TABLE_ID", "rate_limits")
        self.reservations_table = os.getenv("APPWRITE_QUOTA_RESERVATIONS_TABLE_ID", "quota_reservations")
        self.secret = os.getenv("RATE_LIMIT_IP_HMAC_KEY", "")
        self.api_key, self.now = api_key, now or datetime.now(timezone.utc)
        if not self.enabled and os.getenv("APP_ENV", "production").lower() not in {"test", "development"}:
            raise RateLimitError("rate_limit_unavailable", "Сервис временно недоступен. Попробуйте позже.", 503)
        if self.enabled and (not self.endpoint or not self.project or not self.secret):
            logger.warning(
                "rate_limit_persistence_failed operation=%s status_code=%s appwrite_type=%s appwrite_code=%s",
                "rate_limits.configuration", None, "configuration_missing", None,
            )
            raise RateLimitError("rate_limit_unavailable", "Сервис временно недоступен. Попробуйте позже.", 503)

    @staticmethod
    def _appwrite_failure_details(
        response: Any | None = None, exc: BaseException | None = None,
    ) -> tuple[Any, Any, Any]:
        status_code = getattr(response, "status_code", None)
        error_type = type(exc).__name__ if exc is not None else type(response).__name__ if response is not None else None
        appwrite_code: Any = None
        if response is not None:
            try:
                body = response.json()
            except (TypeError, ValueError):
                body = None
            if isinstance(body, dict):
                error_type = body.get("type") if isinstance(body.get("type"), str) else error_type
                appwrite_code = body.get("code") if isinstance(body.get("code"), (int, str)) else None
        return status_code, error_type, appwrite_code

    @classmethod
    def _unavailable(
        cls, operation: str, *, response: Any | None = None, exc: BaseException | None = None,
    ) -> RateLimitError:
        """Log bounded Appwrite failure metadata without request or secret material."""
        status_code, error_type, appwrite_code = cls._appwrite_failure_details(response, exc)
        logger.warning(
            "rate_limit_persistence_failed operation=%s status_code=%s appwrite_type=%s appwrite_code=%s",
            operation, status_code, error_type, appwrite_code,
        )
        return RateLimitError("rate_limit_unavailable", "Сервис временно недоступен. Попробуйте позже.", 503)

    @classmethod
    def _quota_unavailable(
        cls, operation: str, *, response: Any | None = None, exc: BaseException | None = None,
        quota_dimension: str | None = None, row_id: str | None = None, data: dict[str, Any] | None = None,
    ) -> RateLimitError:
        """Emit only bounded quota persistence metadata; never log row values."""
        status_code, error_type, appwrite_code = cls._appwrite_failure_details(response, exc)
        values = data or {}
        data_keys = ",".join(sorted(values))
        field_types = ",".join(f"{key}:{type(value).__name__}" for key, value in sorted(values.items()))
        string_lengths = ",".join(
            f"{key}:{len(value)}" for key, value in sorted(values.items()) if isinstance(value, str)
        )
        logger.warning(
            "quota_persistence_failed operation=%s status_code=%s appwrite_type=%s appwrite_code=%s "
            "quota_dimension=%s row_id_length=%s data_keys=%s field_types=%s string_lengths=%s",
            operation, status_code, error_type, appwrite_code, quota_dimension,
            len(row_id) if row_id is not None else None, data_keys, field_types, string_lengths,
        )
        return RateLimitError("rate_limit_unavailable", "Сервис временно недоступен. Попробуйте позже.", 503)

    @staticmethod
    def _sanitize_transaction_error_message(message: Any, sensitive_values: tuple[str, ...]) -> str | None:
        """Retain only a bounded diagnostic message, without credentials or request identity."""
        if not isinstance(message, str):
            return None
        sanitized = re.sub(r"[\r\n]+", " ", message)
        for value in sensitive_values:
            if value:
                sanitized = sanitized.replace(value, "<redacted>")
        sanitized = re.sub(
            r"(?i)(?:x-appwrite-[a-z0-9-]+|authorization)\s*[:=]\s*\S+|bearer\s+\S+",
            "<redacted-sensitive>",
            sanitized,
        )
        return sanitized[:300]

    def _transaction_create_unavailable(
        self, *, response: Any | None = None, exc: BaseException | None = None,
        quota_dimension: str | None = None, user_id: str = "",
    ) -> RateLimitError:
        """Log only the bounded Appwrite validation message for transaction creation."""
        status_code, error_type, appwrite_code = self._appwrite_failure_details(response, exc)
        message: Any = None
        if response is not None:
            try:
                body = response.json()
            except (TypeError, ValueError):
                body = None
            if isinstance(body, dict):
                message = body.get("message")
        safe_message = self._sanitize_transaction_error_message(
            message, (self.api_key, self.secret, user_id),
        )
        logger.warning(
            "quota_transaction_create_failed operation=%s status_code=%s appwrite_type=%s "
            "appwrite_code=%s quota_dimension=%s appwrite_message=%s",
            "quota.transaction.create", status_code, error_type, appwrite_code,
            quota_dimension, safe_message,
        )
        return RateLimitError("rate_limit_unavailable", "Сервис временно недоступен. Попробуйте позже.", 503)

    def _transaction_commit_unavailable(
        self, *, response: Any | None, quota_dimension: str, row_id: str, data: dict[str, Any],
        user_id: str,
    ) -> RateLimitError:
        """Add only a bounded Appwrite validation message to commit diagnostics."""
        status_code, error_type, appwrite_code = self._appwrite_failure_details(response)
        message: Any = None
        if response is not None:
            try:
                body = response.json()
            except (TypeError, ValueError):
                body = None
            if isinstance(body, dict):
                message = body.get("message")
        safe_message = self._sanitize_transaction_error_message(
            message, (self.api_key, self.secret, user_id, *(value for value in data.values() if isinstance(value, str))),
        )
        data_keys = ",".join(sorted(data))
        field_types = ",".join(f"{key}:{type(value).__name__}" for key, value in sorted(data.items()))
        string_lengths = ",".join(
            f"{key}:{len(value)}" for key, value in sorted(data.items()) if isinstance(value, str)
        )
        logger.warning(
            "quota_persistence_failed operation=%s status_code=%s appwrite_type=%s appwrite_code=%s "
            "quota_dimension=%s row_id_length=%s data_keys=%s field_types=%s string_lengths=%s appwrite_message=%s",
            "quota.transaction.commit", status_code, error_type, appwrite_code, quota_dimension,
            len(row_id), data_keys, field_types, string_lengths, safe_message,
        )
        return RateLimitError("rate_limit_unavailable", "Сервис временно недоступен. Попробуйте позже.", 503)

    @staticmethod
    def _staged_operation_structure(action: str, table_id: str, row_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": action,
            "table_id": table_id,
            "row_id_length": len(row_id),
            "data_keys": ",".join(sorted(data)),
            "field_types": ",".join(f"{key}:{type(value).__name__}" for key, value in sorted(data.items())),
            "string_lengths": ",".join(
                f"{key}:{len(value)}" for key, value in sorted(data.items()) if isinstance(value, str)
            ),
            "transaction_id_placement": "top_level",
        }

    @staticmethod
    def _counter_count(response: Any) -> int | None:
        try:
            value = response.json().get("count")
        except (AttributeError, TypeError, ValueError):
            return None
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    @staticmethod
    def _appwrite_error_type(response: Any) -> str | None:
        try:
            error_type = response.json().get("type")
        except (AttributeError, TypeError, ValueError):
            return None
        return error_type if isinstance(error_type, str) else None

    async def _increment_confirms_counter_exhausted(
        self, client: Any, *, response: Any, counter_url: str, headers: dict[str, str], limit: int,
        dimension: str,
    ) -> bool:
        """Confirm only the bounded-increment column limit race as exhaustion."""
        if self._appwrite_error_type(response) != "column_limit_exceeded":
            return False
        try:
            current = await client.get(counter_url, headers=headers)
        except httpx.HTTPError:
            return False
        count = self._counter_count(current) if current.status_code == 200 else None
        if count is None or count < limit:
            return False
        logger.warning(
            "rate_limit_boundary_confirmed operation=rate_limits.increment dimension=%s limit=%s count=%s",
            dimension, limit, count,
        )
        return True

    async def _commit_confirms_quota_exhausted(
        self, client: Any, *, response: Any, existing_counter: bool, pre_count: int | None,
        staged_increment: bool, increment_max: int | None, limit: int, rows: str, quota_id: str,
        headers: dict[str, str],
    ) -> bool:
        """Classify only a known quota-boundary commit failure, including a race read-back."""
        if (
            not existing_counter or not staged_increment or increment_max != limit
            or self._appwrite_error_type(response) not in {"row_max_exceeded", "attribute_limit_exceeded"}
        ):
            return False
        if pre_count is not None and pre_count >= limit:
            return True
        try:
            current = await client.get(f"{rows}/{quota_id}", headers=headers)
        except httpx.HTTPError:
            return False
        return current.status_code == 200 and (self._counter_count(current) or -1) >= limit

    @staticmethod
    async def _rollback_failed_transaction(client: Any, transactions: str, transaction_id: str, headers: dict[str, str]) -> None:
        """Best-effort cleanup after a confirmed non-conflict commit rejection."""
        try:
            await client.patch(f"{transactions}/{transaction_id}", headers=headers, json={"rollback": True})
        except httpx.HTTPError:
            pass

    @staticmethod
    def limit(name: str, default: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except ValueError as exc:
            raise RateLimitError("rate_limit_unavailable", "Сервис временно недоступен. Попробуйте позже.", 503) from exc
        if value <= 0:
            raise RateLimitError("rate_limit_unavailable", "Сервис временно недоступен. Попробуйте позже.", 503)
        return value

    def ip_subject(self, raw_ip: str) -> str:
        return hmac.new(self.secret.encode(), normalize_client_ip(raw_ip).encode(), hashlib.sha256).hexdigest()[:48]

    def _quota_error(self, dimension: AdmissionDimension) -> RateLimitError:
        retry_after = max(1, int((dimension.window.end - self.now).total_seconds()))
        return RateLimitError(
            dimension.error_code, dimension.error_detail, 429 if dimension.error_code != "provider_temporarily_unavailable" else 503,
            retry_after if dimension.error_code != "provider_temporarily_unavailable" else None,
        )

    async def admit(self, plan: AdmissionPlan) -> None:
        """Atomically spend every known quota dimension before provider I/O.

        A transaction is retried only for write conflicts.  Capacity, malformed
        Appwrite replies, timeouts and 5xx responses all fail closed.
        """
        if not self.enabled:
            return
        if not plan.dimensions:
            return
        for item in plan.dimensions:
            if item.units <= 0 or item.limit <= 0 or item.units > item.limit:
                raise self._quota_unavailable("quota.admission.plan", quota_dimension=item.dimension)

        rows = f"{self.endpoint}/tablesdb/{self.database}/tables/{self.table}/rows"
        reservations = f"{self.endpoint}/tablesdb/{self.database}/tables/{self.reservations_table}/rows"
        transactions = f"{self.endpoint}/tablesdb/transactions"
        headers = {"X-Appwrite-Project": self.project, "X-Appwrite-Key": self.api_key}
        reservation_id = uuid.uuid4().hex
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                for attempt in range(3):
                    created_transaction = await client.post(
                        transactions, headers=headers, json={"ttl": self.TRANSACTION_TTL_SECONDS},
                    )
                    if created_transaction.status_code not in (200, 201):
                        raise self._transaction_create_unavailable(response=created_transaction, user_id=plan.user_id)
                    try:
                        transaction_id = created_transaction.json().get("$id")
                    except (AttributeError, TypeError, ValueError) as exc:
                        raise self._transaction_create_unavailable(exc=exc, user_id=plan.user_id) from exc
                    if not isinstance(transaction_id, str) or not transaction_id:
                        raise self._transaction_create_unavailable(response=created_transaction, user_id=plan.user_id)

                    conflict = False
                    for item in plan.dimensions:
                        row_id = self._row_id(item.dimension, item.subject, item.window.key)
                        data = {
                            "dimension": item.dimension,
                            "subject": item.subject,
                            "window_start": item.window.key,
                            "window_end": item.window.end.isoformat(),
                            "count": item.units,
                        }
                        current = await client.get(
                            f"{rows}/{row_id}", headers=headers, params={"transactionId": transaction_id},
                        )
                        if current.status_code == 404:
                            staged = await client.post(
                                rows, headers=headers,
                                json={"rowId": row_id, "data": data, "permissions": [], "transactionId": transaction_id},
                            )
                        elif current.status_code == 200:
                            staged = await client.patch(
                                f"{rows}/{row_id}/count/increment", headers=headers,
                                json={"value": item.units, "max": item.limit, "transactionId": transaction_id},
                            )
                        else:
                            await self._rollback_failed_transaction(client, transactions, transaction_id, headers)
                            raise self._quota_unavailable(
                                "quota.admission.read", response=current, quota_dimension=item.dimension, row_id=row_id, data=data,
                            )

                        error_type = self._appwrite_error_type(staged)
                        if staged.status_code == 409:
                            conflict = True
                            break
                        if staged.status_code == 400 and error_type in {
                            "row_max_exceeded", "attribute_limit_exceeded", "column_limit_exceeded",
                        }:
                            await self._rollback_failed_transaction(client, transactions, transaction_id, headers)
                            raise self._quota_error(item)
                        if staged.status_code not in (200, 201):
                            await self._rollback_failed_transaction(client, transactions, transaction_id, headers)
                            raise self._quota_unavailable(
                                "quota.admission.stage", response=staged, quota_dimension=item.dimension, row_id=row_id, data=data,
                            )
                    if conflict:
                        await self._rollback_failed_transaction(client, transactions, transaction_id, headers)
                        continue

                    if plan.create_reservation:
                        reservation_data = {
                            "user_id": plan.user_id,
                            "quota_dimension": "admission",
                            "window_start": _window(self.now, "day").key,
                            "state": "consumed",
                        }
                        staged_reservation = await client.post(
                            reservations, headers=headers,
                            json={"rowId": reservation_id, "data": reservation_data, "permissions": [], "transactionId": transaction_id},
                        )
                        if staged_reservation.status_code == 409:
                            # UUID collision is not an exhaustion signal; retry with a new reservation.
                            reservation_id = uuid.uuid4().hex
                            await self._rollback_failed_transaction(client, transactions, transaction_id, headers)
                            continue
                        if staged_reservation.status_code not in (200, 201):
                            await self._rollback_failed_transaction(client, transactions, transaction_id, headers)
                            raise self._quota_unavailable(
                                "quota.admission.reservation", response=staged_reservation,
                                quota_dimension="admission", row_id=reservation_id, data=reservation_data,
                            )

                    committed = await client.patch(
                        f"{transactions}/{transaction_id}", headers=headers, json={"commit": True},
                    )
                    if committed.status_code == 200:
                        return
                    if committed.status_code == 409:
                        continue
                    await self._rollback_failed_transaction(client, transactions, transaction_id, headers)
                    raise self._transaction_commit_unavailable(
                        response=committed, quota_dimension="admission", row_id=reservation_id,
                        data={"dimensions": len(plan.dimensions)}, user_id=plan.user_id,
                    )
        except httpx.HTTPError as exc:
            raise self._quota_unavailable("quota.admission.transport", exc=exc) from exc
        raise RateLimitError("rate_limit_unavailable", "Сервис временно недоступен. Попробуйте позже.", 503)

    async def admit_provider_units(self, provider: str, units: int) -> None:
        """Spend an unplanned provider operation without charging user/IP quota."""
        plan = _provider_plan(self, provider, units)
        if plan.dimensions:
            await self.admit(plan)

    async def consume(self, dimension: str, subject: str, period: str, limit: int) -> int:
        if not self.enabled:
            return 0
        window = _window(self.now, period)
        row_id = self._row_id(dimension, subject, window.key)
        url = f"{self.endpoint}/tablesdb/{self.database}/tables/{self.table}/rows"
        headers = {"X-Appwrite-Project": self.project, "X-Appwrite-Key": self.api_key}
        payload = {"rowId": row_id, "data": {"dimension": dimension, "subject": subject, "window_start": window.key, "window_end": window.end.isoformat(), "count": 1}, "permissions": []}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                created = await client.post(url, headers=headers, json=payload)
                if created.status_code in (200, 201):
                    return 0
                if created.status_code != 409:
                    raise self._unavailable("rate_limits.create", response=created)
                counter_url = f"{url}/{row_id}"
                incremented = await client.patch(
                    f"{counter_url}/count/increment", headers=headers, json={"value": 1, "max": limit}
                )
                if incremented.status_code == 200:
                    return 0
                if incremented.status_code == 409:
                    return max(1, int((window.end - self.now).total_seconds()))
                if incremented.status_code == 400:
                    error_type = self._appwrite_error_type(incremented)
                    if error_type == "row_max_exceeded":
                        return max(1, int((window.end - self.now).total_seconds()))
                    if await self._increment_confirms_counter_exhausted(
                        client,
                        response=incremented,
                        counter_url=counter_url,
                        headers=headers,
                        limit=limit,
                        dimension=dimension,
                    ):
                        return max(1, int((window.end - self.now).total_seconds()))
        except httpx.HTTPError as exc:
            raise self._unavailable("rate_limits.create_or_increment", exc=exc) from exc
        raise self._unavailable("rate_limits.increment", response=incremented)

    async def guard_provider(self, provider: str) -> None:
        env_name = f"PROVIDER_{provider.upper()}_PER_MINUTE"
        retry_after = await self.consume("provider_minute", provider, "minute", self.limit(env_name, 60))
        if retry_after:
            raise RateLimitError("provider_temporarily_unavailable", "Сервис анализа временно перегружен. Попробуйте позже.", 503)

    async def trusted_plan(self, user_id: str) -> str:
        """Read the server-owned profile; request content never influences plan."""
        url = f"{self.endpoint}/tablesdb/{self.database}/tables/{os.getenv('APPWRITE_USERS_TABLE_ID', 'users')}/rows/{user_id}"
        headers = {"X-Appwrite-Project": self.project, "X-Appwrite-Key": self.api_key}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise self._quota_unavailable("quota.plan.read", exc=exc) from exc
        if response.status_code != 200:
            raise self._quota_unavailable("quota.plan.read", response=response)
        try:
            plan = response.json().get("plan")
        except (AttributeError, TypeError, ValueError):
            raise self._quota_unavailable("quota.plan.decode", response=response)
        return "premium" if plan == "premium" else "free"

    async def reserve_quota(self, user_id: str) -> "QuotaReservation":
        plan = await self.trusted_plan(user_id)
        period = "month" if plan == "premium" else "day"
        dimension = "quota_monthly" if plan == "premium" else "quota_daily"
        limit = self.limit("PREMIUM_MONTHLY_LIMIT", 100) if plan == "premium" else self.limit("FREE_DAILY_LIMIT", 3)
        window = _window(self.now, period)
        reservation = QuotaReservation(uuid.uuid4().hex, user_id, dimension, window.key, "reserved")
        quota_id = self._row_id(dimension, user_id, window.key)
        rows = f"{self.endpoint}/tablesdb/{self.database}/tables/{self.table}/rows"
        reservations = f"{self.endpoint}/tablesdb/{self.database}/tables/{self.reservations_table}/rows"
        transactions = f"{self.endpoint}/tablesdb/transactions"
        headers = {"X-Appwrite-Project": self.project, "X-Appwrite-Key": self.api_key}
        code = "monthly_quota_exceeded" if plan == "premium" else "daily_quota_exceeded"
        detail = "Месячный лимит проверок исчерпан." if plan == "premium" else "Дневной лимит проверок исчерпан."
        quota_data = {"dimension": dimension, "subject": user_id, "window_start": window.key, "window_end": window.end.isoformat(), "count": 1}
        reservation_data = {"user_id": user_id, "quota_dimension": dimension, "window_start": window.key, "state": "reserved"}
        if (
            not isinstance(user_id, str) or not user_id or len(user_id) > 36
            or len(dimension) > 32 or len(window.key) > 16 or len("reserved") > 16
        ):
            raise self._quota_unavailable(
                "quota.reservation.payload_validation", quota_dimension=dimension,
                row_id=reservation.id, data=reservation_data,
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                for _ in range(3):
                    staged_operations: list[dict[str, Any]] = []
                    existing = await client.get(f"{rows}/{quota_id}", headers=headers)
                    if existing.status_code not in (200, 404):
                        self._quota_unavailable("quota.counter.read", response=existing, quota_dimension=dimension, row_id=quota_id, data=quota_data)
                        break
                    existing_counter = existing.status_code == 200
                    pre_count = self._counter_count(existing) if existing_counter else None
                    transaction = await client.post(
                        transactions, headers=headers, json={"ttl": self.TRANSACTION_TTL_SECONDS},
                    )
                    if transaction.status_code not in (200, 201):
                        self._transaction_create_unavailable(
                            response=transaction, quota_dimension=dimension, user_id=user_id,
                        )
                        break
                    try:
                        transaction_body = transaction.json()
                    except (TypeError, ValueError):
                        self._transaction_create_unavailable(
                            response=transaction, quota_dimension=dimension, user_id=user_id,
                        )
                        break
                    transaction_id = transaction_body.get("$id") if isinstance(transaction_body, dict) else None
                    if not isinstance(transaction_id, str) or not transaction_id:
                        self._transaction_create_unavailable(
                            response=transaction, quota_dimension=dimension, user_id=user_id,
                        )
                        break
                    if existing.status_code == 404:
                        staged = await client.post(rows, headers=headers, json={"rowId": quota_id, "data": quota_data, "permissions": [], "transactionId": transaction_id})
                        staged_operations.append(self._staged_operation_structure("create", self.table, quota_id, quota_data))
                    else:
                        increment_data = {"value": 1, "max": limit}
                        staged = await client.patch(f"{rows}/{quota_id}/count/increment", headers=headers, json={**increment_data, "transactionId": transaction_id})
                        staged_operations.append(self._staged_operation_structure("increment:count", self.table, quota_id, increment_data))
                    try:
                        staged_body = staged.json()
                    except (TypeError, ValueError):
                        staged_body = None
                    error_type = staged_body.get("type") if isinstance(staged_body, dict) else None
                    if staged.status_code == 400 and error_type == "row_max_exceeded":
                        await client.patch(f"{transactions}/{transaction_id}", headers=headers, json={"rollback": True})
                        raise RateLimitError(code, detail, 429, max(1, int((window.end - self.now).total_seconds())))
                    if staged.status_code != 200 and staged.status_code != 201:
                        self._quota_unavailable(
                            "quota.counter.create" if existing.status_code == 404 else "quota.counter.increment",
                            response=staged, quota_dimension=dimension, row_id=quota_id, data=quota_data,
                        )
                        break
                    created = await client.post(reservations, headers=headers, json={"rowId": reservation.id, "data": reservation_data, "permissions": [], "transactionId": transaction_id})
                    staged_operations.append(self._staged_operation_structure("create", self.reservations_table, reservation.id, reservation_data))
                    if created.status_code not in (200, 201):
                        self._quota_unavailable("quota.reservation.create", response=created, quota_dimension=dimension, row_id=reservation.id, data=reservation_data)
                        await client.patch(f"{transactions}/{transaction_id}", headers=headers, json={"rollback": True})
                        break
                    logger.warning(
                        "quota_transaction_commit_staged operation_count=%s operations=%s",
                        len(staged_operations), json.dumps(staged_operations, separators=(",", ":")),
                    )
                    committed = await client.patch(f"{transactions}/{transaction_id}", headers=headers, json={"commit": True})
                    if committed.status_code == 200:
                        return reservation
                    if committed.status_code == 409:
                        continue
                    exhausted = await self._commit_confirms_quota_exhausted(
                        client, response=committed, existing_counter=existing_counter, pre_count=pre_count,
                        staged_increment=existing_counter, increment_max=limit if existing_counter else None,
                        limit=limit, rows=rows, quota_id=quota_id, headers=headers,
                    )
                    await self._rollback_failed_transaction(client, transactions, transaction_id, headers)
                    if exhausted:
                        raise RateLimitError(
                            code, detail, 429, max(1, int((window.end - self.now).total_seconds()))
                        )
                    self._transaction_commit_unavailable(
                        response=committed, quota_dimension=dimension, row_id=reservation.id,
                        data=reservation_data, user_id=user_id,
                    )
                    break
        except httpx.HTTPError as exc:
            raise self._quota_unavailable("quota.transport", exc=exc, quota_dimension=dimension, row_id=reservation.id, data=reservation_data) from exc
        raise self._quota_unavailable("quota.reservation.incomplete", quota_dimension=dimension, row_id=reservation.id, data=reservation_data)

    def _row_id(self, dimension: str, subject: str, key: str) -> str:
        return hashlib.sha256(f"{dimension}:{subject}:{key}".encode()).hexdigest()[:36]

    async def transition_quota(self, reservation: "QuotaReservation", target: str) -> None:
        if target not in {"consumed", "refunded"}:
            raise ValueError("invalid quota state")
        url = f"{self.endpoint}/tablesdb/{self.database}/tables/{self.reservations_table}/rows/{reservation.id}"
        headers = {"X-Appwrite-Project": self.project, "X-Appwrite-Key": self.api_key}
        transactions = f"{self.endpoint}/tablesdb/transactions"
        async with httpx.AsyncClient(timeout=10.0) as client:
            for _ in range(3):
                transaction = await client.post(
                    transactions, headers=headers, json={"ttl": self.TRANSACTION_TTL_SECONDS},
                )
                if transaction.status_code not in (200, 201):
                    break
                transaction_id = transaction.json().get("$id")
                if not isinstance(transaction_id, str) or not transaction_id:
                    break
                current = await client.get(url, headers=headers, params={"transactionId": transaction_id})
                if current.status_code != 200:
                    break
                if current.json().get("state") != "reserved":
                    await client.patch(f"{transactions}/{transaction_id}", headers=headers, json={"rollback": True})
                    return
                updated = await client.patch(url, headers=headers, json={"data": {"state": target}, "transactionId": transaction_id})
                if updated.status_code != 200:
                    break
                if target == "refunded":
                    counter = f"{self.endpoint}/tablesdb/{self.database}/tables/{self.table}/rows/{self._row_id(reservation.dimension, reservation.user_id, reservation.window)}/count/decrement"
                    decremented = await client.patch(counter, headers=headers, json={"value": 1, "min": 0, "transactionId": transaction_id})
                    if decremented.status_code != 200:
                        break
                committed = await client.patch(f"{transactions}/{transaction_id}", headers=headers, json={"commit": True})
                if committed.status_code == 200:
                    return
                if committed.status_code != 409:
                    break
        raise RateLimitError("rate_limit_unavailable", "Сервис временно недоступен. Попробуйте позже.", 503)


@dataclass(frozen=True)
class QuotaReservation:
    id: str
    user_id: str
    dimension: str
    window: str
    state: str


def _parse_created_at(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise RateLimitError("rate_limit_unavailable", "Сервис временно недоступен. Попробуйте позже.", 503)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RateLimitError("rate_limit_unavailable", "Сервис временно недоступен. Попробуйте позже.", 503) from exc
    if parsed.tzinfo is None:
        raise RateLimitError("rate_limit_unavailable", "Сервис временно недоступен. Попробуйте позже.", 503)
    return parsed.astimezone(timezone.utc)


def _anchored_window(created_at: datetime, now: datetime, hours: int) -> Window:
    """A fixed account-anchored bucket; deliberately not a rolling window."""
    index = max(0, int((now - created_at).total_seconds() // (hours * 3600)))
    start = created_at + timedelta(hours=index * hours)
    # Existing generic rows use a short ``window_start`` attribute.  The user
    # subject makes this hour key unambiguous while the exact account-created
    # timestamp still determines the bucket arithmetic above.
    return Window(start.strftime("%Y-%m-%dT%H"), start + timedelta(hours=hours))


def _dimension(
    name: str, subject: str, window: Window, units: int, limit: int, code: str, detail: str,
) -> AdmissionDimension:
    return AdmissionDimension(name, subject, window, units, limit, code, detail)


def _provider_plan(store: AppwriteTablesRateLimitStore, provider: str, units: int) -> AdmissionPlan:
    if not isinstance(units, int) or isinstance(units, bool) or units <= 0:
        raise RateLimitError("rate_limit_unavailable", "Сервис временно недоступен. Попробуйте позже.", 503)
    now = store.now
    day, month = _window(now, "day"), _window(now, "month")
    unavailable = ("provider_temporarily_unavailable", "Проверка этого типа временно недоступна.")
    if provider == "gemini":
        items = [_dimension("global_gemini_daily", "global", day, units, settings.global_gemini_operations_daily, *unavailable)]
    elif provider == "sightengine":
        items = [
            _dimension("global_sightengine_daily", "global", day, units, settings.global_sightengine_daily, *unavailable),
            _dimension("global_sightengine_monthly", "global", month, units, settings.global_sightengine_monthly, *unavailable),
        ]
    elif provider == "aiornot":
        items = [
            _dimension("global_aiornot_words_daily", "global", day, units, settings.global_aiornot_words_daily, *unavailable),
            _dimension("global_aiornot_words_monthly", "global", month, units, settings.global_aiornot_words_monthly, *unavailable),
        ]
    elif provider == "sapling":
        items = [
            _dimension("global_sapling_chars_daily", "global", day, units, settings.global_sapling_chars_daily, *unavailable),
            _dimension("global_sapling_chars_monthly", "global", month, units, settings.global_sapling_chars_monthly, *unavailable),
        ]
    else:
        items = []
    return AdmissionPlan("provider-budget", tuple(items), ((provider, units),), create_reservation=False)


def build_admission_plan(
    store: AppwriteTablesRateLimitStore, *, user_id: str, client_ip: str, account_created_at: Any,
    media_type: str, input_size: int, text: str = "", hybrid: bool = False,
) -> AdmissionPlan:
    """Build the whole request admission before any provider can be contacted."""
    now = store.now
    created_at = _parse_created_at(account_created_at)
    unlimited = is_unlimited_user(user_id)
    is_new_user = not unlimited and now < created_at + timedelta(days=settings.new_user_period_days)
    try:
        kind = MediaType(media_type)
    except ValueError as exc:
        raise RateLimitError("rate_limit_unavailable", "Сервис временно недоступен. Попробуйте позже.", 503) from exc
    if input_size < 0:
        raise SecurityValidationError("invalid_request", "Некорректный размер входных данных.")

    # New-user input restrictions are checked before any quota is committed.
    if is_new_user:
        if kind == MediaType.TEXT:
            maximum = settings.new_user_hybrid_max_chars if hybrid else settings.new_user_text_max_chars
            if input_size > maximum:
                raise SecurityValidationError("text_too_long", f"Текст превышает лимит в {maximum} символов.", 413)
        else:
            maximum = {
                MediaType.IMAGE: settings.new_user_image_max_bytes,
                MediaType.AUDIO: settings.new_user_audio_max_bytes,
                MediaType.VIDEO: settings.new_user_video_max_bytes,
            }[kind]
            if input_size > maximum:
                raise SecurityValidationError("file_too_large", "Файл превышает лимит для новых пользователей.", 413)

    day = _window(now, "day")
    dimensions: list[AdmissionDimension] = []
    if not unlimited:
        dimensions.append(_dimension("ip_total_daily", store.ip_subject(client_ip), day, 1, settings.ip_total_daily,
                   "daily_quota_exceeded", "Достигнут дневной лимит проверок."))
    if not unlimited and kind in {MediaType.IMAGE, MediaType.AUDIO, MediaType.VIDEO}:
        dimensions.append(_dimension(
            "ip_heavy_media_daily", store.ip_subject(client_ip), day, 1, settings.ip_heavy_media_daily,
            "daily_quota_exceeded", "Достигнут дневной лимит проверок.",
        ))

    if is_new_user:
        first7 = Window(
            created_at.strftime("%Y-%m-%dT%H"),
            created_at + timedelta(days=settings.new_user_period_days),
        )
        dimensions.extend((
            _dimension("new_user_total_daily", user_id, day, 1, settings.new_user_total_daily,
                       "daily_quota_exceeded", "Достигнут дневной лимит проверок."),
            _dimension("new_user_total_first7d", user_id, first7, 1, settings.new_user_total_first_7d,
                       "new_user_quota_exceeded", "Достигнут лимит проверок для новых пользователей."),
        ))
        if kind == MediaType.TEXT:
            if hybrid:
                dimensions.append(_dimension("new_user_hybrid_daily", user_id, day, 1, settings.new_user_hybrid_daily,
                                             "new_user_type_quota_exceeded", "Достигнут лимит проверок этого типа."))
            else:
                dimensions.append(_dimension("new_user_text_daily", user_id, day, 1, settings.new_user_text_daily,
                                             "new_user_type_quota_exceeded", "Достигнут лимит проверок этого типа."))
        elif kind == MediaType.IMAGE:
            dimensions.append(_dimension("new_user_image_daily", user_id, day, 1, settings.new_user_image_daily,
                                         "new_user_type_quota_exceeded", "Достигнут лимит проверок этого типа."))
        elif kind == MediaType.AUDIO:
            dimensions.append(_dimension(
                "new_user_audio_72h", user_id, _anchored_window(created_at, now, settings.new_user_audio_window_hours),
                1, settings.new_user_audio_per_window, "new_user_type_quota_exceeded", "Достигнут лимит проверок этого типа.",
            ))
        elif kind == MediaType.VIDEO:
            dimensions.append(_dimension("new_user_video_first7d", user_id, first7, 1, settings.new_user_video_first_7d,
                                         "new_user_type_quota_exceeded", "Достигнут лимит проверок этого типа."))

    provider_units: list[tuple[str, int]] = []
    if kind == MediaType.TEXT:
        if hybrid:
            # Complex text has exactly its two parallel Gemini branches.
            provider_units.append(("gemini", 2))
        else:
            # This mirrors MediaRouter: eligible text goes to AIOrNot while
            # every normal text request has exactly one Gemini credibility call.
            words = len(text.strip().split())
            if len(text.strip()) >= 250 and words >= 64:
                provider_units.append(("aiornot", words))
            provider_units.append(("gemini", 1 if provider_units else 2))
    elif kind == MediaType.IMAGE:
        provider_units.append(("sightengine", 1))
    elif kind == MediaType.VIDEO:
        # Start-upload, finalize-upload and generateContent are known up front.
        # Processing polls, if any, are admitted just before each request.
        provider_units.append(("gemini", 3))

    for provider, units in provider_units:
        dimensions.extend(_provider_plan(store, provider, units).dimensions)
    return AdmissionPlan(user_id, tuple(dimensions), tuple(provider_units))


def build_source_media_admission_plan(
    store: AppwriteTablesRateLimitStore, *, user_id: str, client_ip: str,
    account_created_at: Any, has_image: bool, has_video: bool,
) -> AdmissionPlan:
    """Reserve only source dimensions learned after safe extraction.

    The initial source plan already owns total/IP/complex counters.  This plan
    intentionally never repeats them, and counts heavy media once per source.
    """
    now = store.now
    created_at = _parse_created_at(account_created_at)
    if is_unlimited_user(user_id):
        return AdmissionPlan(user_id, (), (), create_reservation=False)
    is_new_user = now < created_at + timedelta(days=settings.new_user_period_days)
    day = _window(now, "day")
    dimensions: list[AdmissionDimension] = []
    if has_image or has_video:
        dimensions.append(_dimension("ip_heavy_media_daily", store.ip_subject(client_ip), day, 1,
            settings.ip_heavy_media_daily, "daily_quota_exceeded", "Достигнут дневной лимит проверок."))
    if is_new_user and has_image:
        dimensions.append(_dimension("new_user_image_daily", user_id, day, 1, settings.new_user_image_daily,
            "new_user_type_quota_exceeded", "Достигнут лимит проверок этого типа."))
    if is_new_user and has_video:
        first7 = Window(created_at.strftime("%Y-%m-%dT%H"), created_at + timedelta(days=settings.new_user_period_days))
        dimensions.append(_dimension("new_user_video_first7d", user_id, first7, 1, settings.new_user_video_first_7d,
            "new_user_type_quota_exceeded", "Достигнут лимит проверок этого типа."))
    return AdmissionPlan(user_id, tuple(dimensions), (), create_reservation=False)


async def enforce_admission(store: AppwriteTablesRateLimitStore, user_id: str, client_ip: str) -> None:
    checks = (("ip_minute", store.ip_subject(client_ip), "minute", store.limit("RATE_LIMIT_IP_PER_MINUTE", 10)), ("user_minute", user_id, "minute", store.limit("RATE_LIMIT_USER_PER_MINUTE", 6)), ("user_hour", user_id, "hour", store.limit("RATE_LIMIT_USER_PER_HOUR", 30)))
    for dimension, subject, period, limit in checks:
        retry_after = await store.consume(dimension, subject, period, limit)
        if retry_after:
            raise RateLimitError("rate_limit_exceeded", "Слишком много запросов. Попробуйте позже.", 429, retry_after)
