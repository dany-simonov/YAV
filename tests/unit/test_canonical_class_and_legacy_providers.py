"""BE-06.2B canonical contracts for class-confidence and legacy signals."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.hf_audio import HFAudioAdapter
from adapters.hf_image import HFImageAdapter
from adapters.resemble import ResembleAdapter
from adapters.video_pipeline import VideoPipeline
from api.schemas import AnalysisResult, ProviderEvidence
from core.enums import MediaType, ModelUsed, ScoreKind, Verdict
from router.media_router import MediaRouter


def _response(body: object) -> MagicMock:
    response = MagicMock(status_code=200)
    response.json.return_value = body
    return response


def _client(body: object) -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(return_value=_response(body))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _assert_class_confidence(result, score, verdict, label, index):
    assert result.verdict == verdict
    assert result.confidence == score  # legacy provider output remains available
    assert result.semantics_version == 2
    assert result.ai_probability is None
    assert result.decision_confidence == score
    assert result.authenticity_index == index
    assert result.provider_evidence.score_kind == ScoreKind.CLASS_CONFIDENCE
    assert result.provider_evidence.predicted_label == label


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "score", "verdict", "index"),
    [("FAKE", 0.9, Verdict.FAKE, 10), ("REAL", 0.9, Verdict.REAL, 90), ("FAKE", 0.5, Verdict.UNCERTAIN, None)],
)
async def test_hf_image_uses_top_class_confidence_without_ai_probability(label, score, verdict, index):
    raw_marker = "hf-image-raw-payload"
    body = [{"label": label, "score": score}, {"label": "other", "score": 0.01}, {"raw": raw_marker}]
    with patch("adapters.hf_image.httpx.AsyncClient", return_value=_client(body)):
        result = await HFImageAdapter().analyze(b"image")
    _assert_class_confidence(result, score, verdict, label, index)
    assert result.provider_evidence.safe_details == {"score_field": "top_label_score"}
    assert raw_marker not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "score", "verdict", "index"),
    [("spoof", 0.9, Verdict.FAKE, 10), ("bonafide", 0.9, Verdict.REAL, 90), ("spoof", 0.6, Verdict.UNCERTAIN, None)],
)
async def test_hf_audio_uses_top_class_confidence_without_ai_probability(label, score, verdict, index):
    raw_marker = "hf-audio-raw-payload"
    body = [{"label": label, "score": score}, {"label": "other", "score": 0.01}, {"raw": raw_marker}]
    with patch("adapters.hf_audio.httpx.AsyncClient", return_value=_client(body)):
        result = await HFAudioAdapter().analyze(b"WAV")
    _assert_class_confidence(result, score, verdict, label, index)
    assert raw_marker not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("score", "verdict"),
    [(0.9, Verdict.FAKE), (0.1, Verdict.REAL), (0.5, Verdict.UNCERTAIN)],
)
async def test_resemble_legacy_score_is_an_aggregated_signal_not_ai_probability(score, verdict):
    raw_marker = "resemble-raw-payload"
    with patch(
        "adapters.resemble.httpx.AsyncClient",
        return_value=_client({"success": True, "score": score, "raw": raw_marker}),
    ):
        result = await ResembleAdapter().analyze(b"WAV")
    assert result.verdict == verdict
    assert result.confidence == score
    assert result.semantics_version == 2
    assert result.ai_probability is None
    assert result.decision_confidence is None
    assert result.authenticity_index is None
    assert result.provider_evidence.score_kind == ScoreKind.AGGREGATED_SIGNAL
    assert result.provider_evidence.raw_score == score
    assert raw_marker not in result.model_dump_json()


def _frame_result(verdict: Verdict, confidence: float) -> AnalysisResult:
    return AnalysisResult(
        verdict=verdict,
        confidence=confidence,
        model_used=ModelUsed.SIGHTENGINE,
        explanation="safe",
        media_type=MediaType.IMAGE,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("frame_results", "verdict", "legacy_confidence", "fake_ratio"),
    [
        ([_frame_result(Verdict.FAKE, 0.9)] * 3, Verdict.FAKE, 0.9, 1.0),
        ([_frame_result(Verdict.REAL, 0.9)] * 3, Verdict.REAL, 0.9, 0.0),
        ([_frame_result(Verdict.FAKE, 0.9), _frame_result(Verdict.UNCERTAIN, 0.5), _frame_result(Verdict.UNCERTAIN, 0.5)], Verdict.UNCERTAIN, 0.5, 1 / 3),
    ],
)
async def test_legacy_video_exposes_only_aggregate_signal_and_keeps_legacy_confidence(
    frame_results, verdict, legacy_confidence, fake_ratio
):
    # One preflight invocation plus one result per extracted frame.
    responses = [_frame_result(Verdict.FAKE, 0.9), *frame_results]
    with patch("adapters.video_pipeline._get_duration", return_value=1.0), patch(
        "adapters.video_pipeline._extract_frames", return_value=[b"a", b"b", b"c"]
    ), patch(
        "adapters.sightengine.SightengineAdapter.analyze", new=AsyncMock(side_effect=responses)
    ):
        result = await VideoPipeline().analyze(b"video")

    assert result.verdict == verdict
    assert result.confidence == legacy_confidence
    assert result.semantics_version == 2
    assert result.ai_probability is None  # no inherited/double inversion
    assert result.decision_confidence is None
    assert result.authenticity_index is None
    assert result.provider_evidence.score_kind == ScoreKind.AGGREGATED_SIGNAL
    assert result.provider_evidence.raw_score == pytest.approx(fake_ratio)
    assert result.provider_evidence.safe_details == {
        "aggregation_rule": "legacy_frame_ratio_thresholds",
        "frames_analyzed": 3,
        "fake_frame_ratio": pytest.approx(fake_ratio),
    }


@pytest.mark.asyncio
async def test_audio_uncertain_merge_is_canonical_without_blending_incompatible_scores():
    raw_marker = "raw-audio-provider-payload"
    primary = AnalysisResult(
        verdict=Verdict.UNCERTAIN,
        confidence=0.1,
        model_used=ModelUsed.RESEMBLE,
        explanation="primary",
        media_type=MediaType.AUDIO,
        semantics_version=2,
        provider_evidence=ProviderEvidence(
            provider="resemble",
            model="detect_v1",
            raw_score=0.1,
            score_kind=ScoreKind.AGGREGATED_SIGNAL,
            predicted_label="UNCERTAIN",
            safe_details={"score_field": "score"},
            raw_payload=raw_marker,
        ),
    )
    fallback = AnalysisResult(
        verdict=Verdict.UNCERTAIN,
        confidence=0.6,
        model_used=ModelUsed.HF_AUDIO,
        explanation="fallback",
        media_type=MediaType.AUDIO,
        semantics_version=2,
        provider_evidence=ProviderEvidence(
            provider="huggingface",
            model="audio-deepfake-classifier",
            raw_score=0.6,
            score_kind=ScoreKind.CLASS_CONFIDENCE,
            predicted_label="spoof",
            safe_details={"score_field": "top_label_score"},
            raw_payload=raw_marker,
        ),
    )
    with patch("router.media_router.ResembleAdapter.analyze", new=AsyncMock(return_value=primary)), patch(
        "router.media_router.HFAudioAdapter.analyze", new=AsyncMock(return_value=fallback)
    ):
        result = await MediaRouter().route(MediaType.AUDIO, b"audio")
    assert result.confidence == 0.5  # established UNCERTAIN sentinel, not (0.1 + 0.6) / 2
    assert result.semantics_version == 2
    assert result.ai_probability is None
    assert result.decision_confidence is None
    assert result.authenticity_index is None
    assert result.provider_evidence is None
    assert [(component.evidence.provider, component.verdict) for component in result.component_evidence] == [
        ("resemble", Verdict.UNCERTAIN),
        ("huggingface", Verdict.UNCERTAIN),
    ]
    assert [component.evidence.score_kind for component in result.component_evidence] == [
        ScoreKind.AGGREGATED_SIGNAL,
        ScoreKind.CLASS_CONFIDENCE,
    ]
    assert raw_marker not in result.model_dump_json()
