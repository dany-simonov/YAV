"""Handler for text AI-generation check via /check command."""

import logging

import httpx
from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command
from aiogram.types import Message

from api.schemas import AnalysisResult
from bot.utils.formatters import format_result
from core.config import settings

# Improved type safety
# Memory-efficient implementation
router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("check"))
async def handle_text_check(message: Message, bot: Bot) -> None:
    text = (message.text or "").replace("/check", "", 1).strip()
    if not text:
        await message.reply(
            "Использование: /check &lt;текст для проверки&gt;\n\nМинимум 50 символов.",
            parse_mode="HTML",
        )
        return

    if len(text) < 50:
        await message.reply(
            "Текст слишком короткий. Минимум 50 символов для анализа.",
            parse_mode="HTML",
        )
        return

    await _check_text(message, bot, text)


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(message: Message, bot: Bot) -> None:
    """Автоматическая проверка всех текстовых сообщений."""
    text = (message.text or "").strip()
    
    # Если текст короткий, просто сообщаем об этом
    if len(text) < 50:
        await message.reply(
            f"Текст слишком короткий. Минимум 50 символов для анализа.\n"
            f"Сейчас: {len(text)} симв.",
            parse_mode="HTML",
        )
        return
    
    await _check_text(message, bot, text)


async def _check_text(message: Message, bot: Bot, text: str) -> None:
    """Common text checking logic."""
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    progress_msg = await message.reply("Анализирую текст...")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{settings.api_base_url}/analyze",
                headers={"x-api-secret": settings.api_secret_key},
                data={
                    "user_id": str(message.from_user.id),
                    "username": message.from_user.username or "",
                    "first_name": message.from_user.first_name or "",
                    "text_content": text,
                },
                files={
                    "file": ("text.txt", text.encode("utf-8"), "text/plain"),
                },
            )

        if response.status_code == 429:
            await progress_msg.edit_text(
                "⛔ Дневной лимит исчерпан (3/день)\n\n"
                "Обновится завтра в 00:00 МСК.\n\n"
                "Premium: 100 проверок в месяц — 199₽"
            )
            return

        if response.status_code == 400:
            error_detail = response.json().get("detail", "Ошибка обработки текста")
            await progress_msg.edit_text(f"{error_detail}")
            return

        if response.status_code != 200:
            await progress_msg.edit_text("Ошибка сервера. Попробуйте позже.")
            return

        result = AnalysisResult(**response.json())
        await progress_msg.edit_text(format_result(result))

    except httpx.TimeoutException:
        await progress_msg.edit_text("Превышено время ожидания. Попробуйте ещё раз.")
    except Exception as exc:
        logger.exception("Error in text check: %s", exc)
        await progress_msg.edit_text("Ошибка при обработке. Попробуйте позже.")


@router.message(Command("start"))
async def handle_start(message: Message) -> None:
    # Handle deep link parameters
    args = (message.text or "").split(maxsplit=1)
    deep_link = args[1].strip() if len(args) > 1 else ""

    if deep_link == "waitlist_premium":
        await message.reply(
            "🔔 <b>Ты в листе ожидания Premium!</b>\n\n"
            "Мы сохранили твой запрос. Как только тариф Premium станет доступен, "
            "ты получишь уведомление первым.\n\n"
            "<b>Premium включает:</b>\n"
            "· 100 проверок в месяц\n"
            "· Приоритетная обработка\n"
            "· История 30 дней\n"
            "· Экспорт PDF-отчётов\n\n"
            "Спасибо за интерес! 💚",
            parse_mode="HTML",
        )
        logger.info("Waitlist premium: user_id=%s, username=%s", message.from_user.id, message.from_user.username)
        return

    if deep_link == "enterprise_inquiry":
        await message.reply(
            "💼 <b>Enterprise / B2B API</b>\n\n"
            "Спасибо за интерес к корпоративному тарифу!\n\n"
            "<b>Enterprise включает:</b>\n"
            "· REST API доступ\n"
            "· Безлимитные проверки\n"
            "· SLA и техподдержка\n"
            "· Кастомные модели детекции\n\n"
            "Напиши нам что-нибудь о вашем кейсе использования, "
            "и мы свяжемся с вами в ближайшее время.\n\n"
            "Или: @dany_simonov",
            parse_mode="HTML",
        )
        logger.info("Enterprise inquiry: user_id=%s, username=%s", message.from_user.id, message.from_user.username)
        return

    await message.reply(
        "🛡 <b>Источник</b> — система верификации медиаконтента.\n\n"
        "Отправь файл для анализа:\n"
        "· 📷 Фото — детекция AI-генерации (точность 94.4%)\n"
        "· 🎵 Аудио / голосовое — синтетическая речь (99.5%)\n"
        "· 🎬 Видео — покадровый анализ (81%)\n"
        "· 📝 Текст — просто напиши (мин. 50 символов)\n\n"
        "<b>Команды:</b>\n"
        "/check — проверка текста\n"
        "/bigcheck — режим большой проверки\n"
        "/status — сколько проверок осталось\n"
        "/about — о боте и точности\n\n"
        "Бесплатно: 3 проверки в день",
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def handle_help(message: Message) -> None:
    await message.reply(
        "<b>Как пользоваться Источник:</b>\n\n"
        "<b>Одиночная проверка:</b>\n"
        "Отправь файл (фото, аудио, видео) или текст в чат.\n\n"
        "<b>Большая проверка (Big Check):</b>\n"
        "Команда /bigcheck — отправляй несколько файлов подряд, "
        "бот соберёт их и выполнит кросс-анализ.\n\n"
        "<b>Проверка текста:</b>\n"
        "/check &lt;текст&gt; — проверить текст на ИИ-генерацию.\n"
        "Или просто напиши текст (минимум 50 символов).\n\n"
        "<b>Поддерживаемые форматы:</b>\n"
        "Фото: JPG, PNG, WEBP\n"
        "Аудио: MP3, OGG, WAV, голосовые сообщения\n"
        "Видео: MP4, MOV, AVI (до 60 сек)\n\n"
        "Результат содержит вердикт, индекс подлинности и объяснение.\n"
        "Точность варьируется от 81% до 99.5% в зависимости от типа файла.",
        parse_mode="HTML",
    )


@router.message(Command("status"))
async def handle_status(message: Message) -> None:
    await message.reply(
        "<b>Ваш статус:</b> Free\n\n"
        f"Лимит: {settings.free_daily_limit} проверок в день\n\n"
        "Лимит обновляется ежедневно в 00:00 МСК.",
        parse_mode="HTML",
    )


@router.message(Command("about"))
async def handle_about(message: Message) -> None:
    await message.reply(
        "<b>Источник</b> использует специализированные модели:\n\n"
        "Фото → Sightengine genai + HuggingFace (fallback)\n"
        "Аудио → Resemble Detect + wav2vec2-xlsr (fallback)\n"
        "Видео → FFmpeg extraction + Sightengine pipeline\n"
        "Текст → Gemini Text Verification (обычный) / AI or Not (длинный)\n"
        "Расширенный текст → Sapling AI Detector + fact-checking\n\n"
        "<b>Точность на тестовых датасетах:</b>\n"
        "· Изображения: 94.4%\n"
        "· Аудио: 99.5%\n"
        "· Видео: 81%\n"
        "· Текст: вероятностная оценка\n\n"
        "Все файлы обрабатываются в оперативной памяти и не сохраняются.\n\n"
        "<i>v0.5.0 · Источник</i>",
        parse_mode="HTML",
    )
