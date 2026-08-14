"""Unit tests for trusted Appwrite TablesDB persistence."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.appwrite_store import (
    ChecksPersistenceError,
    ensure_user_profile,
    get_authenticated_account,
    map_analysis_to_check_row,
    persist_check_result,
    serialize_check_details,
)
from src.execution_deadline import ExecutionDeadline, reset_execution_deadline, set_execution_deadline
from src.validation import MAX_DETAILS_BYTES


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


def _canonical_result(**overrides):
    result = {
        "verdict": "FAKE",
        "confidence": 0.8,
        "model_used": "sightengine",
        "explanation": "safe summary",
        "media_type": "image",
        "processing_ms": 123,
        "semantics_version": 2,
        "ai_probability": 0.8,
        "authenticity_index": 20,
        "provider_evidence": {
            "provider": "sightengine",
            "model": "genai",
            "raw_score": 0.8,
            "score_kind": "ai_probability",
            "predicted_label": "FAKE",
            "safe_details": {"score_field": "type.ai_generated"},
        },
    }
    result.update(overrides)
    return result


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


def test_ai_probability_v2_persists_canonical_fields_without_double_inversion():
    row = map_analysis_to_check_row(_canonical_result(), "authenticated-user")

    assert row["semantics_version"] == 2
    assert row["ai_probability"] == 0.8
    assert row["decision_confidence"] is None
    assert row["authenticity_index"] == 20
    assert row["authenticity_index"] != 80


def test_gemini_text_result_persists_its_canonical_authenticity_index():
    row = map_analysis_to_check_row(
        _canonical_result(
            verdict="REAL",
            confidence=0.05,
            model_used="gemini_text_verification",
            media_type="text",
            ai_probability=0.05,
            authenticity_index=95,
            provider_evidence={
                "provider": "gemini",
                "model": "gemini-test-model",
                "raw_score": 0.95,
                "score_kind": "authenticity_score",
                "predicted_label": "REAL",
                "safe_details": {"score_field": "score"},
            },
        ),
        "authenticated-user",
    )

    assert row["authenticity_index"] == 95


def test_combined_text_credibility_is_persisted_in_the_same_safe_details_record():
    row = map_analysis_to_check_row(
        _canonical_result(
            verdict="REAL",
            model_used="gemini_text_verification",
            media_type="text",
            ai_probability=0.05,
            authenticity_index=95,
            credibility={
                "status": "completed",
                "credibility_index": 34,
                "verdict": "LOW_CREDIBILITY",
                "confidence": 0.8,
                "processing_ms": 8120,
                "summary": "Ключевые утверждения требуют дополнительной проверки.",
                "issues": [],
                "sources": [{"title": "Источник", "url": "https://example.org/source"}],
            },
        ),
        "authenticated-user",
    )
    details = json.loads(row["details"])
    assert details["credibility"]["credibility_index"] == 34
    assert details["credibility"]["processing_ms"] == 8120
    assert row["authenticity_index"] == 95


@pytest.mark.parametrize("confidence", [0.0, 0.96, 1.0])
def test_complex_ai_confidence_is_persisted_independently_of_authenticity_index(confidence):
    row = map_analysis_to_check_row(
        _canonical_result(
            verdict="FAKE",
            confidence=confidence,
            model_used="gemini_text_verification",
            media_type="text",
            authenticity_index=4,
            analysis_mode="complex",
        ),
        "authenticated-user",
    )
    details = json.loads(row["details"])

    assert row["verdict"] == "FAKE"
    assert row["authenticity_index"] == 4
    assert details["ai_confidence"] == confidence


def test_unavailable_complex_ai_does_not_persist_a_sentinel_confidence():
    row = map_analysis_to_check_row(
        _canonical_result(
            verdict="UNCERTAIN",
            confidence=0.5,
            model_used="gemini_text_verification",
            media_type="text",
            authenticity_index=None,
            analysis_mode="complex",
            ai_status="unavailable",
        ),
        "authenticated-user",
    )

    assert "ai_confidence" not in json.loads(row["details"])


def test_complex_ai_confidence_survives_when_credibility_is_unavailable():
    row = map_analysis_to_check_row(
        _canonical_result(
            confidence=0.96,
            model_used="gemini_text_verification",
            media_type="text",
            authenticity_index=4,
            analysis_mode="complex",
            credibility={
                "status": "unavailable", "summary": "Проверка временно недоступна.",
                "issues": [], "sources": [],
            },
        ),
        "authenticated-user",
    )

    assert json.loads(row["details"])["ai_confidence"] == 0.96


def _maximum_credibility_payload():
    issue = {
        "type": "UNSUPPORTED_CLAIM", "severity": "LOW", "claim": "Ж" * 300,
        "explanation": "Я" * 500, "source_refs": [0, 1, 2, 3, 4],
    }
    return {
        "status": "completed", "credibility_index": 34, "verdict": "LOW_CREDIBILITY",
        "confidence": 0.8, "summary": "Д" * 500, "issues": [issue] * 5,
        "sources": [{"title": "И" * 180, "url": f"https://example.org/{index}/" + "a" * 740} for index in range(5)],
    }


def test_maximum_unicode_credibility_payload_is_compacted_not_lost_and_references_are_repaired():
    row = map_analysis_to_check_row(_canonical_result(
        credibility=_maximum_credibility_payload(),
        provider_evidence={
            **_canonical_result()["provider_evidence"],
            "safe_details": {f"optional_{index}": "x" * 256 for index in range(16)},
        },
    ), "user")
    assert len(row["details"].encode("utf-8")) <= MAX_DETAILS_BYTES
    details = json.loads(row["details"])
    assert details["credibility"]["credibility_index"] == 34
    assert details["credibility"]["verdict"] == "LOW_CREDIBILITY"
    assert details["credibility"]["summary"] == "Д" * 500
    assert details["ai_confidence"] == 0.8
    assert details["details_compacted"] is True
    sources = details["credibility"]["sources"]
    assert all(0 <= ref < len(sources) for issue in details["credibility"]["issues"] for ref in issue["source_refs"])
    assert details != {"truncated": True}


def test_credibility_compaction_preserves_high_severity_issues_before_low_issues():
    payload = _maximum_credibility_payload()
    payload["issues"][0] = {**payload["issues"][0], "severity": "HIGH", "claim": "Важная проблема"}
    row = map_analysis_to_check_row(_canonical_result(credibility=payload), "user")
    details = json.loads(row["details"])
    assert any(issue["severity"] == "HIGH" for issue in details["credibility"]["issues"])


def test_complex_compaction_keeps_ai_confidence_and_high_priority_signal():
    signals = [
        {
            "type": "STRUCTURAL_UNIFORMITY", "severity": "HIGH",
            "title": "Ключевой сигнал", "explanation": "Важное наблюдение.",
        },
        *[
            {
                "type": "GENERIC_FORMULATION", "severity": "LOW",
                "title": "Низкий сигнал " + str(index), "explanation": "П" * 400,
            }
            for index in range(4)
        ],
    ]
    row = map_analysis_to_check_row(_canonical_result(
        confidence=0.96,
        model_used="gemini_text_verification",
        media_type="text",
        analysis_mode="complex",
        ai_details={"signals": signals, "human_signals": ["Авторская деталь."] * 3},
        credibility=_maximum_credibility_payload(),
        provider_evidence={
            **_canonical_result()["provider_evidence"],
            "safe_details": {f"optional_{index}": "x" * 256 for index in range(16)},
        },
    ), "user")
    details = json.loads(row["details"])

    assert len(row["details"].encode("utf-8")) <= MAX_DETAILS_BYTES
    assert details["ai_confidence"] == 0.96
    assert details["ai_details"]["signals"][0]["severity"] == "HIGH"


def test_gemini_video_persists_its_canonical_authenticity_index_without_inversion():
    row = map_analysis_to_check_row(
        _canonical_result(
            verdict="FAKE",
            confidence=0.95,
            model_used="gemini_video_verification",
            media_type="video",
            ai_probability=None,
            authenticity_index=10,
            provider_evidence={
                "provider": "gemini",
                "model": "gemini-test-model",
                "raw_score": 0.1,
                "score_kind": "authenticity_score",
                "predicted_label": "FAKE",
                "safe_details": {"structured_response": True},
            },
        ),
        "authenticated-user",
    )

    assert row["provider"] == "gemini"
    assert row["model"] == "gemini-test-model"
    assert row["authenticity_index"] == 10
    assert row["authenticity_index"] != 5


def test_class_confidence_v2_persists_decision_semantics_and_canonical_provider_model():
    result = _canonical_result(
        verdict="REAL",
        confidence=0.91,
        model_used="hf_image_inference",
        ai_probability=None,
        decision_confidence=0.91,
        authenticity_index=91,
        provider_evidence={
            "provider": "huggingface",
            "model": "dima806/deepfake-vs-real-image-detection",
            "raw_score": 0.91,
            "score_kind": "class_confidence",
            "predicted_label": "REAL",
            "safe_details": {"score_field": "top_label_score"},
        },
    )

    row = map_analysis_to_check_row(result, "authenticated-user")

    assert row["ai_probability"] is None
    assert row["decision_confidence"] == 0.91
    assert row["authenticity_index"] == 91
    assert row["provider"] == "huggingface"
    assert row["model"] == "dima806/deepfake-vs-real-image-detection"


def test_uncertain_class_confidence_keeps_authenticity_index_null():
    row = map_analysis_to_check_row(
        _canonical_result(
            verdict="UNCERTAIN",
            ai_probability=None,
            decision_confidence=0.5,
            authenticity_index=None,
            provider_evidence={
                "provider": "huggingface",
                "model": "classifier",
                "raw_score": 0.5,
                "score_kind": "class_confidence",
                "predicted_label": "FAKE",
            },
        ),
        "authenticated-user",
    )
    assert row["decision_confidence"] == 0.5
    assert row["authenticity_index"] is None


def test_aggregated_signal_does_not_invent_probability_confidence_or_index():
    row = map_analysis_to_check_row(
        _canonical_result(
            model_used="resemble_detect",
            ai_probability=None,
            decision_confidence=None,
            authenticity_index=None,
            provider_evidence={
                "provider": "resemble",
                "model": "detect_v1",
                "raw_score": 0.8,
                "score_kind": "aggregated_signal",
                "predicted_label": "FAKE",
            },
        ),
        "authenticated-user",
    )
    assert row["ai_probability"] is None
    assert row["decision_confidence"] is None
    assert row["authenticity_index"] is None


def test_legacy_result_without_semantics_version_remains_v1():
    row = map_analysis_to_check_row(
        {"verdict": "FAKE", "confidence": 0.8, "model_used": "sapling"},
        "authenticated-user",
    )
    assert row["semantics_version"] is None
    assert row["ai_probability"] is None
    assert row["decision_confidence"] is None
    assert row["authenticity_index"] == 20


def test_v2_evidence_is_bounded_to_versioned_curated_details():
    raw_marker = "raw-provider-secret"
    result = _canonical_result(
        raw_provider_response={"authorization": raw_marker},
        provider_evidence={
            **_canonical_result()["provider_evidence"],
            "unexpected_raw_payload": raw_marker,
        },
    )
    details = json.loads(map_analysis_to_check_row(result, "authenticated-user")["details"])

    assert set(details) == {"provider_evidence_v2", "ai_confidence"}
    assert details["ai_confidence"] == 0.8
    assert details["provider_evidence_v2"]["safe_details"] == {
        "score_field": "type.ai_generated"
    }
    assert raw_marker not in json.dumps(details)


def test_canonical_short_report_is_persisted_in_existing_details_column():
    report = (
        "В тексте обнаружены признаки AI-генерации: вероятность AI-генерации — 80%. "
        "Это указывает на вероятное использование генеративной модели, но не доказывает автора текста."
    )
    row = map_analysis_to_check_row(
        _canonical_result(short_report=report), "authenticated-user"
    )

    assert json.loads(row["details"])["short_report"] == report


def test_audio_component_evidence_persists_safe_scores_without_a_universal_probability():
    result = _canonical_result(
        verdict="UNCERTAIN",
        model_used="resemble_detect",
        ai_probability=None,
        decision_confidence=None,
        authenticity_index=None,
        provider_evidence=None,
        component_evidence=[
            {
                "verdict": "UNCERTAIN",
                "evidence": {
                    "provider": "resemble",
                    "model": "detect_v1",
                    "raw_score": 0.1,
                    "score_kind": "aggregated_signal",
                    "predicted_label": "UNCERTAIN",
                    "safe_details": {"score_field": "score"},
                    "raw_response": "must-not-persist",
                },
            },
            {
                "verdict": "UNCERTAIN",
                "evidence": {
                    "provider": "huggingface",
                    "model": "audio-deepfake-classifier",
                    "raw_score": 0.6,
                    "score_kind": "class_confidence",
                    "predicted_label": "spoof",
                    "safe_details": {"score_field": "top_label_score"},
                },
            },
        ],
    )
    row = map_analysis_to_check_row(result, "authenticated-user")
    components = json.loads(row["details"])["provider_evidence_v2"]["components"]

    assert row["ai_probability"] is None
    assert row["decision_confidence"] is None
    assert row["authenticity_index"] is None
    assert components == [
        {
            "provider": "resemble",
            "model": "detect_v1",
            "verdict": "UNCERTAIN",
            "score_kind": "aggregated_signal",
            "score": 0.1,
            "predicted_label": "UNCERTAIN",
            "safe_details": {"score_field": "score"},
        },
        {
            "provider": "huggingface",
            "model": "audio-deepfake-classifier",
            "verdict": "UNCERTAIN",
            "score_kind": "class_confidence",
            "score": 0.6,
            "predicted_label": "spoof",
            "safe_details": {"score_field": "top_label_score"},
        },
    ]
    assert "must-not-persist" not in row["details"]


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("semantics_version", 1),
        ("semantics_version", True),
        ("confidence", True),
        ("confidence", float("nan")),
        ("confidence", 1.1),
        ("ai_probability", float("nan")),
        ("ai_probability", 1.1),
        ("ai_probability", "0.5"),
        ("decision_confidence", float("inf")),
        ("decision_confidence", -0.1),
        ("authenticity_index", 50.5),
        ("authenticity_index", True),
        ("authenticity_index", 101),
    ],
)
def test_invalid_canonical_values_never_reach_appwrite_mapping(field, invalid):
    with pytest.raises(ValidationError):
        map_analysis_to_check_row(_canonical_result(**{field: invalid}), "authenticated-user")


@pytest.mark.parametrize(
    ("private_key", "private_value"),
    [
        ("authorization", "Bearer secret"),
        ("api-key", "secret"),
        ("user_text", "private text"),
        ("file_bytes", "encoded bytes"),
        ("otherwise_safe", b"raw bytes"),
    ],
)
def test_private_provider_evidence_is_rejected_by_existing_contract(
    private_key, private_value
):
    evidence = {
        **_canonical_result()["provider_evidence"],
        "safe_details": {private_key: private_value},
    }
    with pytest.raises(ValidationError):
        map_analysis_to_check_row(
            _canonical_result(provider_evidence=evidence), "authenticated-user"
        )


@pytest.mark.parametrize("invalid", [None, "not-a-number", float("nan"), float("inf")])
def test_analysis_result_mapping_handles_invalid_confidence(invalid):
    row = map_analysis_to_check_row({"confidence": invalid}, "authenticated-user")
    assert row["authenticity_index"] == 0


def test_analysis_result_mapping_persists_aiornot_text_under_canonical_provider():
    row = map_analysis_to_check_row({"model_used": "aiornot_text"}, "authenticated-user")
    assert row["provider"] == "aiornot"
    assert row["model"] == "aiornot_text"


def test_analysis_result_mapping_persists_gemini_text_under_canonical_provider():
    row = map_analysis_to_check_row({"model_used": "gemini_text_verification"}, "authenticated-user")
    assert row["provider"] == "gemini"
    assert row["model"] == "gemini_text_verification"


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


@pytest.mark.asyncio
async def test_check_persistence_http_timeout_is_bounded_by_request_persistence_budget():
    client = _client_with(
        _response("post", 201, {"$id": "check-1"}),
        _response("patch", 200),
        _response("patch", 200),
    )
    now = time.monotonic()
    deadline = ExecutionDeadline(
        request_start=now,
        root_absolute_deadline=now + 0.5,
        analysis_deadline=now + 0.1,
        persistence_deadline=now + 0.4,
    )
    token = set_execution_deadline(deadline)
    try:
        with patch("src.appwrite_store.httpx.AsyncClient", return_value=client) as http_client:
            await persist_check_result(
                {"verdict": "REAL", "confidence": 0.2, "media_type": "text"},
                "user-1",
                "source",
                "dynamic-key",
            )
    finally:
        reset_execution_deadline(token)

    timeout = http_client.call_args.kwargs["timeout"]
    assert 0 < timeout < 0.4


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 500])
async def test_check_create_failure_exposes_only_safe_structural_diagnostics(status):
    private_text = "private input text"
    response = _response("post", status, {
        "type": "row_invalid_structure", "code": status,
        "message": f"Invalid row\r\n{private_text} user-1 dynamic-key Bearer jwt-token",
    })
    client = _client_with(response)
    with patch("src.appwrite_store.httpx.AsyncClient", return_value=client):
        with pytest.raises(ChecksPersistenceError) as raised:
            await persist_check_result(
                _canonical_result(explanation=private_text), "user-1", "source", "dynamic-key"
            )
    error = raised.value
    assert error.operation == "checks.create"
    assert error.status_code == status
    assert error.appwrite_type == "row_invalid_structure"
    assert error.appwrite_code == status
    assert error.data_keys == (
        "ai_probability,authenticity_index,decision_confidence,details,explanation,media_type,"
        "model,processing_ms,provider,semantics_version,source_label,status,user_id,verdict"
    )
    assert "\r" not in error.appwrite_message and "\n" not in error.appwrite_message
    assert len(error.appwrite_message) <= 300
    for sensitive_value in (private_text, "user-1", "dynamic-key", "jwt-token"):
        assert sensitive_value not in error.appwrite_message


@pytest.mark.asyncio
async def test_completed_canonical_text_result_persists_json_string_details():
    client = _client_with(
        _response("post", 201, {"$id": "check-1"}),
        _response("patch", 200),
        _response("patch", 200),
    )
    result = _canonical_result(
        model_used="aiornot_text",
        provider_evidence={
            "provider": "aiornot", "model": "text_sync", "raw_score": 0.8,
            "score_kind": "ai_probability", "predicted_label": "FAKE",
            "safe_details": {"is_detected": True},
        },
    )
    with patch("src.appwrite_store.httpx.AsyncClient", return_value=client):
        await persist_check_result(result, "user-1", "source", "dynamic-key")
    data = client.post.await_args.kwargs["json"]["data"]
    assert set(data) == {
        "user_id", "media_type", "status", "verdict", "semantics_version", "ai_probability",
        "decision_confidence", "authenticity_index", "provider", "model", "explanation",
        "source_label", "processing_ms", "details",
    }
    assert isinstance(data["semantics_version"], int) and not isinstance(data["semantics_version"], bool)
    assert isinstance(data["ai_probability"], float)
    assert data["decision_confidence"] is None
    assert isinstance(data["authenticity_index"], int) and not isinstance(data["authenticity_index"], bool)
    assert all(isinstance(data[field], str) for field in ("media_type", "status", "verdict", "provider", "model"))
    assert isinstance(data["details"], str)
    assert isinstance(json.loads(data["details"]), dict)
    assert data["decision_confidence"] is None
