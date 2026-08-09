"""Small, strict validation primitives shared by public entry points."""

from __future__ import annotations

import json
import math
import unicodedata
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


MAX_REQUEST_BYTES = 64 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_SOURCE_LABEL = 120
MAX_EXPLANATION = 2_000
MAX_PROVIDER = 32
MAX_MODEL = 128
MAX_DETAILS_BYTES = 16 * 1024
MAX_EXTERNAL_URL = 2_048
MAX_FACT_CHECK_ITEMS = 20
NORMAL_TEXT_MIN = 50
HYBRID_TEXT_MIN = 200
MAX_TEXT_LENGTH = 10_000
MAX_FILENAME_LENGTH = 255

_FILE_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
_BIDI_SPOOFING = frozenset(chr(value) for value in (*range(0x202A, 0x202F), *range(0x2066, 0x206A)))


class SecurityValidationError(Exception):
    """A client-safe validation failure with a stable public contract."""

    def __init__(self, code: str, detail: str, status_code: int = 400) -> None:
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(code)


def _contains_unsafe_control(value: str, *, permit_whitespace: bool = False) -> bool:
    for char in value:
        codepoint = ord(char)
        if char == "\x00" or 0x7F <= codepoint <= 0x9F or 0xD800 <= codepoint <= 0xDFFF:
            return True
        if codepoint < 0x20 and not (permit_whitespace and char in "\t\n\r"):
            return True
    return False


def validate_text(value: str, *, hybrid: bool) -> str:
    """Validate text without changing its meaningful user-provided content."""
    if not isinstance(value, str):
        raise SecurityValidationError("invalid_request", "Текст должен быть строкой.")
    if not value.strip():
        raise SecurityValidationError("invalid_request", "Текст не должен быть пустым.")
    if _contains_unsafe_control(value, permit_whitespace=True):
        raise SecurityValidationError("invalid_request", "Текст содержит недопустимые управляющие символы.")
    if len(value) > MAX_TEXT_LENGTH:
        raise SecurityValidationError("text_too_long", "Текст превышает лимит в 10 000 символов.")
    minimum = HYBRID_TEXT_MIN if hybrid else NORMAL_TEXT_MIN
    if len(value) < minimum:
        raise SecurityValidationError(
            "text_too_short", f"Для анализа требуется минимум {minimum} символов."
        )
    return value


def validate_file_id(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 36:
        raise SecurityValidationError("invalid_file_id", "Некорректный идентификатор файла.")
    if value[0] not in _FILE_ID_CHARS or any(char not in _FILE_ID_CHARS for char in value):
        raise SecurityValidationError("invalid_file_id", "Некорректный идентификатор файла.")
    return value


def normalize_source_label(value: str | None) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SecurityValidationError("invalid_request", "Название источника должно быть строкой.")
    normalized = unicodedata.normalize("NFC", value).strip()
    if len(normalized) > MAX_SOURCE_LABEL:
        raise SecurityValidationError("invalid_request", "Название источника слишком длинное.")
    if _contains_unsafe_control(normalized) or any(char in _BIDI_SPOOFING for char in normalized):
        raise SecurityValidationError("invalid_request", "Название источника содержит недопустимые символы.")
    return normalized


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    action: Literal["analyze", "ensure_profile"] = "analyze"
    user_id: str | None = Field(default=None, alias="userId", max_length=128)
    username: str | None = Field(default=None, max_length=128)
    first_name: str | None = Field(default=None, alias="firstName", max_length=128)


class EnsureProfileRequest(_RequestModel):
    action: Literal["ensure_profile"]


class TextAnalyzeRequest(_RequestModel):
    action: Literal["analyze"] = "analyze"
    text: str
    media_type: Literal["text"] | None = Field(default=None, alias="mediaType")
    mode: Literal["hybrid_text", "big_text", "factcheck"] | None = None
    analysis_type: Literal["hybrid_text", "big_text", "factcheck"] | None = Field(
        default=None, alias="analysisType"
    )
    source_label: str | None = Field(default=None, alias="sourceLabel")

    @model_validator(mode="after")
    def _validate_text_request(self) -> "TextAnalyzeRequest":
        if self.mode and self.analysis_type and self.mode != self.analysis_type:
            raise ValueError("conflicting analysis modes")
        validate_text(self.text, hybrid=bool(self.mode or self.analysis_type))
        self.source_label = normalize_source_label(self.source_label)
        return self


class FileAnalyzeRequest(_RequestModel):
    action: Literal["analyze"] = "analyze"
    file_id: str = Field(alias="fileId")
    media_type: Literal["image", "audio", "video"] | None = Field(default=None, alias="mediaType")
    source_label: str | None = Field(default=None, alias="sourceLabel")

    @model_validator(mode="after")
    def _validate_file_request(self) -> "FileAnalyzeRequest":
        self.file_id = validate_file_id(self.file_id)
        self.source_label = normalize_source_label(self.source_label)
        return self


ValidatedRequest = EnsureProfileRequest | TextAnalyzeRequest | FileAnalyzeRequest


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SecurityValidationError("invalid_json", "JSON содержит повторяющееся поле.")
        result[key] = value
    return result


def parse_json_object(raw: str | bytes | bytearray) -> dict[str, Any]:
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > MAX_REQUEST_BYTES:
            raise SecurityValidationError("payload_too_large", "Запрос превышает лимит в 64 KiB.", 413)
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecurityValidationError("invalid_json", "Некорректный UTF-8 JSON.") from exc
    if not isinstance(raw, str):
        raise SecurityValidationError("invalid_json", "Некорректный JSON запроса.")
    if len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise SecurityValidationError("payload_too_large", "Запрос превышает лимит в 64 KiB.", 413)
    try:
        parsed = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except SecurityValidationError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SecurityValidationError("invalid_json", "Некорректный JSON запроса.") from exc
    if not isinstance(parsed, dict):
        raise SecurityValidationError("invalid_json", "JSON должен быть объектом.")
    return parsed


def validate_request_payload(payload: Any) -> ValidatedRequest:
    if not isinstance(payload, dict):
        raise SecurityValidationError("invalid_request", "JSON должен быть объектом.")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SecurityValidationError("invalid_request", "Некорректные параметры запроса.") from exc
    if len(encoded) > MAX_REQUEST_BYTES:
        raise SecurityValidationError("payload_too_large", "Запрос превышает лимит в 64 KiB.", 413)

    action = payload.get("action", "analyze")
    if action == "ensure_profile":
        model: type[BaseModel] = EnsureProfileRequest
    elif action == "analyze" or "action" not in payload:
        has_text = "text" in payload
        has_file = "fileId" in payload
        if has_text == has_file:
            raise SecurityValidationError(
                "conflicting_input", "Передайте текст или файл, но не оба."
            )
        model = TextAnalyzeRequest if has_text else FileAnalyzeRequest
    else:
        raise SecurityValidationError("unsupported_action", "Неподдерживаемое действие.")
    try:
        return model.model_validate(payload)
    except SecurityValidationError:
        raise
    except ValidationError as exc:
        raise SecurityValidationError("invalid_request", "Некорректные параметры запроса.") from exc


def normalize_confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("confidence must be numeric")
    confidence = float(value)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence out of range")
    return confidence


def safe_external_url(value: Any) -> str:
    """Return a display-safe external URL or an empty string."""
    if not isinstance(value, str) or len(value) > MAX_EXTERNAL_URL:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return ""
    return value


def bounded_provider_string(value: Any, limit: int) -> str:
    """Drop non-string provider fields and deterministically truncate strings."""
    if not isinstance(value, str):
        return ""
    return value[:limit]
