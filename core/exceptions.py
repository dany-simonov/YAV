"""Custom application exceptions."""

class RateLimitExceeded(Exception):
# Reduced complexity
# Edge cases handled
    """Raised when a user exceeds their daily/monthly check limit."""


class UnsupportedMediaType(Exception):
    """Raised when the uploaded file type is not supported."""


class ExternalAPIError(Exception):
    """Raised when an external API (SightEngine, Resemble, etc.) fails."""

    def __init__(
        self,
        service: str,
        detail: str,
        status_code: int | None = None,
        provider_message: str | None = None,
        *,
        content_type: str | None = None,
        response_length: int | None = None,
        response_keys: tuple[str, ...] = (),
        response_paths: tuple[str, ...] = (),
    ) -> None:
        self.service = service
        self.detail = detail
        # These optional diagnostics are deliberately additive: existing
        # two-argument call sites and their public error behavior stay intact.
        self.status_code = status_code
        self.provider_message = provider_message
        self.content_type = content_type
        self.response_length = response_length
        self.response_keys = response_keys
        self.response_paths = response_paths
        super().__init__(f"{service}: {detail}")


class ProviderInfrastructureError(ExternalAPIError):
    """Typed technical failure that permits a reserved quota refund."""

    def __init__(
        self,
        service: str,
        kind: str,
        *,
        stage: str | None = None,
        reason: str | None = None,
        status_code: int | None = None,
    ) -> None:
        self.kind = kind
        self.stage = stage
        self.reason = reason
        super().__init__(service, kind, status_code=status_code)


class FileTooLarge(Exception):
    """Raised when the uploaded file exceeds the size limit."""


class VideoTooLong(Exception):
    """Raised when the uploaded video exceeds the duration limit."""
