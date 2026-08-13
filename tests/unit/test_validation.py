import pytest

from src.validation import SecurityValidationError, parse_json_object, safe_external_url, validate_request_payload


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
