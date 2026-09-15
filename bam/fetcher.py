"""Guarded HTTP fetcher (httpx-only per plan v3.1 §4).

Security posture:
- scheme allowlist (http/https only)
- SSRF defense: resolve every hostname and refuse private/loopback/link-local/
  reserved addresses; the connection is pinned to the verified public IP
  (anti-DNS-rebinding) using httpx's sni_hostname extension
- redirects are followed MANUALLY so every hop passes the same guards
- hard caps from config: bytes, redirects, timeout, per-domain requests
- honest User-Agent; robots.txt awareness at pipeline level
"""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from bam.config import Config, load_config
from bam.evidence import sha256_bytes

_ALLOWED_SCHEMES = {"http", "https"}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class FetchError(RuntimeError):
    """Blocked or failed fetch (structured reason carried in message)."""


def validate_url(url: str) -> str:
    """Syntactic + scheme validation. Returns normalized URL."""
    parsed = urlparse(url.strip())
    if any(ch in url for ch in "\r\n\x00"):
        raise FetchError(f"control characters in URL are not allowed: {url!r}")
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise FetchError(f"scheme not allowed: {parsed.scheme!r} in {url!r}")
    if not parsed.hostname:
        raise FetchError(f"no hostname in {url!r}")
    if parsed.username or parsed.password:
        raise FetchError(f"credentials in URL are not allowed: {url!r}")
    try:
        parsed.port  # access validates the port range (0-65535)
    except ValueError as exc:
        raise FetchError(f"invalid port in {url!r}: {exc}")
    return url.strip()


def _ip_is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    # Global + not in any local/reserved range. is_global alone already covers
    # private/loopback/link-local/reserved/multicast for IPv4 and IPv6
    # (including IPv4-mapped ::ffff:10.0.0.5 forms and ::1).
    return addr.is_global and not (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_multicast or addr.is_unspecified
    )


def resolve_public_host(hostname: str) -> str:
    """Resolve hostname and require at least one public address.

    Returns the pinned public IP (used for the connection to prevent rebinding).
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise FetchError(f"DNS resolution failed for {hostname!r}: {exc}") from exc
    addrs = {info[4][0] for info in infos}
    public = [a for a in addrs if _ip_is_public(a)]
    if not public:
        raise FetchError(f"host {hostname!r} resolves to non-public address(es); blocked")
    return public[0]


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status: int
    content: bytes
    sha256: str
    elapsed_s: float
    hops: int


class Fetcher:
    def __init__(self, config: Config | None = None,
                 *, allow_loopback: bool = False) -> None:
        """allow_loopback=True is an explicit opt-in for local fixture servers
        (tests, local demos). Production research runs never set it."""
        self.config = config or load_config()
        self.allow_loopback = allow_loopback
        limits = self.config.fetch
        # follow_redirects=False: redirects are followed manually with full guards.
        self._client = httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(limits.timeout_s),
            headers={"User-Agent": limits.user_agent},
        )
        self._requests_made = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _guarded_get(self, url: str) -> httpx.Response:
        """Single guarded request: validate, resolve, pin IP, send."""
        url = validate_url(url)
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if self.allow_loopback:
            pinned_ip = "127.0.0.1"  # explicit local-server opt-in (tests/demos)
        else:
            pinned_ip = resolve_public_host(hostname)

        self._requests_made += 1
        if self._requests_made > self.config.fetch.max_requests_per_domain:
            raise FetchError(
                f"per-run request cap reached ({self.config.fetch.max_requests_per_domain})"
            )

        request = self._client.build_request("GET", url)
        # Pin the connection to the verified public IP: replace the URL host,
        # restore the original Host header, and keep TLS identity via SNI.
        # (IPv6 literals are not pinned; the resolve check above still guards.)
        if ":" not in pinned_ip:  # IPv4 only
            authority = parsed.netloc
            request.url = request.url.copy_with(host=pinned_ip)
            request.headers["Host"] = authority
        if url.lower().startswith("https"):
            request.extensions["sni_hostname"] = hostname
        try:
            return self._client.send(request)
        except httpx.TooManyRedirects as exc:  # pragma: no cover - manual mode
            raise FetchError(f"redirect limit exceeded for {url!r}") from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"fetch failed for {url!r}: {exc}") from exc

    def fetch(self, url: str) -> FetchResult:
        """Fetch a URL under all guards, following redirects manually."""
        start = time.monotonic()
        current = validate_url(url)
        hops = 0
        max_redirects = self.config.fetch.max_redirects
        while True:
            response = self._guarded_get(current)
            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("Location")
                if not location:
                    break
                hops += 1
                if hops > max_redirects:
                    raise FetchError(f"redirect limit exceeded starting at {url!r}")
                current = str(httpx.URL(current).join(location))
                continue
            break

        elapsed = time.monotonic() - start
        if elapsed > self.config.fetch.max_run_seconds:
            raise FetchError(f"fetch exceeded max_run_seconds for {url!r}")

        content = response.content
        if len(content) > self.config.fetch.max_bytes_per_page:
            raise FetchError(
                f"response too large for {url!r}: "
                f"{len(content)} > {self.config.fetch.max_bytes_per_page}"
            )
        # NOTE: report the logical URL chain, not response.url - the request URL
        # is mutated to the pinned IP for the connection and must not leak into
        # evidence or reports.
        return FetchResult(
            url=url,
            final_url=current,
            status=response.status_code,
            content=content,
            sha256=sha256_bytes(content),
            elapsed_s=round(elapsed, 3),
            hops=hops,
        )
