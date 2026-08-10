"""BE-06.5 Hybrid/Big Text canonicalization and persistence contracts."""

import json
from unittest.mock import AsyncMock

import pytest

from api.schemas import AnalysisResult, HybridAnalysisResponse, ProviderEvidence
from core.analyzer import HybridTextAnalyzer
from core.enums import MediaType, ModelUsed, ScoreKind, Verdict
from core.result_normalization import canonicalize_result
from src.appwrite_store import map_analysis_to_check_row


def _sapling_result(score: float, verdict: Verdict) -> AnalysisResult:
    return canonicalize_result(
        AnalysisResult(
            verdict=verdict,
            confidence=score,
            model_used=ModelUsed.SAPLING,
            explanation="Sapling AI: safe bounded explanation.",
            media_type=MediaType.TEXT,
        ),
        ProviderEvidence(
            provider="sapling",
            model="aidetect",
            raw_score=score,
            score_kind=ScoreKind.AI_PROBABILITY,
            predicted_label=verdict.value,
            safe_details={"score_field": "score"},
        ),
    )


async def _analyze(
    sapling: AnalysisResult, fact_checks: list[dict], model: str = "gpt-4.1-nano"
) -> dict:
    analyzer = HybridTextAnalyzer()
    analyzer.sapling.analyze = AsyncMock(return_value=sapling)
    analyzer.fact_check = AsyncMock(return_value=({"fact_checks": fact_checks}, model))
    return await analyzer.analyze("A claim is present. " + "safe text " * 40)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("score", "sapling_verdict", "index"),
    [(0.1, Verdict.REAL, 90), (0.9, Verdict.FAKE, 10), (0.5, Verdict.UNCERTAIN, 50)],
)
async def test_hybrid_exposes_sapling_canonical_ai_component(score, sapling_verdict, index):
    result = await _analyze(_sapling_result(score, sapling_verdict), [])

    # Legacy Hybrid response fields remain available.
    assert result["verdict"] == "clean"
    assert result["ai_verdict"] == sapling_verdict.value
    assert result["ai_confidence"] == score
    assert result["fact_checks"] == []
    assert result["tokens"]

    assert result["semantics_version"] == 2
    assert result["ai_probability"] == score
    assert result["decision_confidence"] is None
    assert result["authenticity_index"] == index
    assert result["provider_evidence"]["provider"] == "sapling"
    assert result["provider_evidence"]["model"] == "aidetect"
    assert result["provider_evidence"]["score_kind"] == "ai_probability"
    assert HybridAnalysisResponse.model_validate(result).ai_probability == score


@pytest.mark.asyncio
async def test_fact_check_verdict_is_deterministic_and_never_changes_ai_probability():
    sapling = _sapling_result(0.2, Verdict.REAL)
    clean = await _analyze(sapling, [])
    factual_finding = await _analyze(
        sapling,
        [
            {
                "exact_quote": "A claim",
                "status": "fake",
                "truth": "The claim is contradicted by the cited source.",
                "source_url": "https://example.com/source",
            }
        ],
    )

    assert clean["verdict"] == "clean"
    assert factual_finding["verdict"] == "contains_fakes"
    assert factual_finding["ai_probability"] == clean["ai_probability"] == 0.2
    assert factual_finding["authenticity_index"] == clean["authenticity_index"] == 80
    assert factual_finding["fact_check_evidence"]["findings"] == [
        {
            "claim": "A claim",
            "status": "fake",
            "summary": "The claim is contradicted by the cited source.",
            "source_url": "https://example.com/source",
        }
    ]


@pytest.mark.asyncio
async def test_hybrid_v2_persistence_keeps_safe_fact_check_evidence_without_tokens_or_raw_text():
    full_text_marker = "FULL_USER_TEXT_MUST_NOT_BE_PERSISTED"
    raw_marker = "raw-g4f-provider-payload"
    analyzer = HybridTextAnalyzer()
    analyzer.sapling.analyze = AsyncMock(return_value=_sapling_result(0.8, Verdict.FAKE))
    analyzer.fact_check = AsyncMock(
        return_value=(
            {
                "fact_checks": [
                    {
                        "exact_quote": "A claim",
                        "status": "manipulation",
                        "truth": "x" * 700,
                        "source_url": "https://example.com/source",
                        "raw_response": raw_marker,
                        "Authorization": "Bearer secret",
                    }
                ],
                "raw_response": raw_marker,
            },
            "gpt-4.1-nano",
        )
    )
    result = await analyzer.analyze(full_text_marker + " A claim " + "x" * 500)
    row = map_analysis_to_check_row(result, "user-1", "source")
    details = json.loads(row["details"])

    assert row["verdict"] == "contains_fakes"
    assert row["provider"] == "sapling"
    assert row["model"] == "aidetect"
    assert row["semantics_version"] == 2
    assert row["ai_probability"] == 0.8
    assert row["decision_confidence"] is None
    assert row["authenticity_index"] == 20
    assert row["authenticity_index"] != 80
    assert set(details) == {"provider_evidence_v2", "fact_check_evidence_v2"}
    finding = details["fact_check_evidence_v2"]["findings"][0]
    assert finding["claim"] == "A claim"
    assert len(finding["summary"]) == 600
    assert full_text_marker not in row["details"]
    assert raw_marker not in row["details"]
    assert "Bearer secret" not in row["details"]
    assert "tokens" not in row["details"]


@pytest.mark.asyncio
async def test_hybrid_fact_check_evidence_omits_a_quote_equal_to_the_full_user_text():
    text = "A" * 200
    analyzer = HybridTextAnalyzer()
    analyzer.sapling.analyze = AsyncMock(return_value=_sapling_result(0.1, Verdict.REAL))
    analyzer.fact_check = AsyncMock(
        return_value=(
            {
                "fact_checks": [
                    {
                        "exact_quote": text,
                        "status": "ok",
                        "truth": "safe",
                        "source_url": "https://example.com/source",
                    }
                ]
            },
            "gpt-4.1-nano",
        )
    )
    result = await analyzer.analyze(text)
    row = map_analysis_to_check_row(result, "user-1")

    assert result["tokens"][0]["text"] == text  # legacy response stays unchanged
    assert text not in row["details"]
    assert json.loads(row["details"])["fact_check_evidence_v2"]["findings"][0]["claim"] == (
        "Full-text claim omitted"
    )


def test_legacy_hybrid_mapping_remains_v1_compatible():
    legacy = {
        "verdict": "clean",
        "ai_verdict": "FAKE",
        "ai_confidence": 0.8,
        "fact_checks": [],
        "tokens": [{"text": "legacy response text", "type": "normal"}],
        "model_used": "gpt-4.1-nano",
        "processing_ms": 5,
    }
    row = map_analysis_to_check_row(legacy, "user-1")

    assert row["semantics_version"] is None
    assert row["authenticity_index"] == 20
    assert row["verdict"] == "FAKE"
    assert json.loads(row["details"])["tokens"] == legacy["tokens"]


@pytest.mark.asyncio
async def test_hybrid_v2_persistence_rejects_an_authenticity_index_not_derived_from_sapling():
    result = await _analyze(_sapling_result(0.8, Verdict.FAKE), [])
    result["authenticity_index"] = 80

    with pytest.raises(ValueError, match="authenticity index"):
        map_analysis_to_check_row(result, "user-1")
