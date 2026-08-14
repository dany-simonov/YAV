"""Bounded and pinned ingestion of public HTML sources.

DNS is resolved exactly once for every navigation target.  The selected public
address is passed to httpcore's TCP backend while the request URL keeps its
original hostname, so HTTPS still verifies the certificate/SNI for that host.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpcore
import httpx
from httpx._config import create_ssl_context
from httpx._transports.default import AsyncResponseStream, map_httpcore_exceptions

from src.execution_deadline import bounded_timeout
from src.validation import MAX_FILE_BYTES, SecurityValidationError

MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 10_000
MAX_IMAGES = 3
MAX_REDIRECTS = 3
USER_AGENT = "YAV-Source-Analyzer/1.0 (+https://yav.example)"
_DNS_SEMAPHORE = asyncio.Semaphore(8)
_SAFE_SOURCE_ERROR_CODES = frozenset({
    "invalid_source_url", "unsafe_source_url", "source_unavailable",
    "source_timeout", "unsupported_source", "source_too_large",
    "source_no_analyzable_content",
})


class SourceUnavailableError(SecurityValidationError):
    def __init__(self, code: str = "source_unavailable", detail: str = "Источник временно недоступен.") -> None:
        super().__init__(code, detail, 422)


def _source_diagnostic(diagnostic_log: object | None, message: str) -> None:
    """Best-effort source telemetry; messages must not contain URLs or page data."""
    if not callable(diagnostic_log):
        return
    try:
        diagnostic_log(message)
    except Exception:
        pass


def _safe_content_type(headers: httpx.Headers) -> str:
    """Return an allowlisted media type so a hostile header cannot enter logs."""
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type in {
        "text/html", "application/xhtml+xml", "text/plain", "application/octet-stream",
    }:
        return content_type
    return "other"


def _safe_source_error_code(code: object) -> str:
    return str(code) if str(code) in _SAFE_SOURCE_ERROR_CODES else "source_unavailable"


@dataclass(frozen=True)
class PinnedTarget:
    fetch_url: str
    display_url: str
    hostname: str
    address: str


def _canonical_host(host: str) -> str:
    return host.rstrip(".").lower().encode("idna").decode("ascii")


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    # ``is_global`` rejects loopback, private, link-local, CGNAT, multicast,
    # reserved/documentation and unspecified ranges, including mapped IPv6.
    mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    return bool(address.is_global and mapped is None)


async def _resolve_addresses(host: str, resolver: Callable[..., object]) -> tuple[str, ...]:
    try:
        async with _DNS_SEMAPHORE:
            records = await asyncio.wait_for(
                asyncio.to_thread(resolver, host, None, type=socket.SOCK_STREAM),
                timeout=min(3.0, bounded_timeout(3.0)),
            )
    except (OSError, TimeoutError) as exc:
        raise SourceUnavailableError() from exc
    addresses = tuple(sorted({item[4][0] for item in records if len(item) > 4 and item[4]}))
    if not addresses or any(not _public_address(address) for address in addresses):
        raise SecurityValidationError("unsafe_source_url", "Адрес источника недопустим.")
    return addresses


async def resolve_public_host(host: str, resolver: Callable[..., object] = socket.getaddrinfo) -> str:
    normalized = _canonical_host(host)
    if not normalized or normalized == "localhost" or normalized.endswith(".localhost"):
        raise SecurityValidationError("unsafe_source_url", "Адрес источника недопустим.")
    try:
        literal = ipaddress.ip_address(normalized)
    except ValueError:
        literal = None
    if literal is not None:
        if not _public_address(str(literal)):
            raise SecurityValidationError("unsafe_source_url", "Адрес источника недопустим.")
        return str(literal)
    return (await _resolve_addresses(normalized, resolver))[0]


def _normalized_url(value: str, *, retain_query: bool) -> tuple[str, str]:
    if not isinstance(value, str) or len(value) > 2048:
        raise SecurityValidationError("invalid_source_url", "Некорректная ссылка на источник.")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise SecurityValidationError("invalid_source_url", "Некорректная ссылка на источник.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise SecurityValidationError("invalid_source_url", "Некорректная ссылка на источник.")
    default_port = 80 if parsed.scheme == "http" else 443
    if port is not None and port != default_port:
        raise SecurityValidationError("unsafe_source_url", "Порт источника недопустим.")
    host = _canonical_host(parsed.hostname)
    # Build URL from canonical host, never the possibly misleading netloc.
    authority = host if ":" not in host else f"[{host}]"
    if port is not None:
        authority = f"{authority}:{port}"
    fetch = urlunsplit((parsed.scheme.lower(), authority, parsed.path or "/", parsed.query if retain_query else "", ""))
    display = urlunsplit((parsed.scheme.lower(), authority, parsed.path or "/", "", ""))
    return fetch, display


async def pin_source_url(
    value: str,
    resolver: Callable[..., object] = socket.getaddrinfo,
    diagnostic_log: object | None = None,
) -> PinnedTarget:
    _source_diagnostic(diagnostic_log, "source_stage=url_parse")
    fetch_url, display_url = _normalized_url(value, retain_query=True)
    host = urlsplit(fetch_url).hostname
    assert host is not None
    target = PinnedTarget(fetch_url, display_url, _canonical_host(host), await resolve_public_host(host, resolver))
    _source_diagnostic(diagnostic_log, f"source_stage=url_pinned host={target.hostname}")
    return target


async def validate_source_url(value: str, resolver: Callable[..., object] = socket.getaddrinfo) -> str:
    """Compatibility validator. Production requests use ``pin_source_url``."""
    return (await pin_source_url(value, resolver)).fetch_url


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    """Connect only to prevalidated addresses; TLS is still hostname based."""
    def __init__(self, hostname: str, address: str) -> None:
        self.hostname, self.address = hostname, address
        self._delegate = httpcore.AnyIOBackend()

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None, socket_options=None):
        if _canonical_host(host) != self.hostname:
            raise httpcore.ConnectError("unexpected source host")
        return await self._delegate.connect_tcp(self.address, port, timeout, local_address, socket_options)

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options=None):
        raise httpcore.ConnectError("unix sockets are not permitted")

    async def sleep(self, seconds: float) -> None:
        await self._delegate.sleep(seconds)


class PinnedAsyncHTTPTransport(httpx.AsyncBaseTransport):
    """Small HTTPX transport with IP-pinned TCP and normal CA/TLS validation."""
    def __init__(self, target: PinnedTarget) -> None:
        self.pinned_host, self.pinned_address = target.hostname, target.address
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=create_ssl_context(verify=True, cert=None, trust_env=False),
            max_connections=1, max_keepalive_connections=0, http1=True, http2=False,
            network_backend=_PinnedBackend(target.hostname, target.address),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        req = httpcore.Request(method=request.method, url=httpcore.URL(
            scheme=request.url.raw_scheme, host=request.url.raw_host, port=request.url.port, target=request.url.raw_path,
        ), headers=request.headers.raw, content=request.stream, extensions=request.extensions)
        with map_httpcore_exceptions():
            response = await self._pool.handle_async_request(req)
        return httpx.Response(response.status, headers=response.headers, stream=AsyncResponseStream(response.stream), extensions=response.extensions)

    async def aclose(self) -> None:
        await self._pool.aclose()


@dataclass(frozen=True)
class SourceDocument:
    url: str
    title: str
    description: str
    site_name: str
    text: str
    image_urls: tuple[str, ...]
    video_urls: tuple[str, ...]
    text_truncated: bool


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}; self.images: list[str] = []; self.videos: list[str] = []; self.parts: list[str] = []; self._skip = 0
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key.lower(): value or "" for key, value in attrs}
        if tag in {"script", "style", "noscript", "nav", "footer", "header", "aside"}: self._skip += 1
        if tag == "meta":
            key = (data.get("property") or data.get("name") or "").lower()
            if key in {"og:title", "twitter:title", "description", "og:description", "twitter:description", "og:site_name", "og:image", "twitter:image", "og:video", "og:video:url", "twitter:player:stream", "og:url"}: self.meta.setdefault(key, data.get("content", ""))
        elif tag == "img" and data.get("src"): self.images.append(data["src"])
        elif tag in {"video", "source"} and data.get("src"): self.videos.append(data["src"])
        elif tag in {"p", "article", "main", "h1", "h2", "li", "br"}: self.parts.append(" ")
    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "nav", "footer", "header", "aside"} and self._skip: self._skip -= 1
    def handle_data(self, data: str) -> None:
        if not self._skip: self.parts.append(data)


def _absolute_urls(values: list[str], base: str, *, limit: int) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if not value:
            continue
        try: candidate, _ = _normalized_url(urljoin(base, value), retain_query=True)
        except (SecurityValidationError, ValueError): continue
        if candidate not in result: result.append(candidate)
        if len(result) >= limit: break
    return tuple(result)


class SourceIngestor:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None, resolver: Callable[..., object] = socket.getaddrinfo) -> None:
        self.transport, self.resolver = transport, resolver

    async def _request(
        self,
        url: str,
        *,
        max_bytes: int,
        accept: str,
        deadline_at: float | None = None,
        diagnostic_log: object | None = None,
    ) -> tuple[httpx.Response, bytes, PinnedTarget]:
        current = url
        redirects = 0
        while True:
            if deadline_at is not None and deadline_at <= time.monotonic():
                raise SourceUnavailableError("source_timeout", "Не удалось получить содержимое источника вовремя.")
            target = await pin_source_url(current, self.resolver, diagnostic_log)
            headers = {"User-Agent": USER_AGENT, "Accept": accept}
            remaining = deadline_at - time.monotonic() if deadline_at is not None else float("inf")
            if remaining <= 0:
                raise SourceUnavailableError("source_timeout", "Не удалось получить содержимое источника вовремя.")
            timeout = httpx.Timeout(connect=min(5.0, bounded_timeout(5.0), remaining), read=min(12.0, bounded_timeout(12.0), remaining), write=min(5.0, remaining), pool=min(5.0, remaining))
            transport = self.transport or PinnedAsyncHTTPTransport(target)
            _source_diagnostic(
                diagnostic_log,
                f"source_stage=fetch_start host={target.hostname} redirect_count={redirects}",
            )
            async with httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=timeout, headers=headers, trust_env=False) as client:
                async with client.stream("GET", target.fetch_url) as response:
                    _source_diagnostic(
                        diagnostic_log,
                        "source_stage=fetch_response "
                        f"http_status={response.status_code} content_type={_safe_content_type(response.headers)} "
                        f"redirect_count={redirects}",
                    )
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location: raise SourceUnavailableError("unsupported_source", "Источник вернул некорректный redirect.")
                        if redirects >= MAX_REDIRECTS: raise SourceUnavailableError("unsupported_source", "Слишком много перенаправлений.")
                        redirects += 1; current = urljoin(target.fetch_url, location); continue
                    if response.status_code >= 400: raise SourceUnavailableError()
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes: raise SourceUnavailableError("source_too_large", "Источник превышает допустимый размер.")
                    chunks: list[bytes] = []; total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes: raise SourceUnavailableError("source_too_large", "Источник превышает допустимый размер.")
                        chunks.append(chunk)
                    return response, b"".join(chunks), target

    async def ingest(self, source_url: str, *, diagnostic_log: object | None = None) -> SourceDocument:
        try:
            response, body, target = await self._request(
                source_url,
                max_bytes=MAX_HTML_BYTES,
                accept="text/html,application/xhtml+xml",
                diagnostic_log=diagnostic_log,
            )
            _source_diagnostic(diagnostic_log, f"source_stage=html_read html_bytes={len(body)}")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                raise SourceUnavailableError("unsupported_source", "Источник не содержит HTML-страницу.")
            parser = _Extractor(); parser.feed(body.decode("utf-8", errors="replace"))
            text = " ".join("".join(parser.parts).split()); truncated = len(text) > MAX_TEXT_CHARS
            safe_url = target.display_url
            canonical = parser.meta.get("og:url", "")
            if canonical:
                try: safe_url = (await pin_source_url(urljoin(target.fetch_url, canonical), self.resolver)).display_url
                except SecurityValidationError: pass
            image_urls = _absolute_urls([parser.meta.get("og:image", ""), parser.meta.get("twitter:image", ""), *parser.images], target.fetch_url, limit=MAX_IMAGES)
            video_urls = _absolute_urls([parser.meta.get("og:video", ""), parser.meta.get("og:video:url", ""), parser.meta.get("twitter:player:stream", ""), *parser.videos], target.fetch_url, limit=1)
            _source_diagnostic(
                diagnostic_log,
                "source_stage=extract_complete "
                f"text_length={len(text)} image_candidates={len(image_urls)} video_candidates={len(video_urls)}",
            )
            return SourceDocument(safe_url, parser.meta.get("og:title") or parser.meta.get("twitter:title") or "", parser.meta.get("og:description") or parser.meta.get("description") or parser.meta.get("twitter:description") or "", parser.meta.get("og:site_name", ""), text[:MAX_TEXT_CHARS], image_urls, video_urls, truncated)
        except SecurityValidationError as exc:
            _source_diagnostic(diagnostic_log, f"source_stage=failed source_error_code={_safe_source_error_code(exc.code)}")
            raise
        except (httpx.HTTPError, httpcore.HTTPError, OSError, TimeoutError) as exc:
            _source_diagnostic(diagnostic_log, "source_stage=failed source_error_code=source_unavailable")
            raise SourceUnavailableError() from exc

    async def download_media(self, url: str, *, timeout_seconds: float | None = None) -> tuple[bytes, str]:
        deadline_at = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        response, body, _ = await self._request(url, max_bytes=MAX_FILE_BYTES, accept="image/*,video/*", deadline_at=deadline_at)
        return body, response.headers.get("content-type", "").split(";", 1)[0].lower()
