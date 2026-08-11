"""BE-06.2A contracts for providers whose scores are AI probabilities."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.aiornot_text import AIOrNotTextAdapter
from adapters.sapling import SaplingAdapter
from adapters.sightengine import SightengineAdapter
from adapters.sightengine_video import SightengineVideoAdapter
from core.enums import ScoreKind, Verdict


ELIGIBLE_TEXT = " ".join(["слово"] * 64)


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


def _assert_canonical_probability(result, score: float, verdict: Verdict, provider: str, model: str):
    assert result.verdict == verdict
    assert result.confidence == score  # legacy contract remains intact
    assert result.semantics_version == 2
    assert result.ai_probability == score
    assert result.decision_confidence is None
    assert result.authenticity_index == round((1 - score) * 100)
    assert result.provider_evidence is not None
    assert result.provider_evidence.provider == provider
    assert result.provider_evidence.model == model
    assert result.provider_evidence.raw_score == score
    assert result.provider_evidence.score_kind == ScoreKind.AI_PROBABILITY
    assert result.provider_evidence.predicted_label == verdict.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("score", "verdict"),
    [(0.9, Verdict.FAKE), (0.1, Verdict.REAL), (0.5, Verdict.UNCERTAIN)],
)
async def test_sightengine_image_emits_canonical_ai_probability(score, verdict):
    raw_marker = "provider-internal-image-payload"
    with patch(
        "adapters.sightengine.httpx.AsyncClient",
        return_value=_client({"status": "success", "type": {"ai_generated": score}, "raw": raw_marker}),
    ):
        result = await SightengineAdapter().analyze(b"image")
    _assert_canonical_probability(result, score, verdict, "sightengine", "genai")
    assert result.provider_evidence.safe_details == {"score_field": "type.ai_generated"}
    assert raw_marker not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("score", "verdict"),
    [(0.9, Verdict.FAKE), (0.1, Verdict.REAL), (0.5, Verdict.UNCERTAIN)],
)
async def test_sapling_emits_canonical_ai_probability(score, verdict):
    raw_marker = "provider-internal-text-payload"
    with patch(
        "adapters.sapling.httpx.AsyncClient",
        return_value=_client({"score": score, "sentence_scores": [], "raw": raw_marker}),
    ):
        result = await SaplingAdapter().analyze(("x" * 60).encode())
    _assert_canonical_probability(result, score, verdict, "sapling", "aidetect")
    assert result.provider_evidence.safe_details == {"score_field": "score"}
    assert raw_marker not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("score", "detected", "verdict"),
    [(0.9, True, Verdict.FAKE), (0.1, False, Verdict.REAL), (0.5, True, Verdict.UNCERTAIN)],
)
async def test_aiornot_text_emits_canonical_ai_probability(score, detected, verdict):
    raw_marker = "provider-internal-aiornot-payload"
    body = {
        "report": {"ai_text": {"confidence": score, "is_detected": detected}},
        "raw": raw_marker,
    }
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(body)):
        result = await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    _assert_canonical_probability(result, score, verdict, "aiornot", "text_sync")
    assert result.provider_evidence.safe_details == {"is_detected": detected}
    assert raw_marker not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("score", "detected", "verdict"),
    [(0.9, False, Verdict.FAKE), (0.1, True, Verdict.REAL)],
)
async def test_aiornot_score_threshold_policy_is_explicit_when_detected_flag_differs(score, detected, verdict):
    body = {"report": {"ai_text": {"confidence": score, "is_detected": detected}}}
    with patch("adapters.aiornot_text.httpx.AsyncClient", return_value=_client(body)):
        result = await AIOrNotTextAdapter().analyze(ELIGIBLE_TEXT.encode())
    # Compatibility policy: score remains authoritative; the raw boolean is
    # retained as bounded evidence rather than contradicting canonical verdict.
    assert result.verdict == verdict
    assert result.provider_evidence.safe_details["is_detected"] is detected
    assert result.provider_evidence.predicted_label == verdict.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scores", "verdict", "maximum"),
    [((0.9, 0.2), Verdict.FAKE, 0.9), ((0.1, 0.2), Verdict.REAL, 0.2), ((0.5, 0.2), Verdict.UNCERTAIN, 0.5)],
)
async def test_direct_sightengine_video_preserves_max_frame_probability(scores, verdict, maximum):
    body = {
        "status": "success",
        "data": {"frames": [{"type": {"deepfake": score}} for score in scores]},
        "raw": "provider-internal-video-payload",
    }
    with patch("adapters.sightengine_video.httpx.AsyncClient", return_value=_client(body)):
        result = await SightengineVideoAdapter().analyze(b"video")
    _assert_canonical_probability(result, maximum, verdict, "sightengine", "deepfake")
    assert result.provider_evidence.safe_details == {
        "aggregation": "max_frame_probability",
        "frames_scored": len(scores),
    }
    assert "fake_frame_ratio" not in result.provider_evidence.safe_details


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter", "patch_path", "body", "boundary", "verdict"),
    [
        (SightengineAdapter, "adapters.sightengine.httpx.AsyncClient", {"status": "success", "type": {"ai_generated": 0.75}}, 0.75, Verdict.FAKE),
        (SaplingAdapter, "adapters.sapling.httpx.AsyncClient", {"score": 0.8}, 0.8, Verdict.FAKE),
        (AIOrNotTextAdapter, "adapters.aiornot_text.httpx.AsyncClient", {"report": {"ai_text": {"confidence": 0.75, "is_detected": True}}}, 0.75, Verdict.FAKE),
    ],
)
async def test_existing_fake_threshold_boundaries_are_unchanged(adapter, patch_path, body, boundary, verdict):
    payload = ELIGIBLE_TEXT.encode() if adapter is AIOrNotTextAdapter else b"x" * 60
    with patch(patch_path, return_value=_client(body)):
        result = await adapter().analyze(payload)
    assert (result.confidence, result.verdict, result.ai_probability) == (boundary, verdict, boundary)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter", "patch_path", "body", "payload", "boundary"),
    [
        (SightengineAdapter, "adapters.sightengine.httpx.AsyncClient", {"status": "success", "type": {"ai_generated": 0.35}}, b"image", 0.35),
        (SaplingAdapter, "adapters.sapling.httpx.AsyncClient", {"score": 0.25}, b"x" * 60, 0.25),
        (AIOrNotTextAdapter, "adapters.aiornot_text.httpx.AsyncClient", {"report": {"ai_text": {"confidence": 0.25, "is_detected": False}}}, ELIGIBLE_TEXT.encode(), 0.25),
    ],
)
async def test_existing_real_threshold_boundaries_are_unchanged(adapter, patch_path, body, payload, boundary):
    with patch(patch_path, return_value=_client(body)):
        result = await adapter().analyze(payload)
    assert (result.confidence, result.verdict, result.ai_probability) == (
        boundary,
        Verdict.REAL,
        boundary,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("score", "verdict"), [(0.75, Verdict.FAKE), (0.35, Verdict.REAL)])
async def test_direct_video_threshold_boundaries_are_unchanged(score, verdict):
    body = {"status": "success", "data": {"frames": [{"type": {"deepfake": score}}]}}
    with patch("adapters.sightengine_video.httpx.AsyncClient", return_value=_client(body)):
        result = await SightengineVideoAdapter().analyze(b"video")
    assert (result.confidence, result.verdict, result.ai_probability) == (score, verdict, score)
