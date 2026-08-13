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

    # Rate limits
    free_daily_limit: int = 3
    premium_monthly_limit: int = 100

    # FFmpeg / video
    max_video_duration_seconds: int = 60
    video_frame_sample_rate: int = 1

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
