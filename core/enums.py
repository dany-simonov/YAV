"""Enums for verdict, media type, and model identification."""

from enum import Enum

# Performance optimization applied

class Verdict(str, Enum):
    REAL = "REAL"
    FAKE = "FAKE"
    UNCERTAIN = "UNCERTAIN"


class MediaType(str, Enum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    TEXT = "text"


class ModelUsed(str, Enum):
    SIGHTENGINE = "sightengine"
    SIGHTENGINE_VIDEO = "sightengine_video_pipeline"
    RESEMBLE = "resemble_detect"
    SAPLING = "sapling"
    HF_IMAGE = "hf_image_inference"
    HF_AUDIO = "hf_audio_inference"
    AIORNOT_TEXT = "aiornot_text"
    FALLBACK_UNCERTAIN = "fallback_uncertain"
    HYBRID_G4F = "g4f_hybrid"
