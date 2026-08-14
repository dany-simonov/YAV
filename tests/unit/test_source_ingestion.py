import socket
from unittest.mock import AsyncMock

import httpx
import httpcore
import pytest

from src.source_ingestion import SourceUnavailableError, _PinnedBackend, PinnedAsyncHTTPTransport, SourceIngestor, pin_source_url, validate_source_url
from src.validation import SecurityValidationError


def public_resolver(host, *_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x", "http://localhost/x", "http://169.254.169.254/",
    "http://10.0.0.1/", "http://[::1]/", "file:///etc/passwd", "https://user:pass@example.com/",
])
async def test_source_url_rejects_ssrf_targets(url):
    with pytest.raises(SecurityValidationError):
        await validate_source_url(url, public_resolver)


@pytest.mark.asyncio
async def test_ingestion_extracts_bounded_text_metadata_and_media():
    html = """<html><head><meta property='og:title' content='Заголовок'><meta property='og:image' content='/cover.jpg'><meta property='og:video' content='https://media.example/video.mp4'></head><body><nav>Меню</nav><article><p>Первый абзац.</p><p>Второй абзац.</p><img src='/body.png'></article></body></html>"""

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=html)

    logs: list[str] = []
    ingestor = SourceIngestor(transport=httpx.MockTransport(handler), resolver=public_resolver)
    document = await ingestor.ingest("https://example.com/article?token=SECRET", diagnostic_log=logs.append)

    assert document.title == "Заголовок"
    assert "Первый абзац" in document.text
    assert "Меню" not in document.text
    assert document.image_urls == ("https://example.com/cover.jpg", "https://example.com/body.png")
    assert document.video_urls == ("https://media.example/video.mp4",)
    assert logs == [
        "source_stage=url_parse",
        "source_stage=url_pinned host=example.com",
        "source_stage=fetch_start host=example.com redirect_count=0",
        "source_stage=fetch_response http_status=200 content_type=text/html redirect_count=0",
        f"source_stage=html_read html_bytes={len(html.encode())}",
        "source_stage=extract_complete text_length=27 image_candidates=2 video_candidates=1",
    ]
    assert all("SECRET" not in item for item in logs)


@pytest.mark.asyncio
async def test_http_error_is_a_controlled_source_error_with_safe_stage_diagnostics():
    logs: list[str] = []

    def handler(_request):
        return httpx.Response(403, headers={"content-type": "text/html; charset=utf-8"})

    ingestor = SourceIngestor(transport=httpx.MockTransport(handler), resolver=public_resolver)
    with pytest.raises(SourceUnavailableError) as raised:
        await ingestor.ingest("https://example.com/article?token=SECRET", diagnostic_log=logs.append)

    assert raised.value.code == "source_unavailable"
    assert logs[-1] == "source_stage=failed source_error_code=source_unavailable"
    assert "source_stage=fetch_response http_status=403 content_type=text/html redirect_count=0" in logs
    assert all("SECRET" not in item for item in logs)


@pytest.mark.asyncio
async def test_redirect_is_revalidated_before_following():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        return httpx.Response(500)

    ingestor = SourceIngestor(transport=httpx.MockTransport(handler), resolver=public_resolver)
    with pytest.raises(SecurityValidationError):
        await ingestor.ingest("https://example.com/article")
    assert calls == ["https://example.com/article"]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://example.com:22/", "https://example.com:6379/", "https://example.com:8080/",
    "https://user:pass@example.com/", "https://[::ffff:127.0.0.1]/", "http://0.0.0.0/",
])
async def test_source_url_rejects_unsafe_port_and_host_representations(url):
    with pytest.raises(SecurityValidationError):
        await validate_source_url(url, public_resolver)


@pytest.mark.asyncio
async def test_default_ports_trailing_dot_idna_and_privacy_are_normalized():
    target = await pin_source_url("https://BÜCHER.example.:443/a?q=secret#fragment", public_resolver)
    assert target.fetch_url == "https://xn--bcher-kva.example:443/a?q=secret"
    assert target.display_url == "https://xn--bcher-kva.example:443/a"


@pytest.mark.asyncio
async def test_pinned_transport_uses_verified_address_without_a_second_dns_lookup():
    calls = 0
    def rebinding_resolver(host, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        address = "8.8.8.8" if calls == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]
    target = await pin_source_url("https://example.com/a?token=secret", rebinding_resolver)
    transport = PinnedAsyncHTTPTransport(target)
    assert transport.pinned_address == "8.8.8.8"
    assert calls == 1
    await transport.aclose()


@pytest.mark.asyncio
async def test_pinned_backend_connects_to_public_ip_not_hostname_or_rebound_answer():
    backend = _PinnedBackend("example.com", "8.8.8.8")
    backend._delegate.connect_tcp = AsyncMock(return_value="stream")
    assert await backend.connect_tcp("example.com", 443) == "stream"
    backend._delegate.connect_tcp.assert_awaited_once_with("8.8.8.8", 443, None, None, None)


@pytest.mark.asyncio
async def test_pinned_backend_rejects_cross_host_connection_before_socket_connect():
    backend = _PinnedBackend("example.com", "8.8.8.8")
    backend._delegate.connect_tcp = AsyncMock()
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("rebound.example", 443)
    backend._delegate.connect_tcp.assert_not_awaited()


@pytest.mark.asyncio
async def test_html_fetch_uses_query_but_document_persists_sanitized_url():
    seen = []
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>hello</p>")
    document = await SourceIngestor(transport=httpx.MockTransport(handler), resolver=public_resolver).ingest(
        "https://example.com/post?id=123&token=SECRET#fragment"
    )
    assert seen == ["https://example.com/post?id=123&token=SECRET"]
    assert document.url == "https://example.com/post"
