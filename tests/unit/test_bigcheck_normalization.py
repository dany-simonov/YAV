"""Regression coverage for Big Check cross-media verdict normalization."""

import sys
from types import ModuleType

import pytest

try:
    import fastapi  # noqa: F401
except ModuleNotFoundError:
    # Unit-test the pure normalization helper without requiring the optional
    # HTTP framework dependency in this minimal backend test environment.
    fastapi_stub = ModuleType("fastapi")

    class _Router:
        def post(self, *_args, **_kwargs):
            return lambda function: function

    class _UploadFile:
        pass

    class _HTTPException(Exception):
        pass

    fastapi_stub.APIRouter = _Router
    fastapi_stub.File = lambda *_args, **_kwargs: None
    fastapi_stub.Form = lambda *_args, **_kwargs: None
    fastapi_stub.Header = lambda *_args, **_kwargs: None
    fastapi_stub.HTTPException = _HTTPException
    fastapi_stub.UploadFile = _UploadFile
    sys.modules["fastapi"] = fastapi_stub

from api.routers.bigcheck import _cross_analysis
from api.schemas import AnalysisResult
from core.enums import MediaType, ModelUsed, Verdict


def _result(media_type: MediaType, verdict: Verdict, confidence: float) -> AnalysisResult:
    return AnalysisResult(
        verdict=verdict,
        confidence=confidence,
        model_used=ModelUsed.SAPLING,
        explanation="safe",
        media_type=media_type,
    )


def test_any_decisive_fake_wins_without_score_averaging():
    verdict, confidence, summary = _cross_analysis(
        [
            _result(MediaType.IMAGE, Verdict.REAL, 0.99),
            _result(MediaType.AUDIO, Verdict.FAKE, 0.71),
        ]
    )
    assert verdict == Verdict.FAKE
    assert confidence == 0.5
    assert "решающим" in summary


def test_real_requires_every_completed_component_to_be_real():
    verdict, confidence, _ = _cross_analysis(
        [
            _result(MediaType.IMAGE, Verdict.REAL, 0.2),
            _result(MediaType.TEXT, Verdict.REAL, 0.9),
        ]
    )
    assert verdict == Verdict.REAL
    assert confidence == 0.5


@pytest.mark.parametrize("second", [Verdict.UNCERTAIN, Verdict.REAL])
def test_mixed_non_fake_components_are_uncertain_without_score_blending(second):
    verdict, confidence, _ = _cross_analysis(
        [
            _result(MediaType.VIDEO, Verdict.UNCERTAIN, 0.1),
            _result(MediaType.AUDIO, second, 0.9),
        ]
    )
    assert verdict == Verdict.UNCERTAIN
    assert confidence == 0.5
