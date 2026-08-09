"""Small BE-06 normalization boundary for provider evidence.

Adapters continue to own HTTP parsing.  This module only turns already
validated, curated evidence into additive canonical result fields.
"""

from api.schemas import AnalysisResult, ProviderEvidence
from core.enums import ScoreKind


def authenticity_index_from_ai_probability(ai_probability: float) -> int:
    """Convert a known AI/synthetic probability to the user-facing index."""
    if not 0.0 <= ai_probability <= 1.0:
        raise ValueError("ai_probability must be between 0 and 1")
    return round((1.0 - ai_probability) * 100)


def canonicalize_result(result: AnalysisResult, evidence: ProviderEvidence) -> AnalysisResult:
    """Attach BE-06 v2 fields without reinterpreting class confidence as AI probability."""
    ai_probability = evidence.raw_score if evidence.score_kind == ScoreKind.AI_PROBABILITY else None
    decision_confidence = (
        evidence.raw_score if evidence.score_kind == ScoreKind.CLASS_CONFIDENCE else None
    )
    authenticity_index = (
        authenticity_index_from_ai_probability(ai_probability)
        if ai_probability is not None
        else None
    )
    return result.model_copy(
        update={
            "semantics_version": 2,
            "ai_probability": ai_probability,
            "decision_confidence": decision_confidence,
            "authenticity_index": authenticity_index,
            "provider_evidence": evidence,
        }
    )
