"""Shared pytest configuration — sets mock env vars before any project imports."""

import os

# ---------------------------------------------------------------------------
# Mock environment variables — must be set BEFORE any project module is
# imported, because pydantic-settings reads them at class-construction time.
# ---------------------------------------------------------------------------
_MOCK_ENV: dict[str, str] = {
    "BOT_TOKEN": "1234567890:AABBCCDDEEFFGGHHtestbottokenXXXX",
    "API_BASE_URL": "http://localhost:8000",
    "API_SECRET_KEY": "test-secret-key-for-unit-tests",
    "DATABASE_URL": "postgresql+asyncpg://user:password@localhost/testdb",
    "SIGHTENGINE_API_USER": "test_se_user",
    "SIGHTENGINE_API_SECRET": "test_se_secret",
    "SAPLING_API_KEY": "test_sapling_key",
    "RESEMBLE_API_KEY": "test_resemble_key",
    "HF_API_TOKEN": "hf_test_token",
    "AIORNOT_API_KEY": "test_aiornot_key",
}

for _key, _val in _MOCK_ENV.items():
    os.environ.setdefault(_key, _val)
