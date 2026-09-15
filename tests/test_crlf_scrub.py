"""Deep CRLF scrub: untrusted text must never carry line-break fragments into
single-line sinks — .eml header values, markdown table cells, evidence rows.

Covers the full hostile alphabet: CR, LF, VT, FF, NEL (0x85), U+2028, U+2029,
NUL. Complements test_redteam.py's boundary-level tests.
"""

from __future__ import annotations

import pytest

from bam.cli import _header_safe
from bam.evidence import redact
from bam.fetcher import FetchError, validate_url
from bam.reporting import render_report

FORBIDDEN = "\r\n\x0b\x0c\x85\u2028\u2029\x00"

_HOSTILE = [
    "a\r\nBcc: victim@evil.test",
    "a\nX-Evil: 1",
    "a\rb",
    "a\x0bb",
    "a\x0cb",
    "a\x85b",
    "a\u2028b",
    "a\u2029b",
    "a\x00b",
]


def test_header_safe_collapses_every_break() -> None:
    for hostile in _HOSTILE:
        out = _header_safe(hostile)
        assert not any(ch in out for ch in FORBIDDEN)
        assert out.startswith("a")  # content preserved, structure collapsed


def test_redact_scrubs_excerpts() -> None:
    for hostile in _HOSTILE:
        out = redact(hostile, keep_emails=True)
        assert not any(ch in out for ch in FORBIDDEN)


def test_validate_url_rejects_control_chars() -> None:
    for url in (
        "https://exa\r\nmple.test/",
        "https://example.test/\r\nX: y",
        "https://evil.test/\x00",
        "https://user\x0b@example.test/",
    ):
        with pytest.raises(FetchError):
            validate_url(url)


def test_report_table_cells_survive_hostile_claims() -> None:
    profile = {
        "domain": "evil.test",
        "url": "https://evil.test/",
        "run_id": "run-x",
        "score": {"value": 5, "confidence": "low", "observed_evidence": 1,
                  "unknowns": 0, "decision": "review",
                  "note": "n", "reasons": [], "dimensions": {}},
        "signals": {},
        "claims": [{
            "kind": "detected_service",
            "value": "data-cleaning\r\nBcc: victim@evil.test",
            "status": "OBSERVED",
            "evidence_id": 1,
            "reasoning": "row\r\nsplit attempt",
        }],
        "evidence": [{
            "status": "OBSERVED", "kind": "page",
            "url": "https://evil.test/\r\ninjected", "sha256": "ab" * 32,
            "observed_at": "2026-09-15T00:00:00+00:00",
        }],
        "llm": {},
    }
    report = render_report(profile)
    # every untrusted cell stays on ONE line: table row count is exact
    claim_rows = [ln for ln in report.splitlines() if ln.startswith("| detected_service")]
    assert len(claim_rows) == 1
    ev_rows = [ln for ln in report.splitlines() if "| OBSERVED | page |" in ln]
    assert len(ev_rows) == 1
    assert "Bcc: victim@evil.test" in claim_rows[0]  # inline residue, single cell
    assert "\r" not in report
