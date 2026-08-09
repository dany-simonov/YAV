"""Small BE-06 normalization boundary for provider evidence.

Adapters continue to own HTTP parsing.  This module only turns already
validated, curated evidence into additive canonical result fields.
"""

from api.schemas import AnalysisResult, ProviderEvidence
from core.enums import ScoreKind, Verdict


def authenticity_index_from_ai_probability(ai_probability: float) -> int:
    """Convert a known AI/synthetic probability to the user-facing index."""
    if not 0.0 <= ai_probability <= 1.0:
        raise ValueError("ai_probability must be between 0 and 1")
    return round((1.0 - ai_probability) * 100)


def authenticity_index_from_class_decision(
    verdict: Verdict, decision_confidence: float
) -> int | None:
    """Map a class decision confidence without claiming it is AI probability."""
    if not 0.0 <= decision_confidence <= 1.0:
        raise ValueError("decision_confidence must be between 0 and 1")
    if verdict == Verdict.REAL:
        return round(decision_confidence * 100)
    if verdict == Verdict.FAKE:
        return round((1.0 - decision_confidence) * 100)
    return None


def canonicalize_result(
    result: AnalysisResult,
    evidence: ProviderEvidence,
    *,
    use_decision_based_authenticity_index: bool = False,
) -> AnalysisResult:
    """Attach BE-06 v2 fields without reinterpreting class confidence as AI probability."""
    ai_probability = evidence.raw_score if evidence.score_kind == ScoreKind.AI_PROBABILITY else None
    decision_confidence = (
        evidence.raw_score if evidence.score_kind == ScoreKind.CLASS_CONFIDENCE else None
    )
    authenticity_index = None
    if ai_probability is not None:
        authenticity_index = authenticity_index_from_ai_probability(ai_probability)
    elif decision_confidence is not None and use_decision_based_authenticity_index:
        authenticity_index = authenticity_index_from_class_decision(
            result.verdict, decision_confidence
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
