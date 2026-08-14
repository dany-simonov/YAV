"""Application settings loaded from environment / .env file."""

from pydantic_settings import BaseSettings

# Input validation added

class Settings(BaseSettings):
    # Telegram
    bot_token: str = ""
    webhook_url: str = ""

    # FastAPI internal
    api_base_url: str = "http://api:8000"
    api_secret_key: str = "change_me"

    # External APIs
    sightengine_api_user: str = ""
    sightengine_api_secret: str = ""
    sapling_api_key: str = ""
    resemble_api_key: str = ""
    hf_api_token: str = ""
    aiornot_api_key: str = ""
    gemini_api_key: str = ""
    gemini_api_url: str = "https://generativelanguage.googleapis.com"
    gemini_model: str = "gemini-3.1-flash-lite"
    # Grounded credibility can use a separately provisioned Gemini model.  An
    # empty value deliberately falls back to gemini_model for compatibility.
    gemini_credibility_model: str = ""
    # Developer-only diagnostic.  It remains unavailable unless both values
    # are explicitly configured in the Function environment.
    gemini_smoke_enabled: bool = False
    gemini_smoke_diagnostic_secret: str = ""

    # Must match the configured Appwrite synchronous Function timeout.  No
    # default is supplied because that platform setting is deployment-specific.
    synchronous_analyze_execution_timeout_seconds: float = 0.0
    synchronous_analyze_safety_margin_seconds: float = 0.0
    synchronous_analyze_response_safety_margin_seconds: float = 0.0

    # Rate limits
    free_daily_limit: int = 3
    premium_monthly_limit: int = 100

    # Production MVP abuse protection.  These are deliberately server-side
    # defaults: changing them needs no schema migration.
    new_user_period_days: int = 7
    new_user_total_daily: int = 4
    new_user_total_first_7d: int = 10
    new_user_text_daily: int = 3
    new_user_hybrid_daily: int = 1
    new_user_image_daily: int = 1
    new_user_audio_window_hours: int = 72
    new_user_audio_per_window: int = 1
    new_user_video_first_7d: int = 1
    ip_total_daily: int = 8
    ip_heavy_media_daily: int = 2
    new_user_text_max_chars: int = 5000
    new_user_hybrid_max_chars: int = 3000
    new_user_image_max_bytes: int = 5 * 1024 * 1024
    new_user_audio_max_bytes: int = 5 * 1024 * 1024
    new_user_video_max_bytes: int = 10 * 1024 * 1024
    global_gemini_operations_daily: int = 100
    global_sightengine_daily: int = 50
    global_sightengine_monthly: int = 1500
    global_aiornot_words_daily: int = 20_000
    global_aiornot_words_monthly: int = 600_000
    global_sapling_chars_daily: int = 20_000
    global_sapling_chars_monthly: int = 120_000

    # FFmpeg / video
    max_video_duration_seconds: int = 60
    video_frame_sample_rate: int = 1

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
