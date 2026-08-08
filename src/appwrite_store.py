"""Server-side Appwrite TablesDB persistence for Function executions."""

from __future__ import annotations

import json
import math
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx


DEFAULT_ENDPOINT = "https://fra.cloud.appwrite.io/v1"
DEFAULT_PROJECT_ID = "6a67d79d000fcca992f3"
DEFAULT_DATABASE_ID = "yav"
DEFAULT_USERS_TABLE_ID = "users"
DEFAULT_CHECKS_TABLE_ID = "checks"


def _resource_config() -> tuple[str, str, str, str, str]:
    endpoint = os.getenv("APPWRITE_FUNCTION_API_ENDPOINT", DEFAULT_ENDPOINT).rstrip("/")
    project_id = os.getenv("APPWRITE_FUNCTION_PROJECT_ID", DEFAULT_PROJECT_ID)
    database_id = os.getenv("APPWRITE_DATABASE_ID", DEFAULT_DATABASE_ID)
    users_table_id = os.getenv("APPWRITE_USERS_TABLE_ID", DEFAULT_USERS_TABLE_ID)
    checks_table_id = os.getenv("APPWRITE_CHECKS_TABLE_ID", DEFAULT_CHECKS_TABLE_ID)
    return endpoint, project_id, database_id, users_table_id, checks_table_id


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _legacy_authenticity_index(result: dict[str, Any]) -> int:
    """Preserve the current UI conversion until confidence semantics are fixed in BE-06."""
    # TODO(BE-06): replace this legacy AI-probability inversion only with an agreed contract.
    raw = result.get("ai_confidence", result.get("confidence", 0))
    try:
        confidence = float(raw)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(confidence):
        return 0
    ai_probability = confidence * 100 if confidence <= 1 else confidence
    return max(0, min(100, round(100 - ai_probability)))


def _provider_for_model(model: str) -> str | None:
    normalized = model.lower()
    if normalized.startswith("sightengine"):
        return "sightengine"
    if normalized.startswith("resemble"):
        return "resemble"
    if normalized.startswith("sapling"):
        return "sapling"
    if normalized.startswith("hf_"):
        return "huggingface"
    if normalized.startswith(("gpt-", "gpt_", "command-")) or "g4f" in normalized:
        return "g4f"
    return None


def serialize_check_details(result: dict[str, Any]) -> str:
    """Serialize non-column analysis fields as a JSON string."""
    stored_columns = {
        "verdict",
        "ai_verdict",
        "confidence",
        "ai_confidence",
        "model_used",
        "explanation",
        "media_type",
        "processing_ms",
        "check_id",
    }
    details = {key: value for key, value in result.items() if key not in stored_columns}
    return json.dumps(details, ensure_ascii=False, default=_value, separators=(",", ":"))


def map_analysis_to_check_row(
    result: dict[str, Any], user_id: str, source_label: str = ""
) -> dict[str, Any]:
    """Map an analysis response to the trusted checks table schema."""
    model = str(_value(result.get("model_used")) or "")
    verdict = str(_value(result.get("ai_verdict") or result.get("verdict")) or "UNCERTAIN")
    media_type = str(_value(result.get("media_type")) or "text")
    explanation = result.get("explanation")

    return {
        "user_id": user_id,
        "media_type": media_type[:16],
        "status": "completed",
        "verdict": verdict[:24],
        "authenticity_index": _legacy_authenticity_index(result),
        "provider": _provider_for_model(model),
        "model": model[:128] or None,
        "explanation": str(explanation) if explanation is not None else None,
        "source_label": str(source_label)[:255] or None,
        "processing_ms": int(result.get("processing_ms") or 0),
        "details": serialize_check_details(result),
    }


def _user_permissions(user_id: str, *, allow_delete: bool) -> list[str]:
    permissions = [f'read("user:{user_id}")']
    if allow_delete:
        permissions.append(f'delete("user:{user_id}")')
    return permissions


async def get_authenticated_account(user_id: str, user_jwt: str) -> dict[str, Any]:
    """Resolve the invoking Appwrite account and verify its runtime user ID."""
    endpoint, project_id, _, _, _ = _resource_config()
    headers = {"X-Appwrite-Project": project_id, "X-Appwrite-JWT": user_jwt}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(f"{endpoint}/account", headers=headers)
    if response.status_code != 200:
        raise RuntimeError(f"Authenticated account lookup failed ({response.status_code})")
    account = response.json()
    if str(account.get("$id") or "") != user_id:
        raise RuntimeError("Authenticated user context mismatch")
    return account


async def ensure_user_profile(account: dict[str, Any], api_key: str) -> dict[str, Any]:
    """Create a profile and mirror the authoritative Auth verification state."""
    endpoint, project_id, database_id, users_table_id, _ = _resource_config()
    user_id = str(account.get("$id") or "")
    if not user_id:
        raise RuntimeError("Authenticated account has no user ID")

    encoded_user = quote(user_id, safe="")
    base_url = f"{endpoint}/tablesdb/{database_id}/tables/{users_table_id}/rows"
    headers = {"X-Appwrite-Project": project_id, "X-Appwrite-Key": api_key}
    email_verified = account.get("emailVerification") is True

    async def _sync_existing(client: httpx.AsyncClient, row: dict[str, Any]) -> dict[str, Any]:
        if row.get("email_verified") is email_verified:
            return row
        updated = await client.patch(
            f"{base_url}/{encoded_user}",
            headers=headers,
            json={"data": {"email_verified": email_verified}},
        )
        if updated.status_code != 200:
            raise RuntimeError(f"Profile verification sync failed ({updated.status_code})")
        return updated.json()

    async with httpx.AsyncClient(timeout=15.0) as client:
        existing = await client.get(f"{base_url}/{encoded_user}", headers=headers)
        if existing.status_code == 200:
            return await _sync_existing(client, existing.json())
        if existing.status_code != 404:
            raise RuntimeError(f"Profile lookup failed ({existing.status_code})")

        response = await client.post(
            base_url,
            headers=headers,
            json={
                "rowId": user_id,
                "data": {
                    "name": str(account.get("name") or "Пользователь")[:128],
                    "plan": "free",
                    "status": "active",
                    "email_verified": email_verified,
                    "checks_count": 0,
                    "last_check_at": None,
                },
                "permissions": _user_permissions(user_id, allow_delete=False),
            },
        )
        if response.status_code == 409:
            existing = await client.get(f"{base_url}/{encoded_user}", headers=headers)
            if existing.status_code == 200:
                return await _sync_existing(client, existing.json())
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Profile creation failed ({response.status_code})")
    return response.json()


async def persist_check_result(
    result: dict[str, Any], user_id: str, source_label: str, api_key: str
) -> str:
    """Create an owner-readable/deletable trusted check row and update profile stats."""
    endpoint, project_id, database_id, users_table_id, checks_table_id = _resource_config()
    check_id = uuid.uuid4().hex
    headers = {"X-Appwrite-Project": project_id, "X-Appwrite-Key": api_key}
    checks_url = f"{endpoint}/tablesdb/{database_id}/tables/{checks_table_id}/rows"
    users_url = f"{endpoint}/tablesdb/{database_id}/tables/{users_table_id}/rows"
    encoded_user = quote(user_id, safe="")
    now = datetime.now(timezone.utc).isoformat()

    async with httpx.AsyncClient(timeout=15.0) as client:
        created = await client.post(
            checks_url,
            headers=headers,
            json={
                "rowId": check_id,
                "data": map_analysis_to_check_row(result, user_id, source_label),
                "permissions": _user_permissions(user_id, allow_delete=True),
            },
        )
        if created.status_code not in (200, 201):
            raise RuntimeError(f"Check history write failed ({created.status_code})")

        incremented = await client.patch(
            f"{users_url}/{encoded_user}/checks_count/increment",
            headers=headers,
            json={"value": 1},
        )
        if incremented.status_code != 200:
            raise RuntimeError(f"Profile checks_count update failed ({incremented.status_code})")

        updated = await client.patch(
            f"{users_url}/{encoded_user}",
            headers=headers,
            json={"data": {"last_check_at": now}},
        )
        if updated.status_code != 200:
            raise RuntimeError(f"Profile last_check_at update failed ({updated.status_code})")

    return check_id
