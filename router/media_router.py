"""Media router — detect file type and dispatch to the right adapter."""

import logging
import os

from adapters.aiornot_text import AIOrNotTextAdapter
from adapters.hf_audio import HFAudioAdapter
from adapters.hf_image import HFImageAdapter
from adapters.resemble import ResembleAdapter
from adapters.sapling import SaplingAdapter
from adapters.sightengine import SightengineAdapter
from adapters.sightengine_video import SightengineVideoAdapter
from api.schemas import AnalysisResult, ComponentEvidence
from core.enums import MediaType, Verdict
from core.exceptions import ExternalAPIError, ProviderInfrastructureError, UnsupportedMediaType

# Cleaner API design
# Improved type safety
logger = logging.getLogger(__name__)

MIME_TYPE_MAP: dict[str, MediaType] = {
    # Images
    "image/jpeg": MediaType.IMAGE,
    "image/png": MediaType.IMAGE,
    "image/webp": MediaType.IMAGE,
    # Audio
    "audio/ogg": MediaType.AUDIO,
    "audio/mpeg": MediaType.AUDIO,
    "audio/wav": MediaType.AUDIO,
    "audio/mp4": MediaType.AUDIO,
    "audio/m4a": MediaType.AUDIO,
    # Video
    "video/mp4": MediaType.VIDEO,
    "video/avi": MediaType.VIDEO,
    "video/quicktime": MediaType.VIDEO,
}

EXTENSION_MAP: dict[str, MediaType] = {
    ".jpg": MediaType.IMAGE,
    ".jpeg": MediaType.IMAGE,
    ".png": MediaType.IMAGE,
    ".webp": MediaType.IMAGE,
    ".mp3": MediaType.AUDIO,
    ".ogg": MediaType.AUDIO,
    ".wav": MediaType.AUDIO,
    ".m4a": MediaType.AUDIO,
    ".mp4": MediaType.VIDEO,
    ".avi": MediaType.VIDEO,
    ".mov": MediaType.VIDEO,
}


def _merge_results(primary: AnalysisResult, fallback: AnalysisResult) -> AnalysisResult:
    """Resolve two UNCERTAIN audio results without blending incompatible scores."""
    if fallback.verdict != Verdict.UNCERTAIN:
        return fallback

    components = [
        ComponentEvidence(verdict=result.verdict, evidence=result.provider_evidence)
        for result in (primary, fallback)
        if result.provider_evidence is not None
    ]
    return AnalysisResult(
        verdict=Verdict.UNCERTAIN,
        # AnalysisResult keeps the legacy float field mandatory.  0.5 is the
        # established UNCERTAIN sentinel, not a combined provider score.
        confidence=0.5,
        model_used=primary.model_used,
        explanation=f"{primary.explanation}\n---\nFallback: {fallback.explanation}",
        media_type=primary.media_type,
        semantics_version=2,
        component_evidence=components or None,
    )


class MediaRouter:
    def detect_type(
        self,
        content_type: str | None,
        filename: str | None,
        text_content: str = "",
    ) -> MediaType:
        """Determine MediaType from MIME type, file extension, or text content."""
        # Text check
        if text_content and text_content.strip():
            return MediaType.TEXT

        # MIME type - handle parameters like "audio/ogg; codecs=opus"
        if content_type:
            base_mime = content_type.split(";")[0].strip().lower()
            if base_mime in MIME_TYPE_MAP:
                return MIME_TYPE_MAP[base_mime]

        # Extension fallback
        if filename:
            ext = os.path.splitext(filename)[1].lower()
            if ext in EXTENSION_MAP:
                return EXTENSION_MAP[ext]

        raise UnsupportedMediaType()

    async def route(self, media_type: MediaType, file_bytes: bytes, text_content: str = "") -> AnalysisResult:
        """Route to the appropriate adapter based on media type."""
        match media_type:
            case MediaType.IMAGE:
                try:
                    return await SightengineAdapter().analyze(file_bytes)
                # A typed provider failure is a technical failure of the
                # Sightengine primary and must take the same HF fallback path
                # as its intentional provider-level 4xx fallback semantics.
                except (ExternalAPIError, ProviderInfrastructureError):
                    return await HFImageAdapter().analyze(file_bytes)

            case MediaType.AUDIO:
                try:
                    result = await ResembleAdapter().analyze(file_bytes)
                    if result.verdict == Verdict.UNCERTAIN:
                        fallback = await HFAudioAdapter().analyze(file_bytes)
                        return _merge_results(result, fallback)
                    return result
                except ExternalAPIError:
                    return await HFAudioAdapter().analyze(file_bytes)

            case MediaType.VIDEO:
                return await SightengineVideoAdapter().analyze(file_bytes)

            case MediaType.TEXT:
                text_bytes = text_content.encode("utf-8") if text_content else file_bytes
                if AIOrNotTextAdapter.is_eligible(text_bytes):
                    try:
                        return await AIOrNotTextAdapter().analyze(text_bytes)
                    except ProviderInfrastructureError:
                        return await SaplingAdapter().analyze(text_bytes)
                return await SaplingAdapter().analyze(text_bytes)

            case _:
                raise UnsupportedMediaType()
