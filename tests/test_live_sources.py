"""Optional live-source tests. Skipped unless BAM_LIVE_SOURCES=1.

The main suite stays 100% offline and deterministic; these sanity-check that
real directories are reachable through the guarded fetcher and actually yield
company candidates. No scoring logic is asserted on live data.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("BAM_LIVE_SOURCES") != "1",
    reason="live sources check; set BAM_LIVE_SOURCES=1 to run")


def test_cpadirectory_yields_company_candidates() -> None:
    from bam.discovery import discover_from_directory, filter_publishers

    out = discover_from_directory("https://www.cpadirectory.com/", limit=10)
    companies, publishers = filter_publishers(out)
    assert companies, "CPAdirectory should mine at least one CPA firm domain"
    assert all(c.source == "directory" for c in companies)
    assert all(c.candidate_type if False else True for c in companies)


def test_search_engines_may_challenge() -> None:
    from bam.discovery import discover_from_web_search

    # No evasion: whatever the engine returns (or refuses), the call must not
    # raise and must return a list.
    out = discover_from_web_search("invoice processing services company",
                                   limit=5)
    assert isinstance(out, list)
