"""Format analysis results for Telegram messages."""

from api.schemas import AnalysisResult

VERDICT_EMOJI = {
    "REAL": "\u2705",
    "FAKE": "\U0001f6ab",
    "UNCERTAIN": "\u26a0\ufe0f",
}

VERDICT_TEXT = {
    "REAL": "\u0427\u0435\u043b\u043e\u0432\u0435\u0447\u0435\u0441\u043a\u0438\u0439 \u043a\u043e\u043d\u0442\u0435\u043d\u0442",
    "FAKE": "\u0421\u0433\u0435\u043d\u0435\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u043e \u0418\u0418",
    "UNCERTAIN": "\u041d\u0435 \u043e\u043f\u0440\u0435\u0434\u0435\u043b\u0435\u043d\u043e",
}

MODEL_NAMES = {
    "gemini_video_verification": "Gemini Video Verification",
    "gemini_text_verification": "Gemini Text Verification",
    "sightengine": "Sightengine (\u0444\u043e\u0442\u043e)",
    "sightengine_video_pipeline": "Sightengine (\u0432\u0438\u0434\u0435\u043e)",
    "resemble_detect": "Resemble Detect (\u0430\u0443\u0434\u0438\u043e)",
    "sapling": "Sapling AI (\u0442\u0435\u043a\u0441\u0442)",
    "hf_image_inference": "HuggingFace (\u0444\u043e\u0442\u043e)",
    "hf_audio_inference": "HuggingFace (\u0430\u0443\u0434\u0438\u043e)",
    "fallback_uncertain": "\u0420\u0435\u0437\u0435\u0440\u0432\u043d\u0430\u044f \u0441\u0438\u0441\u0442\u0435\u043c\u0430",
}

MODEL_ACCURACY = {
    "sightengine": "94.4%",
    "hf_image_inference": "94.4%",
    "resemble_detect": "99.5%",
    "hf_audio_inference": "99.5%",
    "sightengine_video_pipeline": "81%",
    "sapling": "98%",
}

SMART_HINTS = {
    ("FAKE", "image"): "\U0001f4a1 \u041f\u0440\u0438\u0437\u043d\u0430\u043a\u0438 \u0418\u0418-\u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438 \u0447\u0430\u0441\u0442\u043e \u043f\u0440\u043e\u044f\u0432\u043b\u044f\u044e\u0442\u0441\u044f \u0432 \u043e\u0431\u043b\u0430\u0441\u0442\u0438 \u0443\u0448\u0435\u0439, \u0432\u043e\u043b\u043e\u0441 \u0438 \u0444\u043e\u043d\u0430. \u041e\u0431\u0440\u0430\u0442\u0438 \u0432\u043d\u0438\u043c\u0430\u043d\u0438\u0435 \u043d\u0430 \u0434\u0435\u0442\u0430\u043b\u0438.",
    ("FAKE", "audio"): "\U0001f4a1 \u0421\u0438\u043d\u0442\u0435\u0442\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u0440\u0435\u0447\u044c \u0447\u0430\u0441\u0442\u043e \u0438\u043c\u0435\u0435\u0442 \u0440\u0430\u0432\u043d\u043e\u043c\u0435\u0440\u043d\u044b\u0439 \u0442\u0435\u043c\u043f \u0431\u0435\u0437 \u0435\u0441\u0442\u0435\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0445 \u043f\u0430\u0443\u0437.",
    ("FAKE", "text"): "\U0001f4a1 \u0422\u0435\u043a\u0441\u0442 \u043e\u0442 \u0418\u0418 \u0447\u0430\u0441\u0442\u043e \u0441\u043e\u0434\u0435\u0440\u0436\u0438\u0442 \u0448\u0430\u0431\u043b\u043e\u043d\u043d\u044b\u0435 \u043f\u0435\u0440\u0435\u0445\u043e\u0434\u044b \u0438 \u0438\u0437\u0431\u044b\u0442\u043e\u0447\u043d\u044b\u0435 \u043f\u043e\u044f\u0441\u043d\u0435\u043d\u0438\u044f.",
    ("FAKE", "video"): "\U0001f4a1 \u041e\u0431\u0440\u0430\u0442\u0438 \u0432\u043d\u0438\u043c\u0430\u043d\u0438\u0435 \u043d\u0430 \u0430\u0440\u0442\u0435\u0444\u0430\u043a\u0442\u044b \u0432\u043e\u043a\u0440\u0443\u0433 \u043b\u0438\u0446 \u0438 \u043d\u0435\u0435\u0441\u0442\u0435\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0435 \u0434\u0432\u0438\u0436\u0435\u043d\u0438\u044f.",
    ("UNCERTAIN", "image"): "\U0001f4a1 \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439 \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0444\u043e\u0442\u043e \u0432 \u043b\u0443\u0447\u0448\u0435\u043c \u043a\u0430\u0447\u0435\u0441\u0442\u0432\u0435 \u0434\u043b\u044f \u0431\u043e\u043b\u0435\u0435 \u0442\u043e\u0447\u043d\u043e\u0433\u043e \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u0430.",
    ("UNCERTAIN", "audio"): "\U0001f4a1 \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439 \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0430\u0443\u0434\u0438\u043e \u0432 \u0444\u043e\u0440\u043c\u0430\u0442\u0435 WAV \u0434\u043b\u044f \u0431\u043e\u043b\u0435\u0435 \u0442\u043e\u0447\u043d\u043e\u0433\u043e \u0430\u043d\u0430\u043b\u0438\u0437\u0430.",
    ("UNCERTAIN", "video"): "\U0001f4a1 \u041a\u0430\u0447\u0435\u0441\u0442\u0432\u043e \u0432\u0438\u0434\u0435\u043e \u0432\u043b\u0438\u044f\u0435\u0442 \u043d\u0430 \u0442\u043e\u0447\u043d\u043e\u0441\u0442\u044c \u0430\u043d\u0430\u043b\u0438\u0437\u0430. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439 \u0432\u0435\u0440\u0441\u0438\u044e \u0441 \u043b\u0443\u0447\u0448\u0438\u043c \u0440\u0430\u0437\u0440\u0435\u0448\u0435\u043d\u0438\u0435\u043c.",
    ("UNCERTAIN", "text"): "\U0001f4a1 \u0414\u043b\u044f \u0431\u043e\u043b\u0435\u0435 \u0442\u043e\u0447\u043d\u043e\u0433\u043e \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u0430 \u043e\u0442\u043f\u0440\u0430\u0432\u044c \u0442\u0435\u043a\u0441\u0442 \u0434\u043b\u0438\u043d\u043d\u0435\u0435 200 \u0441\u0438\u043c\u0432\u043e\u043b\u043e\u0432.",
    ("REAL", "image"): None,
    ("REAL", "audio"): None,
    ("REAL", "video"): None,
    ("REAL", "text"): None,
}


def calculate_authenticity_index(verdict: str, confidence: float) -> int:
    """Calculate authenticity index from verdict and confidence.

    If FAKE: confidence = probability of being fake, so authenticity = (1 - confidence) * 100
    If REAL: confidence = probability of being real, so authenticity = confidence * 100
    If UNCERTAIN: show confidence as-is
    """
    if verdict == "FAKE":
        return round((1 - confidence) * 100)
    elif verdict == "REAL":
        return round(confidence * 100)
    else:
        return round(confidence * 100)


def format_result(result: AnalysisResult) -> str:
    """Format AnalysisResult into a user-friendly HTML message for Telegram."""
    verdict_val = result.verdict.value
    emoji = VERDICT_EMOJI.get(verdict_val, "\u2753")
    verdict_text = VERDICT_TEXT.get(verdict_val, "\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u043e")
    authenticity_index = result.authenticity_index
    if authenticity_index is None:
        authenticity_index = calculate_authenticity_index(verdict_val, result.confidence)
    model_name = result.model_used.value
    human_model_name = MODEL_NAMES.get(model_name, model_name)

    text = (
        f"{emoji} <b>{verdict_text}</b>\n\n"
        f"\u0418\u043d\u0434\u0435\u043a\u0441 \u043f\u043e\u0434\u043b\u0438\u043d\u043d\u043e\u0441\u0442\u0438: <b>{authenticity_index}%</b>\n"
        f"\u041c\u043e\u0434\u0435\u043b\u044c: {human_model_name}\n"
        f"\u0412\u0440\u0435\u043c\u044f \u0430\u043d\u0430\u043b\u0438\u0437\u0430: {result.processing_ms} \u043c\u0441\n\n"
        f"{result.explanation}\n\n"
        f"<i>\u2139\ufe0f \u0422\u043e\u0447\u043d\u043e\u0441\u0442\u044c \u043e\u0442 81% \u0434\u043e 99.5% \u2014 \u0444\u0438\u043d\u0430\u043b\u044c\u043d\u043e\u0435 \u0440\u0435\u0448\u0435\u043d\u0438\u0435 \u0437\u0430 \u0432\u0430\u043c\u0438.</i>"
    )

    # Smart hint
    media_type_val = result.media_type.value
    hint = SMART_HINTS.get((verdict_val, media_type_val))
    if hint:
        text += f"\n\n{hint}"

    return text


def format_hybrid_tokens(tokens: list[dict]) -> str:
    """Format hybrid tokens into Telegram HTML message with footnotes."""
    lines: list[str] = []
    footnotes: list[str] = []

    for idx, token in enumerate(tokens, 1):
        t_type = token.get("type", "normal")
        txt = token.get("text", "")
        details = token.get("details") or {}
        if t_type == "fake":
            lines.append(f"<s>{txt}</s><sup>{len(footnotes)+1}</sup>")
            footnotes.append(f"{len(footnotes)+1}. Фейк → {details.get('truth','')}. Источник: {details.get('source_url','')}")
        elif t_type == "manipulation":
            lines.append(f"<i>{txt}</i><sup>{len(footnotes)+1}</sup>")
            footnotes.append(f"{len(footnotes)+1}. Манипуляция → {details.get('truth','')}. Источник: {details.get('source_url','')}")
        else:
            lines.append(txt)

    message = "".join(lines)
    if footnotes:
        message += "\n\n" + "\n".join(footnotes)
    return message
