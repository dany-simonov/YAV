"""Typed technical-failure contracts for real provider adapters."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.exceptions import ExternalAPIError, ProviderInfrastructureError


def test_external_api_error_new_diagnostics_are_optional_and_backward_compatible():
    legacy = ExternalAPIError("sapling", "request_error")
    enriched = ExternalAPIError("aiornot", "request_error", status_code=422, provider_message="safe")
    assert (legacy.service, legacy.detail, legacy.status_code, legacy.provider_message) == (
        "sapling", "request_error", None, None
    )
    assert (enriched.status_code, enriched.provider_message) == (422, "safe")


def _client(*, response=None, error=None):
    instance = AsyncMock()
    instance.post = AsyncMock(return_value=response, side_effect=error)
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    return instance


def _response(status_code: int, body: object = None):
    response = MagicMock(status_code=status_code)
    response.json.return_value = body
    return response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_path", "data", "service"),
    [
        ("adapters.sightengine.SightengineAdapter", b"image", "sightengine"),
        ("adapters.sapling.SaplingAdapter", b"x" * 60, "sapling"),
        ("adapters.resemble.ResembleAdapter", b"WAV", "resemble"),
        ("adapters.hf_image.HFImageAdapter", b"image", "huggingface"),
        ("adapters.hf_audio.HFAudioAdapter", b"WAV", "huggingface"),
    ],
)
async def test_timeout_is_typed_provider_infrastructure_failure(adapter_path, data, service):
    module_name, class_name = adapter_path.rsplit(".", 1)
    module = __import__(module_name, fromlist=[class_name])
    adapter = getattr(module, class_name)()
    with patch("httpx.AsyncClient", return_value=_client(error=httpx.ReadTimeout("x"))):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await adapter.analyze(data)
    assert (raised.value.service, raised.value.kind) == (service, "timeout")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_path", "data", "service"),
    [
        ("adapters.sightengine.SightengineAdapter", b"image", "sightengine"),
        ("adapters.sapling.SaplingAdapter", b"x" * 60, "sapling"),
        ("adapters.resemble.ResembleAdapter", b"WAV", "resemble"),
        ("adapters.hf_image.HFImageAdapter", b"image", "huggingface"),
        ("adapters.hf_audio.HFAudioAdapter", b"WAV", "huggingface"),
    ],
)
async def test_transport_error_is_typed_provider_infrastructure_failure(adapter_path, data, service):
    module_name, class_name = adapter_path.rsplit(".", 1)
    module = __import__(module_name, fromlist=[class_name])
    adapter = getattr(module, class_name)()
    with patch("httpx.AsyncClient", return_value=_client(error=httpx.ConnectError("x"))):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await adapter.analyze(data)
    assert (raised.value.service, raised.value.kind) == (service, "transport")


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [500, 502, 503])
async def test_provider_5xx_is_typed_unavailable(status_code):
    from adapters.sapling import SaplingAdapter

    with patch("httpx.AsyncClient", return_value=_client(response=_response(status_code))):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await SaplingAdapter().analyze(b"x" * 60)
    assert raised.value.kind == "unavailable"


@pytest.mark.asyncio
async def test_malformed_success_payload_is_typed_infrastructure_failure():
    from adapters.sightengine import SightengineAdapter

    with patch("httpx.AsyncClient", return_value=_client(response=_response(200, {"status": "success"}))):
        with pytest.raises(ProviderInfrastructureError) as raised:
            await SightengineAdapter().analyze(b"image")
    assert raised.value.kind == "invalid_response"


@pytest.mark.asyncio
async def test_provider_4xx_is_not_misclassified_as_unavailable():
    from adapters.hf_image import HFImageAdapter

    with patch("httpx.AsyncClient", return_value=_client(response=_response(400, {"error": "bad input"}))):
        with pytest.raises(ExternalAPIError) as raised:
            await HFImageAdapter().analyze(b"image")
    assert not isinstance(raised.value, ProviderInfrastructureError)
    assert raised.value.detail == "request_error"


@pytest.mark.asyncio
async def test_sapling_ordinary_4xx_is_an_external_api_error():
    from adapters.sapling import SaplingAdapter

    with patch("httpx.AsyncClient", return_value=_client(response=_response(422))):
        with pytest.raises(ExternalAPIError) as raised:
            await SaplingAdapter().analyze(b"x" * 60)
    assert (raised.value.service, raised.value.detail) == ("sapling", "request_error")
