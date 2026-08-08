"""Unit tests for Appwrite Function runtime integration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main import _download_file_bytes, main


def test_main_passes_dynamic_api_key_from_request_headers():
    context = SimpleNamespace(
        req=SimpleNamespace(
            body_json={"fileId": "file-id", "mediaType": "image"},
            headers={"X-Appwrite-Key": "runtime-key"},
        ),
        res=SimpleNamespace(json=lambda payload, status=200: (payload, status)),
    )

    with patch("src.main._analyze", return_value=object()) as analyze_mock, patch(
        "src.main._run_coro_sync", return_value={"verdict": "UNCERTAIN"}
    ):
        main(context)

    analyze_mock.assert_called_once_with(
        {"fileId": "file-id", "mediaType": "image"}, "runtime-key"
    )


@pytest.mark.asyncio
async def test_download_prefers_dynamic_api_key_over_environment(monkeypatch):
    monkeypatch.setenv("APPWRITE_FUNCTION_API_ENDPOINT", "https://appwrite.example/v1")
    monkeypatch.setenv("APPWRITE_FUNCTION_PROJECT_ID", "project-id")
    monkeypatch.setenv("APPWRITE_FUNCTION_API_KEY", "fallback-key")

    response = MagicMock(status_code=200, content=b"file-bytes")
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.main.httpx.AsyncClient", return_value=client):
        result = await _download_file_bytes("file-id", "bucket-id", "runtime-key")

    assert result == b"file-bytes"
    assert client.get.await_args.kwargs["headers"]["X-Appwrite-Key"] == "runtime-key"
