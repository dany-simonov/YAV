"""Canonical subscription policy and Appwrite Function authorization coverage."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main import _execute_request
from src.rate_limit import AppwriteTablesRateLimitStore, build_admission_plan
from src.subscriptions import (
    AppwriteSubscriptionStore,
    EffectiveQuotaPolicy,
    QuotaLimit,
    SubscriptionValidationError,
    configured_system_admin_ids,
    effective_policy_from_profile,
    effective_quota_policy,
    is_system_admin,
    normalize_quota_overrides,
)
from src.validation import SecurityValidationError, validate_request_payload


def _response(status: int, body=None):
    response = MagicMock(status_code=status)
    response.json.return_value = body or {}
    return response


def _store(monkeypatch):
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project")
    monkeypatch.setenv("APPWRITE_DATABASE_ID", "yav")
    monkeypatch.setenv("APPWRITE_USERS_TABLE_ID", "users")
    return AppwriteSubscriptionStore("runtime-key")


@pytest.mark.parametrize("subscription", ["free", "pro", "enterprise", "custom"])
def test_every_canonical_subscription_has_a_complete_policy(subscription):
    policy = effective_quota_policy(subscription)

    assert policy.subscription == subscription
    assert set(policy.limits) == {"checks", "heavy_media_checks"}
    assert all(
        limit.limit > 0 and limit.period in {"day", "month"}
        for limit in policy.limits.values()
    )


def test_one_override_changes_only_its_quota():
    policy = effective_quota_policy("pro", {"checks": 17})

    assert policy.quota("checks").limit == 17
    assert policy.quota("heavy_media_checks").limit > 0
    assert policy.overrides == {"checks": 17}


def test_multiple_overrides_and_removal_fall_back_to_subscription_default():
    policy = effective_policy_from_profile(
        {
            "subscription": "enterprise",
            "quota_overrides": '{"checks":41,"heavy_media_checks":9}',
        }
    )
    without_heavy = effective_quota_policy(
        policy.subscription, {"checks": policy.quota("checks").limit}
    )

    assert policy.quota("checks").limit == 41
    assert policy.quota("heavy_media_checks").limit == 9
    assert without_heavy.quota("checks").limit == 41
    assert without_heavy.quota("heavy_media_checks").limit != 9


@pytest.mark.parametrize("subscription", ["premium", "vip", "", None])
def test_invalid_subscription_is_rejected(subscription):
    with pytest.raises(SubscriptionValidationError):
        effective_quota_policy(subscription)


@pytest.mark.parametrize(
    "overrides",
    [
        {"unknown": 4},
        {"checks": 0},
        {"checks": True},
        {"checks": 1_000_001},
    ],
)
def test_invalid_quota_override_is_rejected(overrides):
    with pytest.raises(SubscriptionValidationError):
        normalize_quota_overrides(overrides)


def test_legacy_premium_profile_maps_to_canonical_pro_policy():
    assert effective_policy_from_profile({"plan": "premium"}).subscription == "pro"


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ({"subscription": "", "plan": "premium"}, "pro"),
        ({"subscription": None, "plan": "free"}, "free"),
        ({"plan": "free", "quota_overrides": ""}, "free"),
    ],
)
def test_partially_migrated_legacy_profiles_remain_readable(profile, expected):
    assert effective_policy_from_profile(profile).subscription == expected


@pytest.mark.asyncio
async def test_store_removes_one_override_and_persists_compact_json(monkeypatch):
    store = _store(monkeypatch)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(
        side_effect=[
            _response(
                200,
                {
                    "subscription": "pro",
                    "quota_overrides": '{"checks":41,"heavy_media_checks":9}',
                },
            ),
            _response(200, {"subscription": "pro", "quota_overrides": '{"checks":41}'}),
        ]
    )
    client.patch = AsyncMock(return_value=_response(200))

    with patch("src.subscriptions.httpx.AsyncClient", return_value=client):
        policy = await store.remove_quota_override("target-1", "heavy_media_checks")

    assert policy.quota("checks").limit == 41
    assert policy.quota("heavy_media_checks").limit != 9
    assert client.patch.await_args.kwargs["json"] == {
        "data": {"quota_overrides": '{"checks":41}'}
    }


def test_effective_policy_extends_existing_atomic_admission_plan(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("APP_ENV", "test")
    store = AppwriteTablesRateLimitStore("test-key")
    policy = effective_quota_policy("free", {"checks": 12, "heavy_media_checks": 4})

    plan = build_admission_plan(
        store,
        user_id="user-1",
        client_ip="203.0.113.1",
        account_created_at="2020-01-01T00:00:00+00:00",
        media_type="image",
        input_size=1,
        effective_policy=policy,
    )
    dimensions = {item.dimension: item for item in plan.dimensions}

    assert dimensions["subscription_checks_day"].limit == 12
    assert dimensions["subscription_heavy_media_day"].limit == 4


def test_system_admin_comes_only_from_runtime_account_and_server_allowlist(monkeypatch):
    monkeypatch.setenv("SYSTEM_ADMIN_USER_IDS", "admin-1")

    assert is_system_admin(
        {"$id": "admin-1", "email": "not-authoritative@example.test"}, "admin-1"
    )
    assert not is_system_admin(
        {"$id": "user-1", "isAdmin": True, "role": "admin"}, "user-1"
    )
    assert not is_system_admin({"$id": "admin-1"}, "user-1")


def test_admin_allowlist_is_fail_closed_for_empty_or_malformed_config(monkeypatch):
    monkeypatch.delenv("SYSTEM_ADMIN_USER_IDS", raising=False)
    assert configured_system_admin_ids() == frozenset()

    monkeypatch.setenv("SYSTEM_ADMIN_USER_IDS", " admin-1 , admin-1 ")
    assert configured_system_admin_ids() == frozenset({"admin-1"})

    monkeypatch.setenv("SYSTEM_ADMIN_USER_IDS", "admin-1,not valid")
    assert configured_system_admin_ids() == frozenset()
    assert not is_system_admin({"$id": "admin-1"}, "admin-1")


def test_spoofed_admin_fields_are_rejected_by_the_request_contract():
    with pytest.raises(SecurityValidationError):
        validate_request_payload(
            {
                "action": "admin_get_user_policy",
                "targetUserId": "target-1",
                "isAdmin": True,
            }
        )
    with pytest.raises(SecurityValidationError):
        validate_request_payload(
            {
                "action": "admin_get_user_policy",
                "targetUserId": "target-1",
                "role": "admin",
            }
        )
    with pytest.raises(SecurityValidationError):
        validate_request_payload(
            {
                "action": "admin_get_user_policy",
                "targetUserId": "target-1",
                "userId": "admin-1",
            }
        )


@pytest.mark.asyncio
async def test_normal_user_is_denied_even_when_targeting_an_admin(monkeypatch):
    monkeypatch.setenv("SYSTEM_ADMIN_USER_IDS", "admin-1")
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
            "src.main.ensure_user_profile", new=AsyncMock(return_value={"plan": "free"})
        ),
    ):
        with pytest.raises(SecurityValidationError) as raised:
            await _execute_request(
                {"action": "admin_get_user_policy", "targetUserId": "admin-1"},
                "runtime-key",
                "user-1",
                "runtime-jwt",
            )

    assert raised.value.code == "admin_access_denied"


@pytest.mark.asyncio
async def test_admin_action_uses_server_side_store_after_authorization(monkeypatch):
    monkeypatch.setenv("SYSTEM_ADMIN_USER_IDS", "admin-1")
    policy = EffectiveQuotaPolicy(
        "pro",
        {
            "checks": QuotaLimit("checks", "month", 100),
            "heavy_media_checks": QuotaLimit("heavy_media_checks", "month", 25),
        },
        {},
    )
    store = type("Store", (), {"change_subscription": AsyncMock(return_value=policy)})()
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
            {
                "action": "admin_set_subscription",
                "targetUserId": "target-1",
                "subscription": "pro",
            },
            "runtime-key",
            "admin-1",
            "runtime-jwt",
        )

    store.change_subscription.assert_awaited_once_with("admin-1", "target-1", "pro")
    assert result["subscription"] == "pro"
    assert result["effective_limits"]["checks"] == {"period": "month", "limit": 100}
