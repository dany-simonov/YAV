"""Canonical subscription policy and trusted Appwrite profile access.

This module deliberately keeps subscriptions separate from request data.  A
subscription is a server-owned policy stored on the Appwrite user profile; the
only authority that may change it is a configured system administrator.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import quote

import httpx

from core.config import settings

SUBSCRIPTIONS = frozenset({"free", "pro", "enterprise", "custom"})
QUOTA_KEYS = frozenset({"checks", "heavy_media_checks"})
PROVIDER_QUOTA_KEYS = frozenset({"gemini", "sightengine", "aiornot", "sapling", "resemble"})
_USER_ID = re.compile(r"^[A-Za-z0-9._-]{1,36}$")
_ADMIN_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")
_MAX_QUOTA_LIMIT = 1_000_000


class SubscriptionValidationError(ValueError):
    """An invalid subscription or quota override supplied to a trusted API."""


class SubscriptionPersistenceError(RuntimeError):
    """A safe, fail-closed Appwrite subscription persistence failure."""


@dataclass(frozen=True)
class QuotaLimit:
    key: str
    period: str
    limit: int


@dataclass(frozen=True)
class EffectiveQuotaPolicy:
    """Server-derived limits to attach to an existing admission plan."""

    subscription: str
    limits: Mapping[str, QuotaLimit]
    overrides: Mapping[str, int]
    generations: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    provider_overrides: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))

    def quota(self, key: str) -> QuotaLimit:
        try:
            return self.limits[key]
        except KeyError as exc:
            raise SubscriptionValidationError("invalid quota key") from exc

    def generation(self, key: str) -> int:
        value = self.generations.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SubscriptionPersistenceError(
                "profile has an invalid quota generation"
            )
        return value


# The shape and source of every product policy lives here.  Settings hold only
# deployment values; no caller decides periods, dimensions, or fallback rules.
_POLICY_SPECS: Mapping[str, tuple[tuple[str, str, str], ...]] = MappingProxyType(
    {
        "free": (
            ("checks", "day", "free_daily_limit"),
            ("heavy_media_checks", "day", "free_heavy_media_daily_limit"),
        ),
        # ``premium_monthly_limit`` is retained as the deployment-compatible name
        # of the former plan while the canonical public subscription is ``pro``.
        "pro": (
            ("checks", "month", "premium_monthly_limit"),
            ("heavy_media_checks", "month", "pro_heavy_media_monthly_limit"),
        ),
        "enterprise": (
            ("checks", "month", "enterprise_monthly_limit"),
            ("heavy_media_checks", "month", "enterprise_heavy_media_monthly_limit"),
        ),
        "custom": (
            ("checks", "month", "custom_monthly_limit"),
            ("heavy_media_checks", "month", "custom_heavy_media_monthly_limit"),
        ),
    }
)


def validate_user_id(value: Any) -> str:
    if not isinstance(value, str) or not _USER_ID.fullmatch(value):
        raise SubscriptionValidationError("invalid user ID")
    return value


def normalize_subscription(value: Any) -> str:
    if not isinstance(value, str) or value not in SUBSCRIPTIONS:
        raise SubscriptionValidationError("invalid subscription")
    return value


def normalize_quota_overrides(value: Any) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > len(QUOTA_KEYS):
        raise SubscriptionValidationError("invalid quota overrides")
    normalized: dict[str, int] = {}
    for key, limit in value.items():
        if (
            key not in QUOTA_KEYS
            or isinstance(limit, bool)
            or not isinstance(limit, int)
        ):
            raise SubscriptionValidationError("invalid quota override")
        if not 1 <= limit <= _MAX_QUOTA_LIMIT:
            raise SubscriptionValidationError("invalid quota override")
        normalized[str(key)] = limit
    return normalized


def normalize_provider_quota_overrides(value: Any) -> dict[str, int]:
    """Validate optional per-user monthly provider-operation limits."""
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > len(PROVIDER_QUOTA_KEYS):
        raise SubscriptionValidationError("invalid provider quota overrides")
    normalized: dict[str, int] = {}
    for key, limit in value.items():
        if key not in PROVIDER_QUOTA_KEYS or isinstance(limit, bool) or not isinstance(limit, int):
            raise SubscriptionValidationError("invalid provider quota override")
        if not 1 <= limit <= _MAX_QUOTA_LIMIT:
            raise SubscriptionValidationError("invalid provider quota override")
        normalized[str(key)] = limit
    return normalized


def subscription_from_profile(profile: Mapping[str, Any]) -> str:
    """Read a canonical subscription, accepting only the historical plan safely."""
    subscription = profile.get("subscription")
    # An empty value can be produced by a partially completed schema rollout;
    # handle it exactly like the pre-subscription legacy row.
    if subscription in (None, ""):
        # Existing profiles predate the canonical field.  ``premium`` is a
        # compatibility value and is never exposed as a new subscription tier.
        legacy_plan = profile.get("plan", "free")
        if legacy_plan == "premium":
            return "pro"
        if legacy_plan == "free":
            return "free"
        raise SubscriptionPersistenceError("profile has an invalid legacy plan")
    try:
        return normalize_subscription(subscription)
    except SubscriptionValidationError as exc:
        raise SubscriptionPersistenceError(
            "profile has an invalid subscription"
        ) from exc


def overrides_from_profile(profile: Mapping[str, Any]) -> dict[str, int]:
    raw = profile.get("quota_overrides")
    if raw in (None, ""):
        return {}
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 1024:
        raise SubscriptionPersistenceError("profile has invalid quota overrides")
    try:
        decoded = json.loads(raw)
        return normalize_quota_overrides(decoded)
    except (
        TypeError,
        ValueError,
        json.JSONDecodeError,
        SubscriptionValidationError,
    ) as exc:
        raise SubscriptionPersistenceError(
            "profile has invalid quota overrides"
        ) from exc


def provider_overrides_from_profile(profile: Mapping[str, Any]) -> dict[str, int]:
    raw = profile.get("provider_quota_overrides")
    if raw in (None, ""):
        return {}
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 1024:
        raise SubscriptionPersistenceError("profile has invalid provider quota overrides")
    try:
        return normalize_provider_quota_overrides(json.loads(raw))
    except (TypeError, ValueError, json.JSONDecodeError, SubscriptionValidationError) as exc:
        raise SubscriptionPersistenceError("profile has invalid provider quota overrides") from exc


def normalize_quota_generations(value: Any) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > len(QUOTA_KEYS):
        raise SubscriptionPersistenceError("profile has invalid quota generations")
    normalized: dict[str, int] = {}
    for key, generation in value.items():
        if (
            key not in QUOTA_KEYS
            or isinstance(generation, bool)
            or not isinstance(generation, int)
        ):
            raise SubscriptionPersistenceError("profile has invalid quota generations")
        if not 0 <= generation <= _MAX_QUOTA_LIMIT:
            raise SubscriptionPersistenceError("profile has invalid quota generations")
        normalized[str(key)] = generation
    return normalized


def effective_quota_policy(
    subscription: Any,
    overrides: Any = None,
    generations: Any = None,
    provider_overrides: Any = None,
) -> EffectiveQuotaPolicy:
    """Return defaults overridden by a bounded per-user server-side mapping."""
    tier = normalize_subscription(subscription)
    normalized_overrides = normalize_quota_overrides(overrides)
    normalized_generations = normalize_quota_generations(generations)
    normalized_provider_overrides = normalize_provider_quota_overrides(provider_overrides)
    limits: dict[str, QuotaLimit] = {}
    for key, period, setting_name in _POLICY_SPECS[tier]:
        configured = getattr(settings, setting_name)
        if (
            isinstance(configured, bool)
            or not isinstance(configured, int)
            or configured < 1
        ):
            raise SubscriptionPersistenceError("subscription policy is misconfigured")
        limits[key] = QuotaLimit(key, period, normalized_overrides.get(key, configured))
    return EffectiveQuotaPolicy(
        subscription=tier,
        limits=MappingProxyType(limits),
        overrides=MappingProxyType(normalized_overrides),
        generations=MappingProxyType(normalized_generations),
        provider_overrides=MappingProxyType(normalized_provider_overrides),
    )


def effective_policy_from_profile(profile: Mapping[str, Any]) -> EffectiveQuotaPolicy:
    return effective_quota_policy(
        subscription_from_profile(profile),
        overrides_from_profile(profile),
        provider_overrides=provider_overrides_from_profile(profile),
    )


def effective_policy_from_workspace(
    workspace: Mapping[str, Any],
) -> EffectiveQuotaPolicy:
    """Resolve server-managed shared policy from an already trusted workspace row."""
    if not isinstance(workspace, Mapping):
        raise SubscriptionPersistenceError("workspace has invalid quota policy")
    subscription = workspace.get("quota_plan")
    if subscription in (None, ""):
        subscription = "free"
    raw_overrides = workspace.get("quota_overrides")
    if raw_overrides in (None, ""):
        overrides: dict[str, int] = {}
    elif isinstance(raw_overrides, str) and len(raw_overrides.encode("utf-8")) <= 1024:
        try:
            overrides = normalize_quota_overrides(json.loads(raw_overrides))
        except (
            TypeError,
            ValueError,
            json.JSONDecodeError,
            SubscriptionValidationError,
        ) as exc:
            raise SubscriptionPersistenceError(
                "workspace has invalid quota overrides"
            ) from exc
    else:
        raise SubscriptionPersistenceError("workspace has invalid quota overrides")
    raw_provider_overrides = workspace.get("provider_quota_overrides")
    if raw_provider_overrides in (None, ""):
        provider_overrides: dict[str, int] = {}
    elif isinstance(raw_provider_overrides, str) and len(raw_provider_overrides.encode("utf-8")) <= 1024:
        try:
            provider_overrides = normalize_provider_quota_overrides(json.loads(raw_provider_overrides))
        except (
            TypeError,
            ValueError,
            json.JSONDecodeError,
            SubscriptionValidationError,
        ) as exc:
            raise SubscriptionPersistenceError(
                "workspace has invalid provider quota overrides"
            ) from exc
    else:
        raise SubscriptionPersistenceError("workspace has invalid provider quota overrides")
    try:
        return effective_quota_policy(
            subscription, overrides, provider_overrides=provider_overrides
        )
    except SubscriptionValidationError as exc:
        raise SubscriptionPersistenceError("workspace has invalid quota plan") from exc


def configured_system_admin_ids() -> frozenset[str]:
    """Return only valid server-configured Appwrite account IDs."""
    configured = os.getenv("SYSTEM_ADMIN_USER_IDS", settings.system_admin_user_ids)
    if not isinstance(configured, str) or not configured.strip():
        return frozenset()
    values = [part.strip() for part in configured.split(",")]
    # A typo must not silently grant access to the otherwise-valid entries in
    # the same deployment variable. Duplicates are harmless and deduplicated.
    if any(not _USER_ID.fullmatch(value) for value in values):
        return frozenset()
    return frozenset(values)


def configured_system_admin_emails() -> frozenset[str]:
    """Return a normalized, fail-closed Function-only email allowlist."""
    configured = os.getenv("SYSTEM_ADMIN_EMAILS", settings.system_admin_emails)
    if not isinstance(configured, str) or not configured.strip():
        return frozenset()
    values = [part.strip().lower() for part in configured.split(",")]
    if any(not _ADMIN_EMAIL.fullmatch(value) for value in values):
        return frozenset()
    return frozenset(values)


def is_system_admin(account: Mapping[str, Any], runtime_user_id: str) -> bool:
    """Verify the authenticated Appwrite identity against a server allowlist."""
    if not isinstance(account, Mapping):
        return False
    try:
        user_id = validate_user_id(runtime_user_id)
    except SubscriptionValidationError:
        return False
    # The account is the server-fetched /account result for the runtime JWT;
    # never trust request-provided role, email or isAdmin fields. An explicit
    # email list takes priority; deployments without it keep the existing
    # Appwrite-account-ID allowlist working.
    email = str(account.get("email") or "").strip().lower()
    allowed_emails = configured_system_admin_emails()
    return (
        str(account.get("$id") or "") == user_id
        and (
            email in allowed_emails
            if allowed_emails
            else user_id in configured_system_admin_ids()
        )
    )


def require_system_admin(account: Mapping[str, Any], runtime_user_id: str) -> None:
    if not is_system_admin(account, runtime_user_id):
        from src.validation import SecurityValidationError

        raise SecurityValidationError(
            "admin_access_denied", "Требуются права системного администратора.", 403
        )


def _legacy_plan(subscription: str) -> str:
    return "free" if subscription == "free" else "premium"


class AppwriteSubscriptionStore:
    """Narrow server-key-only persistence for subscription profile fields."""

    def __init__(self, api_key: str) -> None:
        if not isinstance(api_key, str) or not api_key:
            raise SubscriptionPersistenceError("missing Appwrite Function API key")
        self.api_key = api_key
        self.endpoint = os.getenv("APPWRITE_FUNCTION_API_ENDPOINT", "").rstrip("/")
        self.project = os.getenv("APPWRITE_FUNCTION_PROJECT_ID", "")
        self.database = os.getenv("APPWRITE_DATABASE_ID", "yav")
        self.users_table = os.getenv("APPWRITE_USERS_TABLE_ID", "users")
        self.generations_table = os.getenv(
            "APPWRITE_USER_QUOTA_GENERATIONS_TABLE_ID", "user_quota_generations"
        )
        if (
            not self.endpoint
            or not self.project
            or not self.database
            or not self.users_table
            or not self.generations_table
        ):
            raise SubscriptionPersistenceError(
                "missing Appwrite subscription configuration"
            )

    @property
    def _rows_url(self) -> str:
        return (
            f"{self.endpoint}/tablesdb/{self.database}/tables/{self.users_table}/rows"
        )

    @property
    def _generations_url(self) -> str:
        return f"{self.endpoint}/tablesdb/{self.database}/tables/{self.generations_table}/rows"

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Appwrite-Project": self.project, "X-Appwrite-Key": self.api_key}

    async def get_profile(self, user_id: str) -> dict[str, Any]:
        user_id = validate_user_id(user_id)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self._rows_url}/{quote(user_id, safe='')}", headers=self._headers
                )
        except httpx.HTTPError as exc:
            raise SubscriptionPersistenceError(
                "subscription profile read failed"
            ) from exc
        if response.status_code != 200:
            raise SubscriptionPersistenceError("subscription profile read failed")
        try:
            profile = response.json()
        except (TypeError, ValueError) as exc:
            raise SubscriptionPersistenceError(
                "subscription profile decode failed"
            ) from exc
        if not isinstance(profile, dict):
            raise SubscriptionPersistenceError("subscription profile decode failed")
        return profile

    async def get_effective_policy(self, user_id: str) -> EffectiveQuotaPolicy:
        user_id = validate_user_id(user_id)
        profile = await self.get_profile(user_id)
        return await self.effective_policy_for_profile(profile, user_id)

    async def effective_policy_for_profile(
        self,
        profile: Mapping[str, Any],
        user_id: str,
    ) -> EffectiveQuotaPolicy:
        # Profiles created before the generation-table migration retain their
        # existing counter subject until the documented schema rollout. This
        # preserves availability for tests and legacy deployments while all
        # migrated production profiles read authoritative generations below.
        if "quota_usage_generations" not in profile:
            return effective_policy_from_profile(profile)
        return effective_quota_policy(
            subscription_from_profile(profile),
            overrides_from_profile(profile),
            await self.get_quota_generations(user_id),
            provider_overrides_from_profile(profile),
        )

    @staticmethod
    def generation_row_id(user_id: str, quota_key: str) -> str:
        return hashlib.sha256(
            f"quota-generation:{user_id}:{quota_key}".encode()
        ).hexdigest()[:36]

    async def get_quota_generations(self, user_id: str) -> dict[str, int]:
        user_id = validate_user_id(user_id)
        generations: dict[str, int] = {}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                for quota_key in sorted(QUOTA_KEYS):
                    row_id = self.generation_row_id(user_id, quota_key)
                    response = await client.get(
                        f"{self._generations_url}/{row_id}",
                        headers=self._headers,
                    )
                    if response.status_code == 404:
                        continue
                    if response.status_code != 200:
                        raise SubscriptionPersistenceError(
                            "quota generation read failed"
                        )
                    body = response.json()
                    if (
                        not isinstance(body, dict)
                        or body.get("user_id") != user_id
                        or body.get("quota_key") != quota_key
                    ):
                        raise SubscriptionPersistenceError(
                            "quota generation decode failed"
                        )
                    generation = body.get("generation")
                    if (
                        isinstance(generation, bool)
                        or not isinstance(generation, int)
                        or generation < 0
                    ):
                        raise SubscriptionPersistenceError(
                            "quota generation decode failed"
                        )
                    generations[quota_key] = generation
        except httpx.HTTPError as exc:
            raise SubscriptionPersistenceError("quota generation read failed") from exc
        return generations

    async def update_subscription(
        self, user_id: str, subscription: Any
    ) -> EffectiveQuotaPolicy:
        user_id = validate_user_id(user_id)
        tier = normalize_subscription(subscription)
        await self._patch(user_id, {"subscription": tier, "plan": _legacy_plan(tier)})
        return await self.get_effective_policy(user_id)

    async def update_quota_overrides(
        self, user_id: str, overrides: Any
    ) -> EffectiveQuotaPolicy:
        user_id = validate_user_id(user_id)
        normalized = normalize_quota_overrides(overrides)
        await self._patch(
            user_id,
            {
                "quota_overrides": json.dumps(
                    normalized, separators=(",", ":"), sort_keys=True
                )
            },
        )
        return await self.get_effective_policy(user_id)

    async def update_provider_quota_overrides(
        self, user_id: str, overrides: Any
    ) -> EffectiveQuotaPolicy:
        user_id = validate_user_id(user_id)
        normalized = normalize_provider_quota_overrides(overrides)
        await self._patch(
            user_id,
            {"provider_quota_overrides": json.dumps(normalized, separators=(",", ":"), sort_keys=True)},
        )
        return await self.get_effective_policy(user_id)

    async def remove_quota_override(
        self, user_id: str, quota_key: Any
    ) -> EffectiveQuotaPolicy:
        user_id = validate_user_id(user_id)
        if quota_key not in QUOTA_KEYS:
            raise SubscriptionValidationError("invalid quota key")
        profile = await self.get_profile(user_id)
        overrides = overrides_from_profile(profile)
        overrides.pop(quota_key, None)
        return await self.update_quota_overrides(user_id, overrides)

    async def reset_quota_overrides(self, user_id: str) -> EffectiveQuotaPolicy:
        return await self.update_quota_overrides(user_id, {})

    async def _patch(self, user_id: str, data: dict[str, Any]) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.patch(
                    f"{self._rows_url}/{quote(user_id, safe='')}",
                    headers=self._headers,
                    json={"data": data},
                )
        except httpx.HTTPError as exc:
            raise SubscriptionPersistenceError(
                "subscription profile update failed"
            ) from exc
        if response.status_code != 200:
            raise SubscriptionPersistenceError("subscription profile update failed")
