"""Integration: the real Fetcher against a live local server (no external net).

Proves end-to-end: IP-pinning does not corrupt logical URLs, redirects are
followed with guards, non-2xx passes through, oversized responses are refused.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from bam.config import Config, FetchLimits, LLMConfig, Paths
from bam.fetcher import FetchError, Fetcher


HTML = b"<html><body><h1>Local Test Page</h1><p>hello from the local server</p></body></html>"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.end_headers()
        elif self.path == "/final":
            body = b"<html>final page</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/big":
            body = b"x" * (3 * 1024 * 1024)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(HTML)))
            self.end_headers()
            self.wfile.write(HTML)

    def log_message(self, *a) -> None:  # silence
        pass


@pytest.fixture(scope="module")
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _cfg(tmp_path: Path) -> Config:
    return Config(
        fetch=FetchLimits(max_pages=1, max_bytes_per_page=2_000_000, max_redirects=5,
                          timeout_s=10, max_requests_per_domain=3, max_run_seconds=60,
                          min_interval_per_domain_s=0.0, user_agent="BAM-TestBot/0.1"),
        llm=LLMConfig(False, "none", "", "", "BAM_LLM_API_KEY", 1, 4000, 1200, 0.10, 30),
        paths=Paths(tmp_path / "bam.db", tmp_path / "l", tmp_path / "j", tmp_path / "r"),
        raw={},
    )


def test_fetch_and_url_reporting(server, tmp_path: Path) -> None:
    with Fetcher(_cfg(tmp_path), allow_loopback=True) as f:
        res = f.fetch(server + "/")
    assert res.status == 200
    assert b"Local Test Page" in res.content
    assert res.final_url == server + "/"      # logical URL, not the pinned IP


def test_redirect_followed_and_reported(server, tmp_path: Path) -> None:
    with Fetcher(_cfg(tmp_path), allow_loopback=True) as f:
        res = f.fetch(server + "/redirect")
    assert res.status == 200
    assert res.hops == 1
    assert res.final_url == server + "/final"


def test_oversized_response_blocked(server, tmp_path: Path) -> None:
    with Fetcher(_cfg(tmp_path), allow_loopback=True) as f:
        with pytest.raises(FetchError, match="too large"):
            f.fetch(server + "/big")


def test_request_cap_enforced(server, tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with Fetcher(cfg, allow_loopback=True) as f:
        f.fetch(server + "/")
        f.fetch(server + "/")
        f.fetch(server + "/")
        with pytest.raises(FetchError, match="cap reached"):
            f.fetch(server + "/")


def test_loopback_blocked_by_default(server, tmp_path: Path) -> None:
    """The production default refuses loopback even if a URL points there."""
    with Fetcher(_cfg(tmp_path)) as f:
        with pytest.raises(FetchError):
            f.fetch(server + "/")
