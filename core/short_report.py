"""Deterministic, user-facing summaries for canonical analysis results."""

from api.schemas import AnalysisResult
from core.enums import MediaType, Verdict


def _metric(result: AnalysisResult) -> str:
    """Return one canonical metric without changing its meaning."""
    if result.ai_probability is not None:
        return f": вероятность AI-генерации — {round(result.ai_probability * 100)}%"
    if result.authenticity_index is not None:
        return f": индекс подлинности — {result.authenticity_index}%"
    return ""


def build_short_report(result: AnalysisResult) -> str:
    """Build exactly two Russian sentences from an already canonical result.

    The verdict stays authoritative: this function only chooses explanatory
    wording and never derives a new classification from a score.
    """
    metric = _metric(result)

    if result.verdict == Verdict.UNCERTAIN:
        return _uncertain_report(result.media_type)
    if result.verdict == Verdict.FAKE:
        return _fake_report(result.media_type, metric)
    return _real_report(result.media_type, metric)


def _fake_report(media_type: MediaType, metric: str) -> str:
    reports = {
        MediaType.TEXT: (
            f"В тексте обнаружены признаки AI-генерации{metric.replace('вероятность AI-генерации —', 'вероятность составила')}. "
            "Это указывает на вероятное использование генеративной модели, "
            "но само по себе не позволяет установить автора текста."
        ),
        MediaType.IMAGE: (
            f"Анализ изображения выявил признаки AI-генерации{metric}. "
            "Это указывает на вероятное синтетическое происхождение изображения, "
            "но для вывода важно учитывать источник и контекст."
        ),
        MediaType.AUDIO: (
            f"В аудиозаписи обнаружены признаки AI-генерации{metric}. "
            "Это может указывать на синтезированную или изменённую речь, "
            "но результат не доказывает происхождение записи."
        ),
        MediaType.VIDEO: (
            f"В видео обнаружены признаки AI-генерации{metric}. "
            "Это указывает на вероятное синтетическое происхождение видео, "
            "но для вывода важно учитывать источник и контекст."
        ),
    }
    return reports[media_type]


def _real_report(media_type: MediaType, metric: str) -> str:
    reports = {
        MediaType.TEXT: (
            f"В тексте не выявлено выраженных признаков AI-генерации{metric}. "
            "Результат скорее соответствует естественному происхождению текста, "
            "но сам по себе не подтверждает его автора."
        ),
        MediaType.IMAGE: (
            f"В изображении не выявлено выраженных признаков AI-генерации{metric}. "
            "Результат скорее соответствует естественному происхождению изображения, "
            "но сам по себе не подтверждает его подлинность."
        ),
        MediaType.AUDIO: (
            f"В аудиозаписи не выявлено выраженных признаков AI-генерации{metric}. "
            "Результат скорее соответствует естественному происхождению записи, "
            "но не подтверждает его без дополнительного контекста."
        ),
        MediaType.VIDEO: (
            f"В видео не выявлено выраженных признаков AI-генерации{metric}. "
            "Результат скорее соответствует естественному происхождению видео, "
            "но не подтверждает его без дополнительного контекста."
        ),
    }
    return reports[media_type]


def _uncertain_report(media_type: MediaType) -> str:
    subjects = {
        MediaType.TEXT: "Проверка текста",
        MediaType.IMAGE: "Проверка изображения",
        MediaType.AUDIO: "Проверка аудиозаписи",
        MediaType.VIDEO: "Проверка видео",
    }
    contexts = {
        MediaType.TEXT: "текста",
        MediaType.IMAGE: "изображения",
        MediaType.AUDIO: "аудиозаписи",
        MediaType.VIDEO: "видео",
    }
    return (
        f"{subjects[media_type]} не дала достаточно уверенного результата, "
        "поэтому итоговый вердикт отмечен как неопределённый. "
        f"Стоит проверить источник и контекст {contexts[media_type]}, "
        "а при необходимости использовать дополнительные способы анализа."
    )
