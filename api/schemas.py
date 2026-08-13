"""Pydantic schemas for API requests/responses."""

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from core.enums import MediaType, ModelUsed, ScoreKind, Verdict


def _finite_unit_interval(value: float | None, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number between 0 and 1")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{field_name} must be a finite number between 0 and 1")
    return normalized


class ProviderEvidence(BaseModel):
    """Curated provider score metadata; never a raw provider payload."""

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    raw_score: float
    score_kind: ScoreKind
    predicted_label: str | None = Field(default=None, max_length=64)
    safe_details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("raw_score", mode="before")
    @classmethod
    def validate_raw_score(cls, value: float) -> float:
        return _finite_unit_interval(value, "raw_score")  # type: ignore[return-value]

    @field_validator("safe_details", mode="before")
    @classmethod
    def validate_safe_details(
        cls, value: dict[str, str | int | float | bool | None]
    ) -> dict[str, str | int | float | bool | None]:
        forbidden_keys = {
            "api_key",
            "apikey",
            "authorization",
            "file_bytes",
            "full_text",
            "private_content",
            "provider_response",
            "raw_payload",
            "raw_response",
            "response_body",
            "source_content",
            "token",
            "token_dump",
            "user_text",
        }
        if len(value) > 16:
            raise ValueError("safe_details has too many fields")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 64:
                raise ValueError("safe_details has an invalid key")
            if key.lower().replace("-", "_") in forbidden_keys:
                raise ValueError("safe_details contains private provider data")
            if item is not None and not isinstance(item, (str, int, float, bool)):
                raise ValueError("safe_details values must be primitives")
            if isinstance(item, str) and len(item) > 256:
                raise ValueError("safe_details string is too long")
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("safe_details float must be finite")
        return value


class ComponentEvidence(BaseModel):
    """A completed provider contribution to a non-score-blended result."""

    verdict: Verdict
    evidence: ProviderEvidence


class CredibilityIssue(BaseModel):
    """One bounded, user-facing issue identified by grounded verification."""

    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "FACTUAL_CONTRADICTION",
        "UNSUPPORTED_CLAIM",
        "LOGICAL_INCONSISTENCY",
        "MISLEADING_INFERENCE",
        "OUTDATED_INFORMATION",
        "INSUFFICIENT_EVIDENCE",
    ]
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    claim: str = Field(min_length=1, max_length=300)
    explanation: str = Field(min_length=1, max_length=500)
    source_refs: list[StrictInt] = Field(default_factory=list, max_length=5)

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: list[int]) -> list[int]:
        if any(item < 0 or item >= 5 for item in value):
            raise ValueError("source_refs must contain source indexes from 0 to 4")
        return value


class CredibilitySource(BaseModel):
    """A source copied only from Gemini grounding metadata."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=180)
    url: str = Field(min_length=1, max_length=768)


class CredibilityAssessment(BaseModel):
    """Independent text credibility branch; never an AI-origin score."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "unavailable"]
    model: str = Field(default="gemini_credibility_grounded", min_length=1, max_length=128)
    credibility_index: StrictInt | None = Field(default=None, ge=0, le=100)
    verdict: Literal[
        "VERY_LOW_CREDIBILITY",
        "LOW_CREDIBILITY",
        "MIXED_CREDIBILITY",
        "MOSTLY_CREDIBLE",
        "HIGH_CREDIBILITY",
    ] | None = None
    confidence: float | None = None
    summary: str = Field(min_length=1, max_length=500)
    issues: list[CredibilityIssue] = Field(default_factory=list, max_length=5)
    sources: list[CredibilitySource] = Field(default_factory=list, max_length=5)

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: float | None) -> float | None:
        return _finite_unit_interval(value, "confidence")

    @field_validator("summary", "issues", "sources")
    @classmethod
    def validate_completed_fields(cls, value: Any) -> Any:
        return value

    @model_validator(mode="after")
    def validate_state(self) -> "CredibilityAssessment":
        if self.status == "completed":
            if self.credibility_index is None or self.verdict is None or self.confidence is None:
                raise ValueError("completed credibility assessment requires score fields")
        elif any(value is not None for value in (self.credibility_index, self.verdict, self.confidence)):
            raise ValueError("unavailable credibility assessment must not contain score fields")
        for issue in self.issues:
            if issue.source_refs != sorted(set(issue.source_refs)):
                raise ValueError("source_refs must be unique and in deterministic order")
            if any(ref >= len(self.sources) for ref in issue.source_refs):
                raise ValueError("source_refs must refer to final sources")
        return self


class AnalysisResult(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    
    verdict: Verdict
    confidence: float  # 0.0 – 1.0
    model_used: ModelUsed
    explanation: str
    media_type: MediaType
    processing_ms: int = 0
    # BE-06.1 fields are additive. Legacy adapters keep returning only the
    # original contract until their provider-specific migration in BE-06.2.
    semantics_version: StrictInt | None = Field(default=None, ge=1)
    ai_probability: float | None = None
    decision_confidence: float | None = None
    authenticity_index: StrictInt | None = Field(default=None, ge=0, le=100)
    short_report: str | None = Field(default=None, max_length=600)
    ai_status: Literal["completed", "unavailable"] | None = None
    credibility: CredibilityAssessment | None = None
    provider_evidence: ProviderEvidence | None = None
    component_evidence: list[ComponentEvidence] | None = Field(default=None, max_length=2)

    @field_validator("ai_probability", "decision_confidence", mode="before")
    @classmethod
    def validate_canonical_probability(cls, value: float | None, info: Any) -> float | None:
        return _finite_unit_interval(value, info.field_name)

    @field_validator("semantics_version")
    @classmethod
    def validate_semantics_version(cls, value: int | None) -> int | None:
        if value is not None and value != 2:
            raise ValueError("unsupported semantics_version")
        return value


class FactCheckItem(BaseModel):
    exact_quote: str
    status: str
    truth: str
    source_url: str


class HybridToken(BaseModel):
    text: str
    type: str  # normal | fake | manipulation
    details: dict | None = None


class FactCheckEvidenceItem(BaseModel):
    """Bounded fact-check finding suitable for persisted v2 evidence."""

    claim: str = Field(min_length=1, max_length=240)
    status: str = Field(min_length=1, max_length=32)
    summary: str = Field(default="", max_length=600)
    source_url: str = Field(default="", max_length=2_048)


class FactCheckEvidence(BaseModel):
    """Curated g4f findings, never prompts, raw output, or tokenized input."""

    version: Literal[2] = 2
    provider: Literal["g4f"] = "g4f"
    model: str = Field(min_length=1, max_length=128)
    state: Literal["completed", "unavailable"]
    findings: list[FactCheckEvidenceItem] = Field(default_factory=list, max_length=20)


class HybridAnalysisResponse(BaseModel):
    """Legacy Hybrid fields plus additive BE-06 canonical AI-detection fields."""

    verdict: str
    ai_verdict: str
    ai_confidence: float
    model_used: str
    processing_ms: int
    fact_checks: list[FactCheckItem]
    tokens: list[HybridToken]
    semantics_version: StrictInt | None = Field(default=None, ge=1)
    ai_probability: float | None = None
    decision_confidence: float | None = None
    authenticity_index: StrictInt | None = Field(default=None, ge=0, le=100)
    provider_evidence: ProviderEvidence | None = None
    fact_check_evidence: FactCheckEvidence | None = None
    ai_explanation: str | None = Field(default=None, max_length=2_000)

    @field_validator("ai_probability", "decision_confidence", mode="before")
    @classmethod
    def validate_canonical_probability(cls, value: float | None, info: Any) -> float | None:
        return _finite_unit_interval(value, info.field_name)

    @field_validator("semantics_version")
    @classmethod
    def validate_semantics_version(cls, value: int | None) -> int | None:
        if value is not None and value != 2:
            raise ValueError("unsupported semantics_version")
        return value


class AnalysisRequest(BaseModel):
    user_id: int
    username: str | None = None
    first_name: str | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
