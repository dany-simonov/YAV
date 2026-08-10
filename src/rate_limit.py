"""Cloud-only, fail-closed fixed-window admission using Appwrite TablesDB."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src.validation import SecurityValidationError


logger = logging.getLogger(__name__)


class RateLimitError(Exception):
    def __init__(self, code: str, detail: str, status_code: int, retry_after: int | None = None) -> None:
        self.code, self.detail, self.status_code, self.retry_after = code, detail, status_code, retry_after
        super().__init__(code)


@dataclass(frozen=True)
class Window:
    key: str
    end: datetime


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
                incremented = await client.patch(f"{url}/{row_id}/count/increment", headers=headers, json={"value": 1, "max": limit})
        except httpx.HTTPError as exc:
            raise self._unavailable("rate_limits.create_or_increment", exc=exc) from exc
        if incremented.status_code == 200:
            return 0
        if incremented.status_code == 409:
            return max(1, int((window.end - self.now).total_seconds()))
        if incremented.status_code == 400:
            try:
                error_type = incremented.json().get("type")
            except (AttributeError, TypeError, ValueError):
                error_type = None
            if error_type == "row_max_exceeded":
                return max(1, int((window.end - self.now).total_seconds()))
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
                    existing = await client.get(f"{rows}/{quota_id}", headers=headers)
                    if existing.status_code not in (200, 404):
                        self._quota_unavailable("quota.counter.read", response=existing, quota_dimension=dimension, row_id=quota_id, data=quota_data)
                        break
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
                    else:
                        staged = await client.patch(f"{rows}/{quota_id}/count/increment", headers=headers, json={"value": 1, "max": limit, "transactionId": transaction_id})
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
                    if created.status_code not in (200, 201):
                        self._quota_unavailable("quota.reservation.create", response=created, quota_dimension=dimension, row_id=reservation.id, data=reservation_data)
                        await client.patch(f"{transactions}/{transaction_id}", headers=headers, json={"rollback": True})
                        break
                    committed = await client.patch(f"{transactions}/{transaction_id}", headers=headers, json={"commit": True})
                    if committed.status_code == 200:
                        return reservation
                    if committed.status_code == 409:
                        continue
                    self._quota_unavailable("quota.transaction.commit", response=committed, quota_dimension=dimension, row_id=reservation.id, data=reservation_data)
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


async def enforce_admission(store: AppwriteTablesRateLimitStore, user_id: str, client_ip: str) -> None:
    checks = (("ip_minute", store.ip_subject(client_ip), "minute", store.limit("RATE_LIMIT_IP_PER_MINUTE", 10)), ("user_minute", user_id, "minute", store.limit("RATE_LIMIT_USER_PER_MINUTE", 6)), ("user_hour", user_id, "hour", store.limit("RATE_LIMIT_USER_PER_HOUR", 30)))
    for dimension, subject, period, limit in checks:
        retry_after = await store.consume(dimension, subject, period, limit)
        if retry_after:
            raise RateLimitError("rate_limit_exceeded", "Слишком много запросов. Попробуйте позже.", 429, retry_after)
