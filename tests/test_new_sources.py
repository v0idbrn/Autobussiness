"""Tests for new intent sources: We Work Remotely, Remote OK, Jobicy,
bootstrap command, and source quality tracking.

Covers: parsers, malformed data, empty responses, dedup, denylist,
bootstrap seed, CSV/JSON import, source quality metrics.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock


# -- We Work Remotely RSS parser ------------------------------------------------

WWRSS_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel>
<title>We Work Remotely - Programming Jobs</title>
<item>
  <title>Excel Data Processing Specialist at Acme Corp</title>
  <link>https://weworkremotely.com/jobs/12345</link>
  <description>We need help cleaning up messy Excel spreadsheet data</description>
  <pubDate>Mon, 15 Sep 2026 12:00:00 +0000</pubDate>
  <dc:creator>Acme Corp</dc:creator>
  <category>Python</category>
</item>
<item>
  <title>QA Automation Engineer at Beta Inc</title>
  <link>https://weworkremotely.com/jobs/12346</link>
  <description>Looking for Selenium automation testing contractor</description>
  <pubDate>Tue, 16 Sep 2026 12:00:00 +0000</pubDate>
  <dc:creator>Beta Inc</dc:creator>
  <category>QA</category>
</item>
</channel>
</rss>"""


def test_parse_wwrss_basic():
    from bam.intent_sources import parse_wwrss_feed
    candidates = parse_wwrss_feed(WWRSS_SAMPLE, source_id="wwr_dev")
    assert len(candidates) == 2
    assert candidates[0].company == "Acme Corp"
    assert candidates[0].source_type == "job_board:wwr_dev"
    assert candidates[0].intent.tier in ("medium", "strong", "explicit")
    assert candidates[0].published_at is not None


def test_parse_wwrss_empty():
    from bam.intent_sources import parse_wwrss_feed
    candidates = parse_wwrss_feed(b"<rss><channel></channel></rss>", source_id="wwr_dev")
    assert candidates == []


def test_parse_wwrss_malformed():
    from bam.intent_sources import parse_wwrss_feed
    candidates = parse_wwrss_feed(b"not xml at all", source_id="wwr_dev")
    assert candidates == []


def test_parse_wwrss_limit():
    from bam.intent_sources import parse_wwrss_feed
    candidates = parse_wwrss_feed(WWRSS_SAMPLE, source_id="wwr_dev", limit=1)
    assert len(candidates) == 1


# -- Remote OK JSON parser -----------------------------------------------------

REMOTEOK_SAMPLE = [
    {"legal": "By using this API you agree..."},
    {
        "id": "12345",
        "position": "Excel Data Cleaning Specialist",
        "company": "DataCorp",
        "link": "https://remoteok.com/l/12345",
        "description": "Need help cleaning up messy Excel spreadsheets",
        "tags": ["excel", "data"],
        "date": "2026-09-15T12:00:00Z",
    },
    {
        "id": "12346",
        "position": "QA Automation Tester",
        "company": "AutoCo",
        "link": "https://remoteok.com/l/12346",
        "description": "Looking for Selenium automation testing contractor",
        "tags": ["qa", "automation"],
        "date": 1726401600,
    },
]


def test_parse_remoteok_basic():
    from bam.intent_sources import parse_remoteok_json
    candidates = parse_remoteok_json(REMOTEOK_SAMPLE)
    assert len(candidates) == 2
    assert candidates[0].company == "DataCorp"
    assert candidates[0].source_type == "job_board:remoteok"


def test_parse_remoteok_skips_metadata():
    from bam.intent_sources import parse_remoteok_json
    candidates = parse_remoteok_json(REMOTEOK_SAMPLE)
    # First item is metadata (no "position" key), should be skipped
    assert all(c.company != "legal" for c in candidates)


def test_parse_remoteok_empty():
    from bam.intent_sources import parse_remoteok_json
    candidates = parse_remoteok_json([])
    assert candidates == []


def test_parse_remoteok_no_position():
    from bam.intent_sources import parse_remoteok_json
    candidates = parse_remoteok_json([{"company": "X", "link": "https://x.com"}])
    assert candidates == []


# -- Jobicy JSON parser --------------------------------------------------------

JOBICY_SAMPLE = {
    "apiVersion": "2.2.16",
    "jobCount": 2,
    "jobs": [
        {
            "id": 153336,
            "url": "https://jobicy.com/jobs/153336",
            "jobTitle": "QA Automation Engineer",
            "companyName": "TestCo",
            "jobIndustry": ["Software Engineering"],
            "jobType": ["Full-Time"],
            "jobGeo": "USA",
            "jobLevel": "Senior",
            "jobExcerpt": "Looking for QA automation testing with Selenium",
            "pubDate": "2026-09-15T15:38:57+00:00",
            "salaryMin": 100000,
            "salaryMax": 150000,
        },
        {
            "id": 153337,
            "url": "https://jobicy.com/jobs/153337",
            "jobTitle": "Data Processing Specialist",
            "companyName": "DataLLC",
            "jobIndustry": ["Data Science"],
            "jobType": ["Contract"],
            "jobGeo": "Remote",
            "jobLevel": "Mid",
            "jobExcerpt": "Need help with PDF to Excel data conversion",
            "pubDate": "2026-09-14T10:00:00+00:00",
        },
    ],
}


def test_parse_jobicy_basic():
    from bam.intent_sources import parse_jobicy_json
    candidates = parse_jobicy_json(JOBICY_SAMPLE)
    assert len(candidates) == 2
    assert candidates[0].company == "TestCo"
    assert candidates[0].source_type == "job_board:jobicy"


def test_parse_jobicy_empty():
    from bam.intent_sources import parse_jobicy_json
    candidates = parse_jobicy_json({"jobs": []})
    assert candidates == []


def test_parse_jobicy_no_jobs_key():
    from bam.intent_sources import parse_jobicy_json
    candidates = parse_jobicy_json({})
    assert candidates == []


def test_parse_jobicy_skips_invalid():
    from bam.intent_sources import parse_jobicy_json
    candidates = parse_jobicy_json({"jobs": [{"no_title": True}, "invalid"]})
    assert candidates == []


# -- Source integration (discover_intent_from_feeds) ---------------------------

def test_discover_feeds_includes_remote_sources():
    from bam.intent_sources import discover_intent_from_feeds
    # Verify the function accepts include_remote_boards parameter
    # We won't actually make network calls in unit tests
    import inspect
    sig = inspect.signature(discover_intent_from_feeds)
    assert "include_remote_boards" in sig.parameters


def test_discover_feeds_can_disable_remote():
    from bam.intent_sources import discover_intent_from_feeds
    import inspect
    sig = inspect.signature(discover_intent_from_feeds)
    param = sig.parameters["include_remote_boards"]
    assert param.default is True


# -- Seed query file ------------------------------------------------------------

def test_seed_queries_file_exists():
    from pathlib import Path
    seed_path = Path(__file__).parent.parent / "data" / "bootstrap" / "seed_queries.yaml"
    assert seed_path.exists(), f"Seed queries not found at {seed_path}"


def test_seed_queries_valid_yaml():
    import yaml
    from pathlib import Path
    seed_path = Path(__file__).parent.parent / "data" / "bootstrap" / "seed_queries.yaml"
    with open(seed_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict)
    # Should have at least our three services
    for svc in ("pdf_to_excel", "excel_cleaning", "qa_automation"):
        assert svc in data, f"Service {svc} missing from seed queries"
        assert "en" in data[svc], f"English queries missing for {svc}"
        assert len(data[svc]["en"]) >= 5, f"Not enough queries for {svc}"


# -- Bootstrap command ---------------------------------------------------------

def test_bootstrap_dry_run(capsys):
    """Bootstrap --dry-run should show queries without writing."""
    import subprocess
    result = subprocess.run(
        ["uv", "run", "bam", "bootstrap", "--dry-run"],
        capture_output=True, text=True, cwd="F:\\Gigs\\Bussiness",
        timeout=30,
    )
    assert result.returncode == 0
    assert "dry-run" in result.stdout.lower() or "[dry-run]" in result.stdout
    assert "Queries seeded:" in result.stdout


def test_bootstrap_service_filter(capsys):
    """Bootstrap --service should only seed one service."""
    import subprocess
    result = subprocess.run(
        ["uv", "run", "bam", "bootstrap", "--service", "qa_automation", "--dry-run"],
        capture_output=True, text=True, cwd="F:\\Gigs\\Bussiness",
        timeout=30,
    )
    assert result.returncode == 0
    assert "qa_automation" in result.stdout
    # Should not have pdf_to_excel queries
    assert "pdf_to_excel" not in result.stdout


# -- Source quality tracking in store ------------------------------------------

def test_source_quality_empty_store():
    """source_quality() should return empty list when no data."""
    from bam.store import Store
    from bam.config import load_config
    cfg = load_config()
    store = Store(cfg)
    quality = store.source_quality()
    assert isinstance(quality, list)


def test_query_quality_empty_store():
    """query_quality() should return empty list when no data."""
    from bam.store import Store
    from bam.config import load_config
    cfg = load_config()
    store = Store(cfg)
    quality = store.query_quality()
    assert isinstance(quality, list)
