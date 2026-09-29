import pytest

from src.validation import (
    SecurityValidationError,
    parse_json_object,
    safe_external_url,
    validate_request_payload,
)


@pytest.mark.parametrize("payload", [None, [], {"text": None}, {"text": []}, {"text": {}}, {"text": True}])
def test_request_rejects_non_string_text(payload):
    with pytest.raises(SecurityValidationError):
        validate_request_payload(payload)


@pytest.mark.parametrize("file_id", ["", "../x", "id?x", "id#x", "id%x", "x" * 37])
def test_request_rejects_unsafe_file_ids(file_id):
    with pytest.raises(SecurityValidationError) as raised:
        validate_request_payload({"fileId": file_id, "mediaType": "image"})
    assert raised.value.code == "invalid_file_id"


def test_request_preserves_script_and_sql_text():
    text = "<script>alert(1)</script> SELECT * FROM users $(rm -rf /) " + "x" * 50
    request = validate_request_payload({"text": text})
    assert request.text == text


@pytest.mark.parametrize("text", [
    "Привет",
    "Это короткий текст.",
    "Сегодня хорошая погода.",
    "Этот небольшой текст написан для проверки работы детектора.",
])
def test_normal_text_accepts_short_nonempty_input_for_sapling(text):
    assert validate_request_payload({"text": text}).text == text


@pytest.mark.parametrize("payload,code", [
    ({"text": "x" * 50, "fileId": "file-id"}, "conflicting_input"),
    ({"text": "x" * 50, "unknown": 1}, "invalid_request"),
    ({"action": "drop_all"}, "unsupported_action"),
    ({"text": " " * 50}, "invalid_request"),
    ({"text": "x" * 10_001}, "text_too_long"),
])
def test_request_contract(payload, code):
    with pytest.raises(SecurityValidationError) as raised:
        validate_request_payload(payload)
    assert raised.value.code == code


def test_json_parser_rejects_malformed_duplicate_and_oversized_input():
    for raw, code in [("{", "invalid_json"), ('{"text":"a","text":"b"}', "invalid_json"), ("x" * (64 * 1024 + 1), "payload_too_large")]:
        with pytest.raises(SecurityValidationError) as raised:
            parse_json_object(raw)
        assert raised.value.code == code


@pytest.mark.parametrize("value", ["javascript:alert(1)", "data:text/html,x", "http://example.com", "https://user:pass@example.com"])
def test_unsafe_external_urls_are_rejected(value):
    assert safe_external_url(value) == ""


def test_https_external_url_is_allowed():
    assert safe_external_url("https://example.com/path") == "https://example.com/path"


def test_source_complex_contract_accepts_only_a_source_url():
    request = validate_request_payload({"mode": "complex_source", "sourceUrl": "https://example.com/post"})
    assert request.source_url == "https://example.com/post"
    with pytest.raises(SecurityValidationError) as raised:
        validate_request_payload({"mode": "complex_source", "sourceUrl": "https://example.com", "text": "x" * 200})
    assert raised.value.code == "conflicting_input"


@pytest.mark.parametrize("payload", [
    {"mode": "complex", "sourceUrl": "https://example.com/post"},
    {"mode": "complex", "text": "x" * 200},
    {"mode": "complex", "fileIds": ["valid-file-id"]},
    {"mode": "complex", "sourceUrl": "https://example.com/post", "text": "x" * 200},
    {"mode": "complex", "sourceUrl": "https://example.com/post", "fileIds": ["valid-file-id"]},
    {"mode": "complex", "text": "x" * 200, "fileIds": ["valid-file-id"]},
    {"mode": "complex", "sourceUrl": "https://example.com/post", "text": "x" * 200, "fileIds": ["valid-file-id"]},
])
def test_unified_complex_contract_accepts_each_supported_input_combination(payload):
    request = validate_request_payload(payload)
    assert request.mode == "complex"


def test_unified_complex_contract_rejects_empty_or_duplicate_files():
    with pytest.raises(SecurityValidationError):
        validate_request_payload({"mode": "complex"})
    with pytest.raises(SecurityValidationError):
        validate_request_payload({"mode": "complex", "fileIds": ["same", "same"]})


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "Текст для персонального анализа."},
        {"fileId": "file-id", "mediaType": "image"},
        {"mode": "complex_source", "sourceUrl": "https://example.com/post"},
        {"mode": "complex", "text": "Текст для комплексного анализа. " * 8},
    ],
)
def test_analysis_requests_without_workspace_remain_personal(payload):
    assert validate_request_payload(payload).workspace_id is None


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "Текст для workspace анализа.", "workspaceId": "workspace-1"},
        {"fileId": "file-id", "mediaType": "image", "workspaceId": "workspace-1"},
        {
            "mode": "complex_source",
            "sourceUrl": "https://example.com/post",
            "workspaceId": "workspace-1",
        },
        {
            "mode": "complex",
            "text": "Текст для workspace комплексного анализа. " * 8,
            "workspaceId": "workspace-1",
        },
    ],
)
def test_analysis_requests_parse_explicit_workspace_id(payload):
    request = validate_request_payload(payload)

    assert request.workspace_id == "workspace-1"
    assert request.model_dump(by_alias=True)["workspaceId"] == "workspace-1"


def test_analysis_workspace_id_uses_existing_controlled_validation_contract():
    with pytest.raises(SecurityValidationError) as raised:
        validate_request_payload(
            {"text": "Текст для проверки workspace контракта.", "workspaceId": "bad/id"}
        )

    assert raised.value.code == "invalid_request"


def test_personal_history_actions_use_actor_only_and_cursor_contract():
    request = validate_request_payload(
        {
            "action": "list_my_history",
            "pageSize": 2,
            "cursorAfter": "check-1",
        }
    )

    assert request.page_size == 2
    assert request.cursor_after == "check-1"
    with pytest.raises(SecurityValidationError) as raised:
        validate_request_payload(
            {"action": "list_my_history", "userId": "another-user"}
        )
    assert raised.value.code == "invalid_request"


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "list_my_history", "pageSize": 0},
        {"action": "list_my_history", "cursorAfter": "bad/id"},
        {"action": "get_my_check", "checkId": "bad/id"},
        {"action": "delete_my_check", "checkId": ""},
    ],
)
def test_personal_history_actions_reject_invalid_pagination_and_check_ids(payload):
    with pytest.raises(SecurityValidationError) as raised:
        validate_request_payload(payload)

    assert raised.value.code in {"invalid_request", "invalid_check_id"}
