"""Fetcher guard tests (offline: validation + SSRF resolution only)."""

from __future__ import annotations

import pytest

from bam.fetcher import FetchError, validate_url


def test_valid_https_url_passes() -> None:
    assert validate_url("https://example.com") == "https://example.com"
    assert validate_url("  http://example.com/x  ") == "http://example.com/x"


def test_non_http_schemes_blocked() -> None:
    for url in ("ftp://example.com", "file:///etc/passwd", "javascript:alert(1)",
                "data:text/html,hi"):
        with pytest.raises(FetchError):
            validate_url(url)


def test_credentials_in_url_blocked() -> None:
    with pytest.raises(FetchError):
        validate_url("https://user:pass@example.com")


def test_missing_host_blocked() -> None:
    with pytest.raises(FetchError):
        validate_url("https://")


def test_loopback_hostname_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """localhost resolves to 127.0.0.1 -> must be refused before any request."""
    from bam.fetcher import resolve_public_host

    def fake_getaddrinfo(host, port, *a, **kw):
        return [(2, 1, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(FetchError):
        resolve_public_host("localhost")


def test_private_range_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    from bam.fetcher import resolve_public_host

    def fake_getaddrinfo(host, port, *a, **kw):
        return [(2, 1, 6, "", ("192.168.1.10", 0))]

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(FetchError):
        resolve_public_host("intranet.example")


def test_link_local_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    from bam.fetcher import resolve_public_host

    def fake_getaddrinfo(host, port, *a, **kw):
        return [(2, 1, 6, "", ("169.254.169.254", 0))]  # cloud metadata endpoint

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(FetchError):
        resolve_public_host("metadata.internal")


def test_dns_failure_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    from bam.fetcher import resolve_public_host

    def boom(host, port, *a, **kw):
        raise OSError("no dns")

    monkeypatch.setattr("socket.getaddrinfo", boom)
    with pytest.raises(FetchError):
        resolve_public_host("nonexistent.invalid")


def test_public_ip_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    from bam.fetcher import resolve_public_host

    def fake_getaddrinfo(host, port, *a, **kw):
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
    assert resolve_public_host("example.com") == "93.184.216.34"
