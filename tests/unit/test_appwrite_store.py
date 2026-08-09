"""Unit tests for trusted Appwrite TablesDB persistence."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.appwrite_store import (
    ensure_user_profile,
    get_authenticated_account,
    map_analysis_to_check_row,
    persist_check_result,
    serialize_check_details,
)


def _client_with(*responses):
    client = AsyncMock()
    client.get = AsyncMock(side_effect=[response for response in responses if response._method == "get"])
    client.post = AsyncMock(side_effect=[response for response in responses if response._method == "post"])
    client.patch = AsyncMock(side_effect=[response for response in responses if response._method == "patch"])
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _response(method: str, status: int, body: dict | None = None):
    response = MagicMock(status_code=status)
    response._method = method
    response.json.return_value = body or {}
    return response


def test_analysis_result_mapping_preserves_legacy_authenticity_behavior():
    result = {
        "verdict": "FAKE",
        "confidence": 0.81,
        "model_used": "sapling",
        "media_type": "text",
        "processing_ms": 123,
        "explanation": "summary",
        "fact_checks": [{"status": "ok"}],
    }

    row = map_analysis_to_check_row(result, "authenticated-user", "source")

    assert row["user_id"] == "authenticated-user"
    assert row["authenticity_index"] == 19
    assert row["provider"] == "sapling"
    assert row["model"] == "sapling"
    assert json.loads(row["details"]) == {"fact_checks": [{"status": "ok"}]}


@pytest.mark.parametrize("invalid", [None, "not-a-number", float("nan"), float("inf")])
def test_analysis_result_mapping_handles_invalid_confidence(invalid):
    row = map_analysis_to_check_row({"confidence": invalid}, "authenticated-user")
    assert row["authenticity_index"] == 0


@pytest.mark.parametrize("model", ["aiornot_text", "aiornot_audio"])
def test_analysis_result_mapping_persists_aiornot_model_under_canonical_provider(model):
    row = map_analysis_to_check_row({"model_used": model}, "authenticated-user")
    assert row["provider"] == "aiornot"
    assert row["model"] == model


def test_details_serialization_handles_enum_like_values():
    enum_like = MagicMock()
    enum_like.value = "safe-value"
    serialized = serialize_check_details({"custom": enum_like, "verdict": "REAL"})
    assert json.loads(serialized) == {"custom": "safe-value"}


@pytest.mark.asyncio
async def test_profile_creation_is_owner_readable_and_has_safe_defaults():
    client = _client_with(
        _response("get", 404),
        _response("post", 201, {"$id": "user-1"}),
    )
    account = {"$id": "user-1", "name": "User", "emailVerification": False}

    with patch("src.appwrite_store.httpx.AsyncClient", return_value=client):
        profile = await ensure_user_profile(account, "dynamic-key")

    assert profile["$id"] == "user-1"
    body = client.post.await_args.kwargs["json"]
    assert body["rowId"] == "user-1"
    assert body["data"]["plan"] == "free"
    assert body["data"]["checks_count"] == 0
    assert body["permissions"] == ['read("user:user-1")']


@pytest.mark.asyncio
async def test_profile_creation_is_idempotent_when_row_exists():
    client = _client_with(_response("get", 200, {"$id": "user-1", "email_verified": False}))
    with patch("src.appwrite_store.httpx.AsyncClient", return_value=client):
        profile = await ensure_user_profile({"$id": "user-1"}, "dynamic-key")
    assert profile["$id"] == "user-1"
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_creation_recovers_from_concurrent_create():
    client = _client_with(
        _response("get", 404),
        _response("post", 409),
        _response("get", 200, {"$id": "user-1", "email_verified": False}),
    )
    with patch("src.appwrite_store.httpx.AsyncClient", return_value=client):
        profile = await ensure_user_profile({"$id": "user-1"}, "dynamic-key")
    assert profile["$id"] == "user-1"
    assert client.get.await_count == 2


@pytest.mark.parametrize(("stored", "authoritative"), [(False, True), (True, False)])
@pytest.mark.asyncio
async def test_profile_mirrors_authoritative_email_verification(stored, authoritative):
    client = _client_with(
        _response("get", 200, {"$id": "user-1", "email_verified": stored}),
        _response("patch", 200, {"$id": "user-1", "email_verified": authoritative}),
    )
    account = {"$id": "user-1", "emailVerification": authoritative}

    with patch("src.appwrite_store.httpx.AsyncClient", return_value=client):
        profile = await ensure_user_profile(account, "dynamic-key")

    assert profile["email_verified"] is authoritative
    assert client.patch.await_args.kwargs["json"] == {
        "data": {"email_verified": authoritative}
    }


@pytest.mark.asyncio
async def test_authenticated_account_rejects_foreign_runtime_identity():
    client = _client_with(_response("get", 200, {"$id": "other-user"}))
    with patch("src.appwrite_store.httpx.AsyncClient", return_value=client):
        with pytest.raises(RuntimeError, match="context mismatch"):
            await get_authenticated_account("runtime-user", "runtime-jwt")


@pytest.mark.asyncio
async def test_account_error_does_not_expose_response_body():
    response = _response("get", 401, {"secret": "must-not-leak"})
    client = _client_with(response)
    with patch("src.appwrite_store.httpx.AsyncClient", return_value=client):
        with pytest.raises(RuntimeError) as exc_info:
            await get_authenticated_account("runtime-user", "runtime-jwt")
    assert "must-not-leak" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_check_creation_uses_exact_owner_permissions():
    client = _client_with(
        _response("post", 201, {"$id": "check-1"}),
        _response("patch", 200),
        _response("patch", 200),
    )
    with patch("src.appwrite_store.httpx.AsyncClient", return_value=client):
        await persist_check_result(
            {"verdict": "REAL", "confidence": 0.2, "media_type": "text"},
            "user-1",
            "source",
            "dynamic-key",
        )

    body = client.post.await_args.kwargs["json"]
    assert body["data"]["user_id"] == "user-1"
    assert body["permissions"] == [
        'read("user:user-1")',
        'delete("user:user-1")',
    ]
