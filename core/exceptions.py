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
    ) -> None:
        self.service = service
        self.detail = detail
        # These optional diagnostics are deliberately additive: existing
        # two-argument call sites and their public error behavior stay intact.
        self.status_code = status_code
        self.provider_message = provider_message
        super().__init__(f"{service}: {detail}")


class ProviderInfrastructureError(ExternalAPIError):
    """Typed technical failure that permits a reserved quota refund."""

    def __init__(self, service: str, kind: str) -> None:
        self.kind = kind
        super().__init__(service, kind)


class FileTooLarge(Exception):
    """Raised when the uploaded file exceeds the size limit."""


class VideoTooLong(Exception):
    """Raised when the uploaded video exceeds the duration limit."""
