"""Focused production-MVP admission plan coverage (no external providers)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config import settings
from src.rate_limit import (
    AdmissionDimension,
    AdmissionPlan,
    AppwriteTablesRateLimitStore,
    ComplexAdmissionInput,
    RateLimitError,
    Window,
    build_admission_plan,
    build_complex_admission_plan,
    build_source_media_admission_plan,
    build_subscription_admission_plan,
)
from src.subscriptions import effective_quota_policy
from src.validation import SecurityValidationError


def _store(monkeypatch, now: datetime) -> AppwriteTablesRateLimitStore:
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project")
    monkeypatch.setenv("RATE_LIMIT_IP_HMAC_KEY", "test-secret")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    return AppwriteTablesRateLimitStore("server-key", now=now)


def _plan(monkeypatch, *, media_type="text", text="short", input_size=None, hybrid=False, created=None):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    return build_admission_plan(
        _store(monkeypatch, now), user_id="user", client_ip="192.0.2.1",
        account_created_at=(created or now - timedelta(hours=1)).isoformat(), media_type=media_type,
        text=text, input_size=len(text) if input_size is None else input_size, hybrid=hybrid,
    )


def test_new_user_short_text_reserves_one_check_and_two_gemini_operations(monkeypatch):
    plan = _plan(monkeypatch)
    dimensions = {item.dimension: item for item in plan.dimensions}
    assert {"ip_total_daily", "new_user_total_daily", "new_user_total_first7d", "new_user_text_daily", "global_gemini_daily"} <= set(dimensions)
    assert dimensions["global_gemini_daily"].units == 2
    assert plan.units_for("gemini") == 2


def test_long_text_uses_actual_aiornot_words_and_one_gemini_operation(monkeypatch):
    text = "word " * 64
    plan = _plan(monkeypatch, text=text)
    dimensions = {item.dimension: item for item in plan.dimensions}
    assert dimensions["global_aiornot_words_daily"].units == 64
    assert dimensions["global_aiornot_words_monthly"].units == 64
    assert dimensions["global_gemini_daily"].units == 1
    assert plan.units_for("aiornot") == 64
    assert plan.units_for("gemini") == 1


def test_complex_text_has_no_sapling_or_aiornot_and_exactly_two_gemini_operations(monkeypatch):
    plan = _plan(monkeypatch, text="word " * 100, hybrid=True)
    names = {item.dimension for item in plan.dimensions}
    assert plan.units_for("gemini") == 2
    assert plan.units_for("sapling") == 0
    assert plan.units_for("aiornot") == 0
    assert "global_gemini_daily" in names


@pytest.mark.parametrize(
    ("has_image", "has_video", "expected"),
    [
        (False, False, set()),
        (True, False, {"ip_heavy_media_daily", "new_user_image_daily"}),
        (True, True, {"ip_heavy_media_daily", "new_user_image_daily", "new_user_video_first7d"}),
        (False, True, {"ip_heavy_media_daily", "new_user_video_first7d"}),
    ],
)
def test_source_post_extraction_plan_charges_known_media_once_without_duplicate_check_dimensions(monkeypatch, has_image, has_video, expected):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    plan = build_source_media_admission_plan(
        _store(monkeypatch, now), user_id="user", client_ip="192.0.2.1",
        account_created_at=(now - timedelta(hours=1)).isoformat(), has_image=has_image, has_video=has_video,
    )
    names = [item.dimension for item in plan.dimensions]
    assert set(names) == expected
    assert len(names) == len(set(names))
    assert not {"ip_total_daily", "new_user_total_daily", "new_user_total_first7d", "new_user_hybrid_daily"} & set(names)


@pytest.mark.parametrize(
    ("media_type", "dimension"),
    [("image", "new_user_image_daily"), ("audio", "new_user_audio_72h"), ("video", "new_user_video_first7d")],
)
def test_new_user_media_plan_has_type_and_shared_heavy_ip_dimensions(monkeypatch, media_type, dimension):
    plan = _plan(monkeypatch, media_type=media_type, input_size=1)
    names = {item.dimension for item in plan.dimensions}
    assert {"ip_total_daily", "ip_heavy_media_daily", "new_user_total_daily", "new_user_total_first7d", dimension} <= names


def test_old_user_has_no_new_user_dimensions(monkeypatch):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    plan = _plan(monkeypatch, created=now - timedelta(days=settings.new_user_period_days + 1))
    assert not any(item.dimension.startswith("new_user_") for item in plan.dimensions)
    assert {item.dimension for item in plan.dimensions} == {"ip_total_daily", "global_gemini_daily"}


def test_unlimited_user_skips_user_and_ip_dimensions_but_keeps_provider_budget(monkeypatch):
    monkeypatch.setenv("UNLIMITED_USER_IDS", "trusted-user,other-user")
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    store = _store(monkeypatch, now)
    plan = build_admission_plan(
        store, user_id="trusted-user", client_ip="192.0.2.1",
        account_created_at=(now - timedelta(hours=1)).isoformat(), media_type="text",
        input_size=100, text="word " * 20, hybrid=True,
    )
    assert {item.dimension for item in plan.dimensions} == {"global_gemini_daily"}
    assert plan.units_for("gemini") == 2


@pytest.mark.parametrize(("has_image", "has_video"), [(True, False), (False, True), (True, True)])
def test_unlimited_source_post_extraction_has_no_user_or_ip_dimensions(monkeypatch, has_image, has_video):
    monkeypatch.setenv("UNLIMITED_USER_IDS", "trusted-user")
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    plan = build_source_media_admission_plan(
        _store(monkeypatch, now), user_id="trusted-user", client_ip="192.0.2.1",
        account_created_at=(now - timedelta(hours=1)).isoformat(), has_image=has_image, has_video=has_video,
    )
    assert plan.dimensions == ()


@pytest.mark.parametrize(
    ("media_type", "expected_provider"),
    [
        ("text", "global_gemini_daily"),
        ("image", "global_sightengine_daily"),
        ("video", "global_gemini_daily"),
    ],
)
def test_workspace_admission_uses_shared_subscription_scope_and_actor_protection(
    monkeypatch, media_type, expected_provider
):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    plan = build_admission_plan(
        _store(monkeypatch, now),
        user_id="member-a",
        workspace_id="workspace-1",
        client_ip="192.0.2.1",
        account_created_at=(now - timedelta(hours=1)).isoformat(),
        media_type=media_type,
        input_size=100,
        text="workspace analysis text",
        effective_policy=effective_quota_policy("free"),
    )
    dimensions = {item.dimension: item for item in plan.dimensions}

    assert plan.user_id == "member-a"
    assert plan.workspace_id == "workspace-1"
    assert dimensions["workspace_checks_day"].subject == "workspace-1"
    assert not any(name.startswith("subscription_") for name in dimensions)
    assert {"ip_total_daily", "new_user_total_daily", expected_provider} <= set(dimensions)
    workspace_dimensions = [
        item for item in plan.dimensions if item.dimension.startswith("workspace_")
    ]
    assert all(
        len(item.dimension) <= 32 and len(item.subject) <= 36
        for item in workspace_dimensions
    )


def test_workspace_members_share_one_counter_but_workspaces_are_isolated(monkeypatch):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    store = _store(monkeypatch, now)
    common = {
        "client_ip": "192.0.2.1",
        "account_created_at": (now - timedelta(days=30)).isoformat(),
        "media_type": "text",
        "input_size": 20,
        "text": "workspace analysis text",
        "effective_policy": effective_quota_policy("free"),
    }
    member_a = build_admission_plan(
        store, user_id="member-a", workspace_id="workspace-a", **common
    )
    member_b = build_admission_plan(
        store, user_id="member-b", workspace_id="workspace-a", **common
    )
    workspace_b = build_admission_plan(
        store, user_id="member-a", workspace_id="workspace-b", **common
    )
    personal = build_admission_plan(store, user_id="workspace-a", **common)

    def counter_id(plan):
        counter = next(item for item in plan.dimensions if item.dimension == "workspace_checks_day")
        return store._row_id(counter.dimension, counter.subject, counter.window.key)

    assert counter_id(member_a) == counter_id(member_b)
    assert counter_id(member_a) != counter_id(workspace_b)
    assert counter_id(member_a) != store._row_id(
        "subscription_checks_day", "workspace-a", "2026-08-09"
    )
    assert any(item.dimension == "subscription_checks_day" for item in personal.dimensions)


def test_workspace_source_media_uses_shared_heavy_counter_once(monkeypatch):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    plan = build_source_media_admission_plan(
        _store(monkeypatch, now),
        user_id="member-a",
        workspace_id="workspace-1",
        client_ip="192.0.2.1",
        account_created_at=(now - timedelta(hours=1)).isoformat(),
        has_image=True,
        has_video=False,
        effective_policy=effective_quota_policy("free"),
    )
    names = [item.dimension for item in plan.dimensions]

    assert names.count("workspace_heavy_media_day") == 1
    assert "subscription_heavy_media_day" not in names
    assert "workspace_checks_day" not in names
    assert "ip_heavy_media_daily" in names
    assert "new_user_image_daily" in names


def test_workspace_complex_subscription_plan_has_no_personal_counter(monkeypatch):
    plan = build_subscription_admission_plan(
        _store(monkeypatch, datetime(2026, 8, 9, 12, tzinfo=timezone.utc)),
        user_id="member-a",
        workspace_id="workspace-1",
        policy=effective_quota_policy("free"),
    )

    assert plan.workspace_id == "workspace-1"
    assert [(item.dimension, item.subject) for item in plan.dimensions] == [
        ("workspace_checks_day", "workspace-1")
    ]


def _complex_plan(monkeypatch, inputs, *, workspace_id=None):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    return build_complex_admission_plan(
        _store(monkeypatch, now),
        user_id="member-a",
        client_ip="192.0.2.1",
        account_created_at=(now - timedelta(hours=1)).isoformat(),
        inputs=tuple(inputs),
        effective_policy=effective_quota_policy("free"),
        workspace_id=workspace_id,
    )


@pytest.mark.parametrize(
    ("workspace_id", "checks_dimension", "checks_subject"),
    [(None, "subscription_checks_day", "member-a"), ("workspace-1", "workspace_checks_day", "workspace-1")],
)
def test_complex_text_has_one_check_and_full_actor_protection(monkeypatch, workspace_id, checks_dimension, checks_subject):
    plan = _complex_plan(
        monkeypatch,
        [ComplexAdmissionInput("text", 12, text="complex text", hybrid=True)],
        workspace_id=workspace_id,
    )
    dimensions = {item.dimension: item for item in plan.dimensions}

    assert dimensions[checks_dimension].subject == checks_subject
    assert len([item for item in plan.dimensions if item.dimension.endswith("checks_day")]) == 1
    if workspace_id:
        assert not any(
            item.dimension.startswith("subscription_")
            for item in plan.dimensions
        )
    else:
        assert not any(
            item.dimension.startswith("workspace_")
            for item in plan.dimensions
        )
    assert {"ip_total_daily", "new_user_total_daily", "new_user_total_first7d", "new_user_hybrid_daily"} <= set(dimensions)
    assert plan.provider_units == ()
    assert not any(item.dimension.startswith("global_") for item in plan.dimensions)


@pytest.mark.parametrize(
    ("media_type", "new_user_dimension"),
    [
        ("image", "new_user_image_daily"),
        ("audio", "new_user_audio_72h"),
        ("video", "new_user_video_first7d"),
    ],
)
@pytest.mark.parametrize("workspace_id", [None, "workspace-1"])
def test_complex_manual_media_has_one_heavy_admission_and_actor_protection(monkeypatch, media_type, new_user_dimension, workspace_id):
    plan = _complex_plan(
        monkeypatch,
        [ComplexAdmissionInput(media_type, 1)],
        workspace_id=workspace_id,
    )
    names = [item.dimension for item in plan.dimensions]
    heavy_dimension = "workspace_heavy_media_day" if workspace_id else "subscription_heavy_media_day"

    assert names.count("workspace_checks_day" if workspace_id else "subscription_checks_day") == 1
    assert names.count(heavy_dimension) == 1
    assert {"ip_total_daily", "ip_heavy_media_daily", "new_user_total_daily", "new_user_total_first7d", new_user_dimension} <= set(names)
    if workspace_id:
        assert not any(name.startswith("subscription_") for name in names)
    else:
        assert not any(name.startswith("workspace_") for name in names)


def test_complex_mixed_known_inputs_deduplicate_request_and_heavy_dimensions(monkeypatch):
    plan = _complex_plan(
        monkeypatch,
        [
            ComplexAdmissionInput("text", 12, text="complex text", hybrid=True),
            ComplexAdmissionInput("image", 1),
            ComplexAdmissionInput("audio", 1),
            ComplexAdmissionInput("image", 1),
        ],
    )
    names = [item.dimension for item in plan.dimensions]

    assert names.count("subscription_checks_day") == 1
    assert names.count("subscription_heavy_media_day") == 1
    assert names.count("ip_total_daily") == 1
    assert names.count("ip_heavy_media_daily") == 1
    assert {"new_user_hybrid_daily", "new_user_image_daily", "new_user_audio_72h"} <= set(names)


def test_complex_source_and_manual_media_keep_initial_and_secondary_heavy_units_separate(monkeypatch):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    store = _store(monkeypatch, now)
    plan = build_complex_admission_plan(
        store,
        user_id="member-a",
        client_ip="192.0.2.1",
        account_created_at=(now - timedelta(hours=1)).isoformat(),
        inputs=(
            ComplexAdmissionInput("text", 24, hybrid=True),
            ComplexAdmissionInput("image", 1),
        ),
        effective_policy=effective_quota_policy("free"),
    )
    discovered_source_plan = build_source_media_admission_plan(
        store,
        user_id="member-a",
        client_ip="192.0.2.1",
        account_created_at=(now - timedelta(hours=1)).isoformat(),
        has_image=True,
        has_video=False,
        effective_policy=effective_quota_policy("free"),
    )
    names = [item.dimension for item in plan.dimensions]
    discovered_names = [item.dimension for item in discovered_source_plan.dimensions]

    assert names.count("subscription_checks_day") == 1
    assert names.count("subscription_heavy_media_day") == 1
    assert names.count("ip_heavy_media_daily") == 1
    assert discovered_names.count("subscription_heavy_media_day") == 1
    assert "subscription_checks_day" not in discovered_names
    assert "ip_total_daily" not in discovered_names


@pytest.mark.parametrize(
    ("input_item", "expected_error"),
    [
        (ComplexAdmissionInput("text", settings.new_user_hybrid_max_chars + 1, hybrid=True), "text_too_long"),
        (ComplexAdmissionInput("image", settings.new_user_image_max_bytes + 1), "file_too_large"),
    ],
)
def test_complex_reuses_ordinary_new_user_input_size_validation(monkeypatch, input_item, expected_error):
    with pytest.raises(SecurityValidationError) as exc_info:
        _complex_plan(monkeypatch, [input_item])
    assert exc_info.value.code == expected_error


def test_complex_manual_file_matches_ordinary_admission_except_dynamic_provider_budget(monkeypatch):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    store = _store(monkeypatch, now)
    common = {
        "user_id": "member-a",
        "client_ip": "192.0.2.1",
        "account_created_at": (now - timedelta(hours=1)).isoformat(),
        "media_type": "image",
        "input_size": 1,
        "effective_policy": effective_quota_policy("free"),
    }
    ordinary = build_admission_plan(store, **common)
    complex_plan = build_complex_admission_plan(
        store,
        inputs=(ComplexAdmissionInput("image", 1),),
        user_id="member-a",
        client_ip="192.0.2.1",
        account_created_at=(now - timedelta(hours=1)).isoformat(),
        effective_policy=effective_quota_policy("free"),
    )

    def identities(plan):
        return {
            (item.dimension, item.subject, item.window.key)
            for item in plan.dimensions
            if not item.dimension.startswith("global_")
        }

    assert identities(complex_plan) == identities(ordinary)
    assert complex_plan.provider_units == ()


@pytest.mark.parametrize(
    ("media_type", "hybrid", "size"),
    [
        ("text", False, settings.new_user_text_max_chars + 1),
        ("text", True, settings.new_user_hybrid_max_chars + 1),
        ("image", False, settings.new_user_image_max_bytes + 1),
        ("audio", False, settings.new_user_audio_max_bytes + 1),
        ("video", False, settings.new_user_video_max_bytes + 1),
    ],
)
def test_new_user_size_overflow_is_rejected_before_admission(monkeypatch, media_type, hybrid, size):
    with pytest.raises(SecurityValidationError):
        _plan(monkeypatch, media_type=media_type, hybrid=hybrid, input_size=size, text="x" * min(size, 3001))


def _response(status: int, body=None):
    response = MagicMock(status_code=status)
    response.json.return_value = body or {}
    return response


@pytest.mark.asyncio
async def test_admit_stages_every_dimension_and_one_reservation_in_one_transaction(monkeypatch):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    store = _store(monkeypatch, now)
    window = Window("2026-08-09", now + timedelta(days=1))
    plan = AdmissionPlan("user", (
        AdmissionDimension("user", "user", window, 1, 4, "daily_quota_exceeded", "safe"),
        AdmissionDimension("gemini", "global", window, 2, 100, "provider_temporarily_unavailable", "safe"),
    ))
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=[_response(404), _response(404)])
    client.post = AsyncMock(side_effect=[_response(201, {"$id": "tx"}), _response(201), _response(201), _response(201)])
    client.patch = AsyncMock(return_value=_response(200))

    with patch("src.rate_limit.httpx.AsyncClient", return_value=client):
        await store.admit(plan)

    assert client.get.await_count == 2
    assert all(call.kwargs["params"] == {"transactionId": "tx"} for call in client.get.await_args_list)
    staged = client.post.await_args_list[1:]
    assert all(call.kwargs["json"]["transactionId"] == "tx" for call in staged)
    assert client.patch.await_args_list[-1].kwargs["json"] == {"commit": True}


@pytest.mark.asyncio
async def test_workspace_admission_reservation_keeps_actor_and_workspace_scope(monkeypatch):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    store = _store(monkeypatch, now)
    window = Window("2026-08-09", now + timedelta(days=1))
    plan = AdmissionPlan(
        "member-a",
        (AdmissionDimension("workspace_checks_day", "workspace-1", window, 1, 4, "workspace_daily_quota_exceeded", "safe"),),
        workspace_id="workspace-1",
    )
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=_response(404))
    client.post = AsyncMock(
        side_effect=[_response(201, {"$id": "tx"}), _response(201), _response(201)]
    )
    client.patch = AsyncMock(return_value=_response(200))

    with patch("src.rate_limit.httpx.AsyncClient", return_value=client):
        await store.admit(plan)

    assert client.post.await_args_list[2].kwargs["json"]["data"] == {
        "user_id": "member-a",
        "workspace_id": "workspace-1",
        "quota_dimension": "admission",
        "window_start": "2026-08-09",
        "state": "consumed",
    }


@pytest.mark.asyncio
async def test_workspace_counter_exhaustion_is_a_controlled_shared_quota_error(monkeypatch):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    store = _store(monkeypatch, now)
    window = Window("2026-08-09", now + timedelta(days=1))
    plan = AdmissionPlan(
        "member-b",
        (AdmissionDimension("workspace_checks_day", "workspace-1", window, 1, 1, "workspace_daily_quota_exceeded", "safe"),),
        workspace_id="workspace-1",
    )
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=_response(200, {"count": 1}))
    client.post = AsyncMock(return_value=_response(201, {"$id": "tx"}))
    client.patch = AsyncMock(
        side_effect=[_response(400, {"type": "row_max_exceeded"}), _response(200)]
    )

    with patch("src.rate_limit.httpx.AsyncClient", return_value=client), pytest.raises(
        RateLimitError
    ) as raised:
        await store.admit(plan)

    assert raised.value.code == "workspace_daily_quota_exceeded"
    assert raised.value.status_code == 429
    assert any(
        call.kwargs["json"] == {"rollback": True}
        for call in client.patch.await_args_list
    )


@pytest.mark.asyncio
async def test_late_dimension_denial_rolls_back_without_commit(monkeypatch):
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    store = _store(monkeypatch, now)
    window = Window("2026-08-09", now + timedelta(days=1))
    plan = AdmissionPlan("user", (
        AdmissionDimension("user", "user", window, 1, 4, "daily_quota_exceeded", "safe"),
        AdmissionDimension("gemini", "global", window, 2, 2, "provider_temporarily_unavailable", "safe"),
    ))
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=[_response(404), _response(200, {"count": 0})])
    client.post = AsyncMock(side_effect=[_response(201, {"$id": "tx"}), _response(201)])
    client.patch = AsyncMock(side_effect=[_response(400, {"type": "row_max_exceeded"}), _response(200)])

    with patch("src.rate_limit.httpx.AsyncClient", return_value=client), pytest.raises(RateLimitError) as raised:
        await store.admit(plan)

    assert raised.value.code == "provider_temporarily_unavailable"
    assert any(call.kwargs["json"] == {"rollback": True} for call in client.patch.await_args_list)
    assert not any(call.kwargs["json"] == {"commit": True} for call in client.patch.await_args_list)
