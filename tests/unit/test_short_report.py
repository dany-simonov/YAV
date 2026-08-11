"""Unit tests for deterministic short reports built from canonical results."""

import pytest

from api.schemas import AnalysisResult
from core.enums import MediaType, ModelUsed, Verdict
from core.short_report import build_short_report


def _result(
    *,
    verdict: Verdict = Verdict.FAKE,
    media_type: MediaType = MediaType.TEXT,
    ai_probability: float | None = 0.98,
    authenticity_index: int | None = 2,
) -> AnalysisResult:
    return AnalysisResult(
        verdict=verdict,
        confidence=0.98,
        model_used=ModelUsed.SAPLING,
        explanation="Provider explanation",
        media_type=media_type,
        semantics_version=2,
        ai_probability=ai_probability,
        authenticity_index=authenticity_index,
    )


def test_fake_text_report_uses_ai_probability_without_claiming_proof():
    report = build_short_report(_result())

    assert "В тексте обнаружены признаки AI-генерации" in report
    assert "вероятность составила 98%" in report
    assert "вероятное использование генеративной модели" in report
    assert "доказ" not in report.lower()
    assert "точно" not in report.lower()


def test_fake_image_report_uses_authenticity_index_when_probability_is_missing():
    report = build_short_report(
        _result(media_type=MediaType.IMAGE, ai_probability=None, authenticity_index=1)
    )

    assert "Анализ изображения выявил признаки AI-генерации" in report
    assert "индекс подлинности — 1%" in report
    assert "вероятное синтетическое происхождение изображения" in report
    assert "вероятность AI-генерации" not in report


def test_real_report_does_not_claim_human_authorship_or_authenticity_as_fact():
    report = build_short_report(
        _result(
            verdict=Verdict.REAL,
            media_type=MediaType.IMAGE,
            ai_probability=None,
            authenticity_index=96,
        )
    )

    assert "не выявлено выраженных признаков AI-генерации" in report
    assert "индекс подлинности — 96%" in report
    assert "скорее соответствует естественному происхождению" in report
    assert "создан человеком" not in report.lower()
    assert "изображение подлинное" not in report.lower()


def test_uncertain_report_does_not_suggest_real_or_fake_direction():
    report = build_short_report(
        _result(
            verdict=Verdict.UNCERTAIN,
            media_type=MediaType.AUDIO,
            ai_probability=None,
            authenticity_index=None,
        )
    )

    assert "неопределённый" in report
    assert "источник и контекст аудиозаписи" in report
    assert "не выявлено выраженных признаков" not in report
    assert "обнаружены признаки AI-генерации" not in report


def test_probability_is_preferred_over_authenticity_index():
    report = build_short_report(_result(ai_probability=0.75, authenticity_index=25))

    assert "вероятность составила 75%" in report
    assert "индекс подлинности" not in report


def test_report_can_omit_a_metric_when_canonical_values_are_unavailable():
    report = build_short_report(
        _result(
            media_type=MediaType.VIDEO,
            ai_probability=None,
            authenticity_index=None,
        )
    )

    assert "%" not in report
    assert "В видео обнаружены признаки AI-генерации." in report


@pytest.mark.parametrize(
    "result",
    [
        _result(verdict=Verdict.FAKE, media_type=MediaType.TEXT),
        _result(verdict=Verdict.FAKE, media_type=MediaType.IMAGE),
        _result(verdict=Verdict.REAL, media_type=MediaType.AUDIO),
        _result(verdict=Verdict.UNCERTAIN, media_type=MediaType.VIDEO),
    ],
)
def test_every_known_template_contains_exactly_two_sentences(result: AnalysisResult):
    report = build_short_report(result)

    assert report.count(".") == 2


def test_short_report_is_optional_and_serializes_when_present():
    result = _result()
    assert result.short_report is None

    report = build_short_report(result)
    serialized = result.model_copy(update={"short_report": report}).model_dump(exclude_none=True)
    assert serialized["short_report"] == report
