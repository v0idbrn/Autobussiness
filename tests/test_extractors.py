"""Extractor tests: deterministic homepage signal extraction (offline fixture)."""

from __future__ import annotations

from pathlib import Path

from bam.extractors import extract_signals

FIXTURE = Path(__file__).parent / "fixtures" / "acme_homepage.html"


def test_extract_signals_full() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    s = extract_signals(html, keep_emails=True)

    assert s["title"] and "Acme Data Services" in s["title"]
    assert s["organization"]["name"] == "Acme Data Services"
    assert s["organization"]["email"] == "info@acme-example.test"
    assert "info@acme-example.test" in s["emails"]
    assert any("wordpress" in t.lower() for t in s["technologies"])
    assert any("hubspot" in t.lower() for t in s["technologies"])
    assert s["has_contact_form"] is True
    assert s["mailto_count"] == 1
    assert s["pricing_url"] is not None
    assert s["contact_url"] is not None
    # closed-enum keyword services/problems are OBSERVED claims
    assert "data-cleaning" in s["keyword_services"]
    assert "pdf-extraction" in s["keyword_services"]
    # script content must not leak into text signals
    assert "must be skipped" not in str(s)


def test_extract_signals_without_emails() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    s = extract_signals(html, keep_emails=False)
    assert s["emails"] == []


def test_malformed_html_does_not_crash() -> None:
    s = extract_signals("<html><body><p>unclosed <b>tags<p>galore")
    assert isinstance(s, dict)


def test_empty_page() -> None:
    s = extract_signals("<html></html>")
    assert s["keyword_services"] == []
    assert s["has_contact_form"] is False
