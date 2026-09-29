"""Small, strict validation primitives shared by public entry points."""

from __future__ import annotations

import json
import math
import re
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
# Normal text is routed to Gemini below AIOrNot's own eligibility threshold.
# Gemini accepts non-empty text, while accuracy guidance is presentation-only.
NORMAL_TEXT_MIN = 1
HYBRID_TEXT_MIN = 200
MAX_TEXT_LENGTH = 10_000
MAX_FILENAME_LENGTH = 255

_FILE_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_BIDI_SPOOFING = frozenset(
    chr(value) for value in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))
)
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")
_APPWRITE_ID = re.compile(r"^[A-Za-z0-9._-]{1,36}$")


class SecurityValidationError(Exception):
    """A client-safe validation failure with a stable public contract."""

    def __init__(self, code: str, detail: str, status_code: int = 400) -> None:
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(code)


class EmailCanonicalizationError(ValueError):
    """Email cannot be represented safely as the canonical account identity."""


def canonicalize_email(value: Any) -> str:
    """Return the one email representation shared by Auth, profiles and invitations."""
    if not isinstance(value, str):
        raise EmailCanonicalizationError("email must be a string")
    email = value.strip().lower()
    if len(email) > 320 or not _EMAIL.fullmatch(email):
        raise EmailCanonicalizationError("invalid email")
    return email


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
        raise SecurityValidationError(
            "invalid_request", "Текст содержит недопустимые управляющие символы."
        )
    if len(value) > MAX_TEXT_LENGTH:
        raise SecurityValidationError(
            "text_too_long", "Текст превышает лимит в 10 000 символов."
        )
    minimum = HYBRID_TEXT_MIN if hybrid else NORMAL_TEXT_MIN
    if len(value) < minimum:
        raise SecurityValidationError(
            "text_too_short", f"Для анализа требуется минимум {minimum} символов."
        )
    return value


def validate_file_id(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 36:
        raise SecurityValidationError(
            "invalid_file_id", "Некорректный идентификатор файла."
        )
    if value[0] not in _FILE_ID_CHARS or any(
        char not in _FILE_ID_CHARS for char in value
    ):
        raise SecurityValidationError(
            "invalid_file_id", "Некорректный идентификатор файла."
        )
    return value


def validate_check_id(value: str) -> str:
    if not isinstance(value, str) or not _APPWRITE_ID.fullmatch(value):
        raise SecurityValidationError(
            "invalid_check_id", "Некорректный идентификатор проверки."
        )
    return value


def normalize_source_label(value: str | None) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SecurityValidationError(
            "invalid_request", "Название источника должно быть строкой."
        )
    normalized = unicodedata.normalize("NFC", value).strip()
    if len(normalized) > MAX_SOURCE_LABEL:
        raise SecurityValidationError(
            "invalid_request", "Название источника слишком длинное."
        )
    if _contains_unsafe_control(normalized) or any(
        char in _BIDI_SPOOFING for char in normalized
    ):
        raise SecurityValidationError(
            "invalid_request", "Название источника содержит недопустимые символы."
        )
    return normalized


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    action: Literal[
        "analyze", "ensure_profile", "gemini_smoke_test", "gemini_list_models"
    ] = "analyze"
    user_id: str | None = Field(default=None, alias="userId", max_length=128)
    username: str | None = Field(default=None, max_length=128)
    first_name: str | None = Field(default=None, alias="firstName", max_length=128)


class EnsureProfileRequest(_RequestModel):
    action: Literal["ensure_profile"]


class GeminiSmokeTestRequest(_RequestModel):
    action: Literal["gemini_smoke_test"]


class GeminiListModelsRequest(_RequestModel):
    action: Literal["gemini_list_models"]


class GetMySubscriptionRequest(_RequestModel):
    action: Literal["get_my_subscription"]


class _AdminActionRequest(_RequestModel):
    """Admin actions never accept caller-controlled identity or role hints."""

    @model_validator(mode="after")
    def _reject_spoofable_identity(self) -> "_AdminActionRequest":
        if (
            self.user_id is not None
            or self.username is not None
            or self.first_name is not None
        ):
            raise ValueError("admin requests cannot include caller identity fields")
        return self


class _AdminRequest(_AdminActionRequest):
    target_user_id: str = Field(alias="targetUserId", min_length=1, max_length=36)


class AdminGetUserPolicyRequest(_AdminRequest):
    action: Literal["admin_get_user_policy"]


class AdminSetSubscriptionRequest(_AdminRequest):
    action: Literal["admin_set_subscription"]
    subscription: str = Field(min_length=1, max_length=16)


class AdminSetQuotaOverridesRequest(_AdminRequest):
    action: Literal["admin_set_quota_overrides"]
    overrides: dict[str, int]


class AdminRemoveQuotaOverrideRequest(_AdminRequest):
    action: Literal["admin_remove_quota_override"]
    quota_key: str = Field(alias="quotaKey", min_length=1, max_length=32)


class AdminResetQuotaOverridesRequest(_AdminRequest):
    action: Literal["admin_reset_quota_overrides"]


class AdminListUsersRequest(_AdminActionRequest):
    action: Literal["admin_list_users"]
    page_size: int = Field(default=25, alias="pageSize", ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=36)
    search: str | None = Field(default=None, min_length=1, max_length=320)


class AdminResetUserQuotaUsageRequest(_AdminRequest):
    action: Literal["admin_reset_user_quota_usage"]
    quota_key: str = Field(alias="quotaKey", min_length=1, max_length=32)
    idempotency_key: str = Field(
        alias="idempotencyKey",
        min_length=16,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )


class AdminResetAllUserUsageRequest(_AdminRequest):
    action: Literal["admin_reset_all_user_usage"]
    idempotency_key: str = Field(
        alias="idempotencyKey",
        min_length=16,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )


class AdminListAuditEventsRequest(_AdminActionRequest):
    action: Literal["admin_list_audit_events"]
    page_size: int = Field(default=25, alias="pageSize", ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=36)
    target_user_id: str | None = Field(
        default=None, alias="targetUserId", min_length=1, max_length=36
    )


class _WorkspaceActionRequest(_RequestModel):
    """Workspace identity is always resolved from the runtime Appwrite JWT."""

    @model_validator(mode="after")
    def _reject_spoofable_identity(self) -> "_WorkspaceActionRequest":
        if (
            self.user_id is not None
            or self.username is not None
            or self.first_name is not None
        ):
            raise ValueError("workspace requests cannot include caller identity fields")
        return self


class WorkspaceCreateRequest(_WorkspaceActionRequest):
    action: Literal["workspace_create"]
    name: str = Field(min_length=1, max_length=120)


class _WorkspaceTargetRequest(_WorkspaceActionRequest):
    workspace_id: str = Field(
        alias="workspaceId", min_length=1, max_length=36, pattern=_APPWRITE_ID.pattern
    )


class _WorkspaceContextRequest(_RequestModel):
    """Optional explicit workspace context for analysis requests."""

    workspace_id: str | None = Field(
        default=None,
        alias="workspaceId",
        min_length=1,
        max_length=36,
        pattern=_APPWRITE_ID.pattern,
    )


class _WorkspacePageRequest(_WorkspaceActionRequest):
    page_size: int = Field(default=25, alias="pageSize", ge=1, le=100)
    cursor: str | None = Field(
        default=None, min_length=1, max_length=36, pattern=_APPWRITE_ID.pattern
    )


class WorkspaceGetRequest(_WorkspacePageRequest):
    action: Literal["workspace_get"]


class WorkspaceListMembersRequest(_WorkspaceTargetRequest):
    action: Literal["workspace_list_members"]


class WorkspaceListInvitationsRequest(_WorkspaceTargetRequest):
    action: Literal["workspace_list_invitations"]
    page_size: int = Field(default=25, alias="pageSize", ge=1, le=100)
    cursor: str | None = Field(
        default=None, min_length=1, max_length=36, pattern=_APPWRITE_ID.pattern
    )


class WorkspaceListHistoryRequest(_WorkspaceTargetRequest):
    action: Literal["workspace_list_history"]
    page_size: int = Field(default=25, alias="pageSize", ge=1, le=100)
    cursor_after: str | None = Field(
        default=None,
        alias="cursorAfter",
        min_length=1,
        max_length=36,
        pattern=_APPWRITE_ID.pattern,
    )


class WorkspaceInviteMemberRequest(_WorkspaceTargetRequest):
    action: Literal["workspace_invite_member"]
    email: str = Field(min_length=3, max_length=320)


class WorkspaceCancelInvitationRequest(_WorkspaceTargetRequest):
    action: Literal["workspace_cancel_invitation"]
    email: str = Field(min_length=3, max_length=320)


class WorkspaceListMyInvitationsRequest(_WorkspacePageRequest):
    action: Literal["workspace_list_my_invitations"]


class WorkspaceAcceptInvitationRequest(_WorkspaceTargetRequest):
    action: Literal["workspace_accept_invitation"]


class WorkspaceRejectInvitationRequest(_WorkspaceTargetRequest):
    action: Literal["workspace_reject_invitation"]


class _PersonalHistoryRequest(_RequestModel):
    """Personal history identity is always the authenticated Function actor."""

    @model_validator(mode="after")
    def _reject_spoofable_identity(self) -> "_PersonalHistoryRequest":
        if (
            self.user_id is not None
            or self.username is not None
            or self.first_name is not None
        ):
            raise ValueError("personal history requests cannot include caller identity fields")
        return self


class ListMyHistoryRequest(_PersonalHistoryRequest):
    action: Literal["list_my_history"]
    page_size: int = Field(default=25, alias="pageSize", ge=1, le=100)
    cursor_after: str | None = Field(
        default=None,
        alias="cursorAfter",
        min_length=1,
        max_length=36,
        pattern=_APPWRITE_ID.pattern,
    )


class _MyCheckRequest(_PersonalHistoryRequest):
    check_id: str = Field(alias="checkId")

    @model_validator(mode="after")
    def _validate_check_id(self) -> "_MyCheckRequest":
        self.check_id = validate_check_id(self.check_id)
        return self


class GetMyCheckRequest(_MyCheckRequest):
    action: Literal["get_my_check"]


class DeleteMyCheckRequest(_MyCheckRequest):
    action: Literal["delete_my_check"]


class TextAnalyzeRequest(_WorkspaceContextRequest):
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


class FileAnalyzeRequest(_WorkspaceContextRequest):
    action: Literal["analyze"] = "analyze"
    file_id: str = Field(alias="fileId")
    media_type: Literal["image", "audio", "video"] | None = Field(
        default=None, alias="mediaType"
    )
    source_label: str | None = Field(default=None, alias="sourceLabel")

    @model_validator(mode="after")
    def _validate_file_request(self) -> "FileAnalyzeRequest":
        self.file_id = validate_file_id(self.file_id)
        self.source_label = normalize_source_label(self.source_label)
        return self


class SourceAnalyzeRequest(_WorkspaceContextRequest):
    """A public URL is the sole input for source-based Complex analysis."""

    action: Literal["analyze"] = "analyze"
    mode: Literal["complex_source"]
    source_url: str = Field(alias="sourceUrl", min_length=8, max_length=2_048)


class ComplexAnalyzeRequest(_WorkspaceContextRequest):
    """Unified Complex input: each source is optional, one is required."""

    action: Literal["analyze"] = "analyze"
    mode: Literal["complex"]
    source_url: str | None = Field(
        default=None, alias="sourceUrl", min_length=8, max_length=2_048
    )
    text: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    file_ids: list[str] = Field(default_factory=list, alias="fileIds", max_length=4)

    @model_validator(mode="after")
    def _validate_complex_request(self) -> "ComplexAnalyzeRequest":
        self.file_ids = [validate_file_id(file_id) for file_id in self.file_ids]
        if len(set(self.file_ids)) != len(self.file_ids):
            raise ValueError("duplicate fileIds")
        if self.text is not None:
            if not self.text.strip():
                self.text = None
            else:
                validate_text(self.text, hybrid=True)
        if not self.source_url and not self.text and not self.file_ids:
            raise ValueError("complex request needs at least one source")
        return self


ValidatedRequest = (
    EnsureProfileRequest
    | GeminiSmokeTestRequest
    | GeminiListModelsRequest
    | GetMySubscriptionRequest
    | AdminGetUserPolicyRequest
    | AdminSetSubscriptionRequest
    | AdminSetQuotaOverridesRequest
    | AdminRemoveQuotaOverrideRequest
    | AdminResetQuotaOverridesRequest
    | AdminListUsersRequest
    | AdminResetUserQuotaUsageRequest
    | AdminResetAllUserUsageRequest
    | AdminListAuditEventsRequest
    | WorkspaceCreateRequest
    | WorkspaceGetRequest
    | WorkspaceListMembersRequest
    | WorkspaceListInvitationsRequest
    | WorkspaceListHistoryRequest
    | WorkspaceInviteMemberRequest
    | WorkspaceCancelInvitationRequest
    | WorkspaceListMyInvitationsRequest
    | WorkspaceAcceptInvitationRequest
    | WorkspaceRejectInvitationRequest
    | ListMyHistoryRequest
    | GetMyCheckRequest
    | DeleteMyCheckRequest
    | TextAnalyzeRequest
    | FileAnalyzeRequest
    | SourceAnalyzeRequest
    | ComplexAnalyzeRequest
)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SecurityValidationError(
                "invalid_json", "JSON содержит повторяющееся поле."
            )
        result[key] = value
    return result


def parse_json_object(raw: str | bytes | bytearray) -> dict[str, Any]:
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > MAX_REQUEST_BYTES:
            raise SecurityValidationError(
                "payload_too_large", "Запрос превышает лимит в 64 KiB.", 413
            )
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecurityValidationError(
                "invalid_json", "Некорректный UTF-8 JSON."
            ) from exc
    if not isinstance(raw, str):
        raise SecurityValidationError("invalid_json", "Некорректный JSON запроса.")
    if len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise SecurityValidationError(
            "payload_too_large", "Запрос превышает лимит в 64 KiB.", 413
        )
    try:
        parsed = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except SecurityValidationError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SecurityValidationError(
            "invalid_json", "Некорректный JSON запроса."
        ) from exc
    if not isinstance(parsed, dict):
        raise SecurityValidationError("invalid_json", "JSON должен быть объектом.")
    return parsed


def validate_request_payload(payload: Any) -> ValidatedRequest:
    if not isinstance(payload, dict):
        raise SecurityValidationError("invalid_request", "JSON должен быть объектом.")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise SecurityValidationError(
            "invalid_request", "Некорректные параметры запроса."
        ) from exc
    if len(encoded) > MAX_REQUEST_BYTES:
        raise SecurityValidationError(
            "payload_too_large", "Запрос превышает лимит в 64 KiB.", 413
        )

    action = payload.get("action", "analyze")
    if action == "ensure_profile":
        model: type[BaseModel] = EnsureProfileRequest
    elif action == "gemini_smoke_test":
        model = GeminiSmokeTestRequest
    elif action == "gemini_list_models":
        model = GeminiListModelsRequest
    elif action == "get_my_subscription":
        model = GetMySubscriptionRequest
    elif action == "admin_get_user_policy":
        model = AdminGetUserPolicyRequest
    elif action == "admin_set_subscription":
        model = AdminSetSubscriptionRequest
    elif action == "admin_set_quota_overrides":
        model = AdminSetQuotaOverridesRequest
    elif action == "admin_remove_quota_override":
        model = AdminRemoveQuotaOverrideRequest
    elif action == "admin_reset_quota_overrides":
        model = AdminResetQuotaOverridesRequest
    elif action == "admin_list_users":
        model = AdminListUsersRequest
    elif action == "admin_reset_user_quota_usage":
        model = AdminResetUserQuotaUsageRequest
    elif action == "admin_reset_all_user_usage":
        model = AdminResetAllUserUsageRequest
    elif action == "admin_list_audit_events":
        model = AdminListAuditEventsRequest
    elif action == "workspace_create":
        model = WorkspaceCreateRequest
    elif action == "workspace_get":
        model = WorkspaceGetRequest
    elif action == "workspace_list_members":
        model = WorkspaceListMembersRequest
    elif action == "workspace_list_invitations":
        model = WorkspaceListInvitationsRequest
    elif action == "workspace_list_history":
        model = WorkspaceListHistoryRequest
    elif action == "workspace_invite_member":
        model = WorkspaceInviteMemberRequest
    elif action == "workspace_cancel_invitation":
        model = WorkspaceCancelInvitationRequest
    elif action == "workspace_list_my_invitations":
        model = WorkspaceListMyInvitationsRequest
    elif action == "workspace_accept_invitation":
        model = WorkspaceAcceptInvitationRequest
    elif action == "workspace_reject_invitation":
        model = WorkspaceRejectInvitationRequest
    elif action == "list_my_history":
        model = ListMyHistoryRequest
    elif action == "get_my_check":
        model = GetMyCheckRequest
    elif action == "delete_my_check":
        model = DeleteMyCheckRequest
    elif action == "analyze" or "action" not in payload:
        has_text = "text" in payload
        has_file = "fileId" in payload
        has_source = "sourceUrl" in payload
        if payload.get("mode") == "complex":
            model = ComplexAnalyzeRequest
        elif has_source:
            if has_text or has_file or payload.get("mode") != "complex_source":
                raise SecurityValidationError(
                    "conflicting_input",
                    "Передайте один источник для комплексного анализа.",
                )
            model = SourceAnalyzeRequest
        elif has_text == has_file:
            raise SecurityValidationError(
                "conflicting_input", "Передайте текст или файл, но не оба."
            )
        else:
            model = TextAnalyzeRequest if has_text else FileAnalyzeRequest
    else:
        raise SecurityValidationError(
            "unsupported_action", "Неподдерживаемое действие."
        )
    try:
        return model.model_validate(payload)
    except SecurityValidationError:
        raise
    except ValidationError as exc:
        raise SecurityValidationError(
            "invalid_request", "Некорректные параметры запроса."
        ) from exc


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
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return ""
    return value


def bounded_provider_string(value: Any, limit: int) -> str:
    """Drop non-string provider fields and deterministically truncate strings."""
    if not isinstance(value, str):
        return ""
    return value[:limit]
