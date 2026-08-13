"""Unit tests for result formatters."""

import pytest

from api.schemas import AnalysisResult
from bot.utils.formatters import calculate_authenticity_index, format_result
from core.enums import MediaType, ModelUsed, Verdict


def _make_result(
    verdict: Verdict = Verdict.FAKE,
    confidence: float = 0.95,
    model_used: ModelUsed = ModelUsed.SIGHTENGINE,
    explanation: str = "Test explanation",
    media_type: MediaType = MediaType.IMAGE,
    processing_ms: int = 1000,
    authenticity_index: int | None = None,
) -> AnalysisResult:
    return AnalysisResult(
        verdict=verdict,
        confidence=confidence,
        model_used=model_used,
        explanation=explanation,
        media_type=media_type,
        processing_ms=processing_ms,
        authenticity_index=authenticity_index,
    )


class TestCalculateAuthenticityIndex:
    def test_fake_high_confidence(self):
        assert calculate_authenticity_index("FAKE", 0.95) == 5

    def test_fake_low_confidence(self):
        assert calculate_authenticity_index("FAKE", 0.16) == 84

    def test_real_high_confidence(self):
        assert calculate_authenticity_index("REAL", 0.91) == 91

    def test_real_low_confidence(self):
        assert calculate_authenticity_index("REAL", 0.12) == 12

    def test_uncertain(self):
        assert calculate_authenticity_index("UNCERTAIN", 0.5) == 50


class TestFormatResult:
    def test_fake_result_has_prohibition_emoji(self):
        text = format_result(_make_result(verdict=Verdict.FAKE, confidence=0.95))
        assert "\U0001f6ab" in text

    def test_fake_result_shows_authenticity_index(self):
        """FAKE with confidence 0.95 => authenticity_index = 5%"""
        text = format_result(_make_result(verdict=Verdict.FAKE, confidence=0.95))
        assert "5%" in text

    def test_gemini_video_uses_canonical_authenticity_index_and_model_name(self):
        text = format_result(_make_result(
            verdict=Verdict.FAKE,
            confidence=0.95,
            authenticity_index=10,
            model_used=ModelUsed.GEMINI_VIDEO,
            media_type=MediaType.VIDEO,
        ))

        assert "Индекс подлинности: <b>10%</b>" in text
        assert "Индекс подлинности: <b>5%</b>" not in text
        assert "Gemini Video Verification" in text

    def test_gemini_text_uses_canonical_authenticity_index_and_model_name(self):
        text = format_result(_make_result(
            verdict=Verdict.FAKE,
            confidence=0.93,
            authenticity_index=12,
            model_used=ModelUsed.GEMINI_TEXT,
            media_type=MediaType.TEXT,
        ))

        assert "Индекс подлинности: <b>12%</b>" in text
        assert "Индекс подлинности: <b>7%</b>" not in text
        assert "Gemini Text Verification" in text

    def test_canonical_authenticity_index_takes_priority_over_legacy_calculation(self):
        text = format_result(_make_result(
            verdict=Verdict.FAKE, confidence=0.95, authenticity_index=0
        ))

        assert "Индекс подлинности: <b>0%</b>" in text

    @pytest.mark.parametrize("authenticity_index", [0, 100])
    def test_canonical_authenticity_index_accepts_boundaries(self, authenticity_index):
        text = format_result(_make_result(authenticity_index=authenticity_index))

        assert f"Индекс подлинности: <b>{authenticity_index}%</b>" in text

    def test_fake_result_shows_human_model_name(self):
        text = format_result(_make_result(verdict=Verdict.FAKE, model_used=ModelUsed.SIGHTENGINE))
        assert "Sightengine" in text

    def test_real_result_has_check_emoji(self):
        text = format_result(_make_result(verdict=Verdict.REAL, confidence=0.91))
        assert "\u2705" in text

    def test_real_result_says_human_content(self):
        text = format_result(_make_result(verdict=Verdict.REAL))
        assert "\u0427\u0435\u043b\u043e\u0432\u0435\u0447\u0435\u0441\u043a\u0438\u0439 \u043a\u043e\u043d\u0442\u0435\u043d\u0442" in text

    def test_real_result_shows_authenticity_index(self):
        """REAL with confidence 0.91 => authenticity_index = 91%"""
        text = format_result(_make_result(verdict=Verdict.REAL, confidence=0.91))
        assert "91%" in text

    def test_uncertain_result_has_warning_emoji(self):
        text = format_result(_make_result(verdict=Verdict.UNCERTAIN, confidence=0.5))
        assert "\u26a0\ufe0f" in text

    def test_uncertain_result_says_undefined(self):
        text = format_result(_make_result(verdict=Verdict.UNCERTAIN))
        assert "\u041d\u0435 \u043e\u043f\u0440\u0435\u0434\u0435\u043b\u0435\u043d\u043e" in text

    def test_explanation_is_present(self):
        text = format_result(_make_result(explanation="Custom test explanation"))
        assert "Custom test explanation" in text

    def test_processing_ms_is_present(self):
        text = format_result(_make_result(processing_ms=1234))
        assert "1234" in text

    def test_shows_authenticity_index_label(self):
        text = format_result(_make_result())
        assert "\u0418\u043d\u0434\u0435\u043a\u0441 \u043f\u043e\u0434\u043b\u0438\u043d\u043d\u043e\u0441\u0442\u0438" in text

    def test_no_confidence_label(self):
        """Old 'Confidence' label must not appear."""
        text = format_result(_make_result())
        assert "\u0423\u0432\u0435\u0440\u0435\u043d\u043d\u043e\u0441\u0442\u044c" not in text

    def test_output_contains_html_bold_tags(self):
        text = format_result(_make_result(verdict=Verdict.FAKE))
        assert "<b>" in text
        assert "</b>" in text

    def test_sapling_model_name_in_output(self):
        text = format_result(_make_result(model_used=ModelUsed.SAPLING, media_type=MediaType.TEXT))
        assert "Sapling" in text

    def test_resemble_model_name_in_output(self):
        text = format_result(_make_result(model_used=ModelUsed.RESEMBLE, media_type=MediaType.AUDIO))
        assert "Resemble" in text

    def test_returns_non_empty_string(self):
        result = _make_result()
        assert isinstance(format_result(result), str)
        assert len(format_result(result)) > 0

    def test_smart_hint_for_fake_image(self):
        text = format_result(_make_result(verdict=Verdict.FAKE, media_type=MediaType.IMAGE))
        assert "\U0001f4a1" in text

    def test_no_smart_hint_for_real(self):
        text = format_result(_make_result(verdict=Verdict.REAL, media_type=MediaType.IMAGE))
        assert "\U0001f4a1" not in text

    @pytest.mark.parametrize("verdict,expected_emoji", [
        (Verdict.REAL, "\u2705"),
        (Verdict.FAKE, "\U0001f6ab"),
        (Verdict.UNCERTAIN, "\u26a0\ufe0f"),
    ])
    def test_all_verdicts_have_correct_emoji(self, verdict: Verdict, expected_emoji: str):
        text = format_result(_make_result(verdict=verdict))
        assert expected_emoji in text
