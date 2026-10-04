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
    free_heavy_media_daily_limit: int = 1
    pro_heavy_media_monthly_limit: int = 25
    enterprise_monthly_limit: int = 1_000
    enterprise_heavy_media_monthly_limit: int = 250
    custom_monthly_limit: int = 100
    custom_heavy_media_monthly_limit: int = 25
    # Comma-separated Appwrite account IDs. This is a global server-side role,
    # never a client payload field or a mutable user-profile attribute.
    system_admin_user_ids: str = ""
    # Comma-separated email addresses returned by the authenticated Appwrite
    # /account response. Kept only on the Function, never in the browser.
    system_admin_emails: str = ""

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
    # Legacy mixed rows (pre provider-usage migration).  New text counters
    # deliberately use the explicit ``text_words`` dimensions below.
    global_aiornot_text_words_daily: int = 20_000
    global_aiornot_text_words_monthly: int = 600_000
    # Image checks are a distinct paid unit from AI or Not text words.  Keep
    # their server-side budget separate so the admin overview never presents
    # a mixed counter as a word count.
    global_aiornot_image_daily: int = 50
    global_aiornot_image_monthly: int = 1_500
    global_sapling_chars_daily: int = 20_000
    global_sapling_chars_monthly: int = 120_000
    # Resemble and HuggingFace are operation-priced external inference
    # providers, so their conservative defaults mirror Sightengine's existing
    # operation budget and remain overrideable only through server env.
    global_resemble_daily: int = 50
    global_resemble_monthly: int = 1_500
    global_huggingface_daily: int = 50
    global_huggingface_monthly: int = 1_500
    # Comma-separated authoritative Appwrite account IDs.  This is server-only
    # configuration; clients never receive or select this entitlement.
    unlimited_user_ids: str = ""

    # FFmpeg / video
    max_video_duration_seconds: int = 60
    video_frame_sample_rate: int = 1

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
