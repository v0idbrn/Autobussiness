"""Free web search module using DuckDuckGo (ddgs).

Provides web search capability without API keys.
Rate limits: ~20-30 requests/minute (DuckDuckGo soft limit).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ddgs import DDGS


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    body: str


class WebSearchError(RuntimeError):
    """Web search failed."""


class WebSearcher:
    """DuckDuckGo web searcher with rate limiting."""

    def __init__(self, min_interval_s: float = 2.0, max_results: int = 10):
        self.min_interval_s = min_interval_s
        self.max_results = max_results
        self._last_request_time = 0.0
        self._request_count = 0

    def search(self, query: str, max_results: int | None = None) -> list[SearchResult]:
        """Search DuckDuckGo for the given query.

        Args:
            query: Search query string
            max_results: Maximum results to return (default: self.max_results)

        Returns:
            List of SearchResult objects
        """
        # Rate limiting
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)

        self._last_request_time = time.monotonic()
        self._request_count += 1

        try:
            results = DDGS().text(
                query,
                max_results=max_results or self.max_results,
            )
            return [
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("href", ""),
                    body=r.get("body", ""),
                )
                for r in results
            ]
        except Exception as exc:
            raise WebSearchError(f"search failed for {query!r}: {exc}") from exc

    @property
    def request_count(self) -> int:
        return self._request_count
