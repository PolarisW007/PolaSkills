"""Bounded HTTPS client for public monitoring sources.

Module: HTTP transport
Purpose: enforce timeout, response-size and retry limits without external packages
Created: 2026-07-24
Author: Codex
Dependencies: Python standard library
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from .models import SourceError


AddressInfo = tuple[int, int, int, str, tuple]
STANDARD_SECRET_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "x-api-key",
    "api-key",
    "x-auth-token",
}
HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9-]+$")


def _ensure_global_address(address: str) -> None:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return
    if not ip.is_global:
        raise SourceError(
            "unsafe_url", "private or non-global addresses are not allowed"
        )


def validate_public_https_url(url: str) -> None:
    """Reject malformed, credential-bearing and obviously local HTTPS URLs."""
    raw = str(url or "")
    if any(
        ord(character) < 32
        or ord(character) == 127
        or character.isspace()
        for character in raw
    ):
        raise SourceError("unsafe_url", "URL contains forbidden whitespace")
    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise SourceError("unsafe_url", "URL is malformed") from exc
    host = (parts.hostname or "").rstrip(".").lower()
    if parts.scheme != "https" or not host:
        raise SourceError("unsafe_url", "only absolute HTTPS URLs are allowed")
    if parts.username or parts.password:
        raise SourceError("unsafe_url", "URL credentials are not allowed")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise SourceError("unsafe_url", "local hostnames are not allowed")
    try:
        port = parts.port
    except ValueError as exc:
        raise SourceError("unsafe_url", "URL port is invalid") from exc
    if port is not None and not 1 <= port <= 65535:
        raise SourceError("unsafe_url", "URL port is invalid")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise SourceError("unsafe_url", "URL hostname is invalid") from exc
        labels = ascii_host.split(".")
        if (
            len(ascii_host) > 253
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or not HOST_LABEL_RE.fullmatch(label)
                for label in labels
            )
        ):
            raise SourceError("unsafe_url", "URL hostname is invalid")
    _ensure_global_address(host.strip("[]"))


def resolve_public_addresses(host: str, port: int) -> list[AddressInfo]:
    """Resolve once and return only addresses safe to use for this connection."""
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SourceError("dns_error", f"cannot resolve public host: {host}") from exc
    if not addresses:
        raise SourceError("dns_error", f"cannot resolve public host: {host}")
    for item in addresses:
        _ensure_global_address(item[4][0])
    return addresses


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to the exact public IPs returned by the validated DNS lookup."""

    def connect(self) -> None:
        if self._tunnel_host:
            raise OSError("HTTP proxy tunneling is disabled")
        addresses = resolve_public_addresses(self.host, self.port or 443)
        last_error: OSError | None = None
        for family, socktype, proto, _, sockaddr in addresses:
            sock = None
            try:
                sock = socket.socket(family, socktype, proto)
                if self.timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                    sock.settimeout(self.timeout)
                if self.source_address:
                    sock.bind(self.source_address)
                sock.connect(sockaddr)
                self.sock = sock
                break
            except OSError as exc:
                last_error = exc
                if sock is not None:
                    sock.close()
        else:
            if last_error is not None:
                raise last_error
            raise OSError("no public address was connectable")
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class PinnedHTTPSHandler(HTTPSHandler):
    """urllib HTTPS handler using the DNS-pinned connection implementation."""

    def https_open(self, req):
        return self.do_open(
            PinnedHTTPSConnection,
            req,
            context=self._context,
        )


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    return (
        parts.scheme.lower(),
        (parts.hostname or "").rstrip(".").lower(),
        parts.port or 443,
    )


def _redacted_url(url: str) -> str:
    """Keep only origin and path so query-backed secrets never enter errors."""
    parts = urlsplit(str(url or ""))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class SafeRedirectHandler(HTTPRedirectHandler):
    """Revalidate redirects and reject cross-origin provider secret forwarding."""

    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_https_url(newurl)
        sensitive = getattr(req, "_pola_sensitive_headers", frozenset())
        sensitive_url = bool(getattr(req, "_pola_sensitive_url", False))
        if (sensitive or sensitive_url) and _origin(req.full_url) != _origin(newurl):
            raise SourceError(
                "cross_origin_auth_redirect",
                "secret-bearing requests may not redirect to another origin",
                status="blocked",
            )
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected._pola_sensitive_headers = sensitive
            redirected._pola_sensitive_url = sensitive_url
        return redirected


@dataclass(slots=True)
class HttpResponse:
    status: int
    url: str
    headers: dict[str, str]
    body: bytes

    def text(self) -> str:
        content_type = self.headers.get("content-type", "")
        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


class HttpClient:
    """Small injectable HTTP client; tests can replace it with a fake transport."""

    def __init__(
        self,
        *,
        timeout_seconds: int,
        max_response_bytes: int,
        retry_count: int,
        run_deadline_seconds: int | None = None,
        user_agent: str = "PolaWeChatPublicReader/0.1 (+public-monitoring)",
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.retry_count = retry_count
        self.user_agent = user_agent
        self.monotonic = monotonic
        self.deadline = (
            monotonic() + run_deadline_seconds
            if run_deadline_seconds is not None
            else None
        )

    def _remaining(self) -> float:
        if self.deadline is None:
            return float(self.timeout_seconds)
        remaining = self.deadline - self.monotonic()
        if remaining <= 0:
            raise SourceError(
                "run_deadline_exceeded",
                "monitor run exceeded its configured deadline",
            )
        return remaining

    def _timeout(self) -> float:
        return max(0.001, min(float(self.timeout_seconds), self._remaining()))

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        sensitive_headers: set[str] | None = None,
        sensitive_url: bool = False,
    ) -> HttpResponse:
        merged = {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            **dict(headers or {}),
        }
        validate_public_https_url(url)
        supplied = {name.lower() for name in (sensitive_headers or set())}
        supplied.update(
            name.lower()
            for name in merged
            if name.lower() in STANDARD_SECRET_HEADERS
        )
        request = None
        try:
            request = Request(
                url=url,
                data=body,
                headers=merged,
                method=method.upper(),
            )
        except (ValueError, UnicodeError):
            pass
        if request is None:
            raise SourceError(
                "invalid_request_header",
                "request headers are invalid",
            )
        request._pola_sensitive_headers = frozenset(supplied)
        request._pola_sensitive_url = bool(sensitive_url)
        opener = build_opener(
            ProxyHandler({}),
            SafeRedirectHandler(),
            PinnedHTTPSHandler(),
        )
        last_error: SourceError | None = None
        for attempt in range(self.retry_count + 1):
            terminal = False
            try:
                with opener.open(request, timeout=self._timeout()) as response:
                    content_length = response.headers.get("Content-Length")
                    try:
                        declared_size = int(content_length) if content_length else None
                    except ValueError:
                        declared_size = None
                    if declared_size and declared_size > self.max_response_bytes:
                        raise SourceError(
                            "response_too_large",
                            f"response Content-Length exceeds {self.max_response_bytes} bytes",
                        )
                    chunks: list[bytes] = []
                    size = 0
                    reader = getattr(response, "read1", None)
                    if reader is None:
                        reader = response.read
                    while True:
                        self._remaining()
                        chunk = reader(min(64 * 1024, self.max_response_bytes + 1 - size))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > self.max_response_bytes:
                            raise SourceError(
                                "response_too_large",
                                f"response exceeds {self.max_response_bytes} bytes",
                            )
                    payload = b"".join(chunks)
                    final_url = response.geturl()
                    validate_public_https_url(final_url)
                    return HttpResponse(
                        status=int(response.status),
                        url=final_url,
                        headers={key.lower(): value for key, value in response.headers.items()},
                        body=payload,
                    )
            except SourceError as exc:
                last_error = SourceError(
                    exc.code,
                    str(exc)[:500],
                    status=exc.status,
                    http_status=exc.http_status,
                )
                terminal = True
            except HTTPError as exc:
                status = int(exc.code)
                final_url = exc.geturl()
                if status in {401, 403}:
                    last_error = SourceError(
                        "http_blocked",
                        f"upstream returned HTTP {status}",
                        status="blocked",
                        http_status=status,
                    )
                    terminal = True
                else:
                    last_error = SourceError(
                        "rate_limited" if status == 429 else "http_error",
                        f"upstream returned HTTP {status} at {_redacted_url(final_url)}",
                        http_status=status,
                    )
                    terminal = status != 429 and status < 500
            except (URLError, TimeoutError, OSError) as exc:
                last_error = SourceError(
                    "network_error",
                    f"network request failed: {type(exc).__name__}",
                )
            except (ValueError, UnicodeError):
                last_error = SourceError(
                    "invalid_request_header",
                    "request headers are invalid",
                )
                terminal = True
            if terminal:
                break
            if attempt < self.retry_count:
                delay = min(4.0, 0.5 * (2**attempt), self._remaining())
                time.sleep(delay)
        assert last_error is not None
        raise last_error
