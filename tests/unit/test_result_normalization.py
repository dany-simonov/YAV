"""BE-06.1 canonical result foundation tests."""

import math

import pytest
from pydantic import ValidationError

from api.schemas import AnalysisResult, ProviderEvidence
from core.enums import MediaType, ModelUsed, ScoreKind, Verdict
from core.result_normalization import (
    authenticity_index_from_ai_probability,
    canonicalize_result,
)


def _legacy_result() -> AnalysisResult:
    return AnalysisResult(
        verdict=Verdict.REAL,
        confidence=0.8,
        model_used=ModelUsed.SAPLING,
        explanation="safe",
        media_type=MediaType.TEXT,
    )


def _legacy_payload() -> dict:
    return _legacy_result().model_dump(exclude_none=True)


def test_legacy_analysis_result_construction_and_serialization_remain_compatible():
    result = _legacy_result()
    assert result.model_dump(exclude_none=True) == {
        "verdict": Verdict.REAL,
        "confidence": 0.8,
        "model_used": ModelUsed.SAPLING,
        "explanation": "safe",
        "media_type": MediaType.TEXT,
        "processing_ms": 0,
    }
    assert result.semantics_version is None
    assert result.ai_probability is None
    assert result.authenticity_index is None


@pytest.mark.parametrize(
    ("probability", "index"),
    [(0.0, 100), (0.25, 75), (0.5, 50), (0.75, 25), (1.0, 0)],
)
def test_ai_probability_maps_to_canonical_authenticity_index(probability, index):
    evidence = ProviderEvidence(
        provider="sightengine",
        model="genai",
        raw_score=probability,
        score_kind=ScoreKind.AI_PROBABILITY,
        predicted_label="ai_generated",
        safe_details={"endpoint_version": "1"},
    )
    result = canonicalize_result(_legacy_result(), evidence)
    assert result.semantics_version == 2
    assert result.ai_probability == probability
    assert result.decision_confidence is None
    assert result.authenticity_index == index
    assert result.provider_evidence == evidence


def test_class_confidence_is_not_automatically_interpreted_as_ai_probability():
    evidence = ProviderEvidence(
        provider="huggingface",
        model="deepfake-vs-real-image-detection",
        raw_score=1.0,
        score_kind=ScoreKind.CLASS_CONFIDENCE,
        predicted_label="REAL",
    )
    result = canonicalize_result(_legacy_result(), evidence)
    assert result.semantics_version == 2
    assert result.ai_probability is None
    assert result.decision_confidence == 1.0
    assert result.authenticity_index is None


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, -0.1, 1.1, "0.5", True])
def test_probability_fields_reject_non_finite_or_out_of_range_values(value):
    with pytest.raises(ValidationError):
        AnalysisResult(
            **_legacy_payload(),
            ai_probability=value,
        )
    with pytest.raises(ValidationError):
        AnalysisResult(
            **_legacy_payload(),
            decision_confidence=value,
        )


@pytest.mark.parametrize("value", [-1, 101, 50.5, True])
def test_authenticity_index_must_be_an_integer_between_zero_and_one_hundred(value):
    with pytest.raises(ValidationError):
        AnalysisResult(**_legacy_payload(), authenticity_index=value)


def test_canonical_boundary_accepts_all_required_edge_values():
    result = AnalysisResult(
        **_legacy_payload(),
        semantics_version=2,
        ai_probability=0.5,
        decision_confidence=0.0,
        authenticity_index=50,
    )
    assert result.semantics_version == 2
    assert result.ai_probability == 0.5
    assert result.decision_confidence == 0.0
    assert result.authenticity_index == 50


def test_authenticity_index_function_rejects_invalid_probability():
    with pytest.raises(ValueError):
        authenticity_index_from_ai_probability(1.1)
