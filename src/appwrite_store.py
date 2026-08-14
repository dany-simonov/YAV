"""Server-side Appwrite TablesDB persistence for Function executions."""

from __future__ import annotations

import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from api.schemas import AnalysisResult, HybridAnalysisResponse
from core.enums import MediaType, ModelUsed, ScoreKind, Verdict
from core.result_normalization import authenticity_index_from_ai_probability
from src.validation import (
    MAX_DETAILS_BYTES,
    MAX_EXPLANATION,
    MAX_MODEL,
    MAX_PROVIDER,
    MAX_SOURCE_LABEL,
)
from src.execution_deadline import bounded_persistence_timeout, current_execution_deadline


DEFAULT_ENDPOINT = "https://fra.cloud.appwrite.io/v1"
DEFAULT_PROJECT_ID = "6a67d79d000fcca992f3"
DEFAULT_DATABASE_ID = "yav"
DEFAULT_USERS_TABLE_ID = "users"
DEFAULT_CHECKS_TABLE_ID = "checks"


def _appwrite_failure_metadata(response: Any | None, exc: BaseException | None = None) -> tuple[Any, Any, Any]:
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


def _safe_appwrite_message(response: Any | None, sensitive_values: tuple[str, ...]) -> str | None:
    """Extract only bounded validation text, never a response body or credentials."""
    if response is None:
        return None
    try:
        body = response.json()
    except (TypeError, ValueError):
        return None
    message = body.get("message") if isinstance(body, dict) else None
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


class ChecksPersistenceError(RuntimeError):
    """Safe metadata carrier for Function-side checks persistence diagnostics."""

    def __init__(
        self, operation: str, *, response: Any | None = None, exc: BaseException | None = None,
        data: dict[str, Any] | None = None, user_id: str = "", api_key: str = "",
    ) -> None:
        self.operation = operation
        self.exception_class = type(exc).__name__ if exc is not None else None
        self.status_code, self.appwrite_type, self.appwrite_code = _appwrite_failure_metadata(response, exc)
        values = data or {}
        self.data_keys = ",".join(sorted(values))
        self.field_types = ",".join(
            f"{key}:{type(value).__name__}" for key, value in sorted(values.items())
        )
        self.string_lengths = ",".join(
            f"{key}:{len(value)}" for key, value in sorted(values.items()) if isinstance(value, str)
        )
        sensitive_values = (user_id, api_key, *(value for value in values.values() if isinstance(value, str)))
        self.appwrite_message = _safe_appwrite_message(response, sensitive_values)
        super().__init__(operation)


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
    if normalized.startswith("gemini"):
        return "gemini"
    if normalized.startswith("hf_"):
        return "huggingface"
    if normalized.startswith("aiornot"):
        return "aiornot"
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
    try:
        encoded = json.dumps(
            details,
            ensure_ascii=False,
            default=_value,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return '{"truncated":true}'
    if len(encoded.encode("utf-8")) > MAX_DETAILS_BYTES:
        return '{"truncated":true}'
    return encoded


def _serialize_canonical_details(result: AnalysisResult) -> str:
    """Serialize only contract-validated, curated v2 provider evidence."""
    evidence = result.provider_evidence
    details: dict[str, Any] = {}
    evidence_details = evidence.model_dump(mode="json") if evidence is not None else {}
    if result.component_evidence:
        evidence_details["components"] = [
            {
                "provider": component.evidence.provider,
                "model": component.evidence.model,
                "verdict": component.verdict.value,
                "score_kind": component.evidence.score_kind.value,
                "score": component.evidence.raw_score,
                "predicted_label": component.evidence.predicted_label,
                "safe_details": component.evidence.safe_details,
            }
            for component in result.component_evidence
        ]
    if evidence_details:
        details["provider_evidence_v2"] = evidence_details
    if result.short_report is not None:
        details["short_report"] = result.short_report
    if result.analysis_mode is not None:
        details["analysis_mode"] = result.analysis_mode
    # `confidence` is the canonical AI-origin model confidence. It is distinct
    # from authenticity_index and is retained in details because the checks
    # table has no dedicated confidence column.
    if result.ai_status != "unavailable":
        details["ai_confidence"] = result.confidence
    if result.ai_details is not None:
        details["ai_details"] = result.ai_details.model_dump(mode="json")
    if result.ai_status == "unavailable":
        details["ai_status"] = result.ai_status
    if result.credibility is not None:
        details["credibility"] = result.credibility.model_dump(mode="json")
    return _serialize_canonical_details_bounded(details)


def _encode_details(details: dict[str, Any]) -> str | None:
    """Encode once and enforce the TablesDB byte limit, including UTF-8."""
    try:
        encoded = json.dumps(details, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return None
    return encoded if len(encoded.encode("utf-8")) <= MAX_DETAILS_BYTES else None


def _compact_credibility_sources(
    credibility: dict[str, Any], kept_indexes: list[int],
) -> dict[str, Any]:
    """Keep selected sources and deterministically remap all issue refs."""
    sources = credibility.get("sources")
    issues = credibility.get("issues")
    if not isinstance(sources, list) or not isinstance(issues, list):
        return credibility
    mapping = {old: new for new, old in enumerate(kept_indexes)}
    compacted = dict(credibility)
    compacted["sources"] = [sources[index] for index in kept_indexes if 0 <= index < len(sources)]
    repaired_issues: list[dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        refs = issue.get("source_refs")
        canonical_refs = sorted({
            mapping[ref]
            for ref in refs if isinstance(ref, int) and not isinstance(ref, bool) and ref in mapping
        }) if isinstance(refs, list) else []
        repaired = dict(issue)
        repaired["source_refs"] = canonical_refs
        repaired_issues.append(repaired)
    compacted["issues"] = repaired_issues
    return compacted


def _credibility_priority_indexes(credibility: dict[str, Any]) -> list[int]:
    """Prefer sources actually cited by retained issues, then source order."""
    sources = credibility.get("sources")
    issues = credibility.get("issues")
    if not isinstance(sources, list):
        return []
    referenced: set[int] = set()
    if isinstance(issues, list):
        for issue in issues:
            refs = issue.get("source_refs") if isinstance(issue, dict) else None
            if isinstance(refs, list):
                referenced.update(
                    ref for ref in refs
                    if isinstance(ref, int) and not isinstance(ref, bool) and 0 <= ref < len(sources)
                )
    return sorted(referenced) + [index for index in range(len(sources)) if index not in referenced]


def _compact_credibility_details(details: dict[str, Any]) -> str | None:
    """Retain a valid combined report instead of replacing it with truncation."""
    credibility = details.get("credibility")
    if not isinstance(credibility, dict):
        return None

    base = dict(details)
    base["details_compacted"] = True
    # Provider evidence and the derived report are optional details; canonical
    # AI table fields remain untouched and credibility has priority here.
    base.pop("provider_evidence_v2", None)
    base.pop("short_report", None)
    encoded = _encode_details(base)
    if encoded is not None:
        return encoded

    issues = credibility.get("issues")
    sources = credibility.get("sources")
    if not isinstance(issues, list) or not isinstance(sources, list):
        return None
    severity_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    ordered_issues = sorted(
        (item for item in issues if isinstance(item, dict)),
        key=lambda item: severity_rank.get(item.get("severity"), 3),
    )

    ai_details = details.get("ai_details")
    signals = ai_details.get("signals") if isinstance(ai_details, dict) else []
    ordered_signals = sorted(
        (item for item in signals if isinstance(item, dict)),
        key=lambda item: severity_rank.get(item.get("severity"), 3),
    ) if isinstance(signals, list) else []
    credible_points = credibility.get("credible_points")
    points = [item for item in credible_points if isinstance(item, str)] if isinstance(credible_points, list) else []

    # Retain the most severe issues and signals first.  The canonical score
    # fields live in columns; summaries and reduced, valid evidence remain in
    # details rather than replacing the entire report with a truncation marker.
    for issue_count in range(len(ordered_issues), -1, -1):
        candidate_credibility = dict(credibility)
        candidate_credibility["issues"] = ordered_issues[:issue_count]
        priority = _credibility_priority_indexes(candidate_credibility)
        for signal_count in range(len(ordered_signals), -1, -1):
            for point_count in range(len(points), -1, -1):
                for source_count in range(len(priority), -1, -1):
                    candidate = dict(base)
                    compacted_credibility = _compact_credibility_sources(
                        candidate_credibility, priority[:source_count]
                    )
                    compacted_credibility["credible_points"] = points[:point_count]
                    candidate["credibility"] = compacted_credibility
                    if isinstance(ai_details, dict):
                        compacted_ai = dict(ai_details)
                        compacted_ai["signals"] = ordered_signals[:signal_count]
                        candidate["ai_details"] = compacted_ai
                    encoded = _encode_details(candidate)
                    if encoded is not None:
                        return encoded
    return None


def _serialize_canonical_details_bounded(details: dict[str, Any]) -> str:
    encoded = _encode_details(details)
    if encoded is not None:
        return encoded
    compacted = _compact_credibility_details(details)
    if compacted is not None:
        return compacted
    # Keep the small canonical Complex discriminator/confidence subset even if
    # optional evidence itself could not be compacted into the row limit.
    essential = {
        key: details[key]
        for key in ("analysis_mode", "ai_confidence", "ai_status")
        if key in details
    }
    encoded_essential = _encode_details(essential)
    return encoded_essential if encoded_essential is not None else '{"truncated":true}'


def _map_hybrid_v2_to_check_row(
    result: dict[str, Any], user_id: str, source_label: str
) -> dict[str, Any]:
    """Persist Hybrid factual findings separately from Sapling AI detection."""
    hybrid = HybridAnalysisResponse.model_validate(result)
    evidence = hybrid.provider_evidence
    if evidence is None or evidence.provider != "sapling" or evidence.model != "aidetect":
        raise ValueError("Hybrid v2 requires canonical Sapling provider evidence")
    if evidence.score_kind != ScoreKind.AI_PROBABILITY:
        raise ValueError("Hybrid v2 Sapling evidence must be an AI probability")

    # Reuse the canonical BE-06 result contract for all persisted AI fields.
    ai_component = AnalysisResult(
        verdict=Verdict(hybrid.ai_verdict),
        confidence=hybrid.ai_confidence,
        model_used=ModelUsed.SAPLING,
        explanation=hybrid.ai_explanation or "",
        media_type=MediaType.TEXT,
        processing_ms=hybrid.processing_ms,
        semantics_version=hybrid.semantics_version,
        ai_probability=hybrid.ai_probability,
        decision_confidence=hybrid.decision_confidence,
        authenticity_index=hybrid.authenticity_index,
        provider_evidence=evidence,
    )
    if ai_component.ai_probability != evidence.raw_score:
        raise ValueError("Hybrid v2 AI probability must equal Sapling score")
    if ai_component.authenticity_index != authenticity_index_from_ai_probability(
        evidence.raw_score
    ):
        raise ValueError("Hybrid v2 authenticity index must equal Sapling AI probability")

    details = {
        "provider_evidence_v2": evidence.model_dump(mode="json"),
        "fact_check_evidence_v2": hybrid.fact_check_evidence.model_dump(mode="json")
        if hybrid.fact_check_evidence
        else None,
    }
    encoded_details = json.dumps(details, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded_details.encode("utf-8")) > MAX_DETAILS_BYTES:
        encoded_details = '{"truncated":true}'

    return {
        "user_id": user_id,
        "media_type": MediaType.TEXT.value,
        "status": "completed",
        # Preserve the existing deterministic fact-check verdict rather than
        # silently replacing it with Sapling's AI-generation verdict.
        "verdict": hybrid.verdict[:24],
        "semantics_version": ai_component.semantics_version,
        "ai_probability": ai_component.ai_probability,
        "decision_confidence": ai_component.decision_confidence,
        "authenticity_index": ai_component.authenticity_index,
        "provider": evidence.provider[:MAX_PROVIDER],
        "model": evidence.model[:MAX_MODEL],
        "explanation": ai_component.explanation[:MAX_EXPLANATION] or None,
        "source_label": str(source_label)[:MAX_SOURCE_LABEL] or None,
        "processing_ms": max(0, hybrid.processing_ms),
        "details": encoded_details,
    }


def map_analysis_to_check_row(
    result: dict[str, Any], user_id: str, source_label: str = ""
) -> dict[str, Any]:
    """Map an analysis response to the trusted checks table schema."""
    semantics_version = result.get("semantics_version")
    if semantics_version is not None and "ai_verdict" in result:
        return _map_hybrid_v2_to_check_row(result, user_id, source_label)
    canonical = AnalysisResult.model_validate(result) if semantics_version is not None else None
    if canonical is not None:
        evidence = canonical.provider_evidence
        model = (evidence.model if evidence else canonical.model_used.value)[:MAX_MODEL]
        provider = evidence.provider if evidence else _provider_for_model(canonical.model_used.value)
        verdict = canonical.verdict.value
        media_type = canonical.media_type.value
        explanation = canonical.explanation
        ai_probability = canonical.ai_probability
        decision_confidence = canonical.decision_confidence
        authenticity_index = canonical.authenticity_index
        details = _serialize_canonical_details(canonical)
    else:
        model = str(_value(result.get("model_used")) or "")[:MAX_MODEL]
        provider = _provider_for_model(model)
        verdict = str(_value(result.get("ai_verdict") or result.get("verdict")) or "UNCERTAIN")
        media_type = str(_value(result.get("media_type")) or "text")
        explanation = result.get("explanation")
        ai_probability = None
        decision_confidence = None
        authenticity_index = _legacy_authenticity_index(result)
        details = serialize_check_details(result)
    try:
        processing_ms = int(result.get("processing_ms") or 0)
    except (TypeError, ValueError):
        processing_ms = 0

    return {
        "user_id": user_id,
        "media_type": media_type[:16],
        "status": "completed",
        "verdict": verdict[:24],
        "semantics_version": canonical.semantics_version if canonical else None,
        "ai_probability": ai_probability,
        "decision_confidence": decision_confidence,
        "authenticity_index": authenticity_index,
        "provider": provider[:MAX_PROVIDER] if provider else None,
        "model": model or None,
        "explanation": str(explanation)[:MAX_EXPLANATION] if explanation is not None else None,
        "source_label": str(source_label)[:MAX_SOURCE_LABEL] or None,
        "processing_ms": max(0, processing_ms),
        "details": details,
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

    try:
        check_data = map_analysis_to_check_row(result, user_id, source_label)
    except Exception as exc:
        raise ChecksPersistenceError("checks.payload.map", exc=exc, user_id=user_id, api_key=api_key) from exc

    deadline = current_execution_deadline()

    async def _persist_request(awaitable: Any) -> Any:
        return await deadline.run_persistence(awaitable) if deadline is not None else await awaitable

    try:
        async with httpx.AsyncClient(timeout=bounded_persistence_timeout(15.0)) as client:
            created = await _persist_request(client.post(
                checks_url,
                headers=headers,
                json={
                    "rowId": check_id,
                    "data": check_data,
                    "permissions": _user_permissions(user_id, allow_delete=True),
                },
            ))
            if created.status_code not in (200, 201):
                raise ChecksPersistenceError(
                    "checks.create", response=created, data=check_data, user_id=user_id, api_key=api_key,
                )

            incremented = await _persist_request(client.patch(
                f"{users_url}/{encoded_user}/checks_count/increment",
                headers=headers,
                json={"value": 1},
            ))
            if incremented.status_code != 200:
                raise ChecksPersistenceError(
                    "profile.checks_count.increment", response=incremented,
                    user_id=user_id, api_key=api_key,
                )

            updated = await _persist_request(client.patch(
                f"{users_url}/{encoded_user}",
                headers=headers,
                json={"data": {"last_check_at": now}},
            ))
            if updated.status_code != 200:
                raise ChecksPersistenceError(
                    "profile.last_check_at.update", response=updated,
                    user_id=user_id, api_key=api_key,
                )
    except httpx.HTTPError as exc:
        raise ChecksPersistenceError(
            "checks.persistence.transport", exc=exc, data=check_data, user_id=user_id, api_key=api_key,
        ) from exc

    return check_id
