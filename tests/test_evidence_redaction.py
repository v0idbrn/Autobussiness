"""Evidence + redaction tests (no network)."""

from __future__ import annotations

from bam.evidence import page_evidence, redact, sha256_bytes


def test_redact_emails() -> None:
    assert redact("contact me at john.doe@example.com now") == \
        "contact me at [REDACTED_EMAIL] now"


def test_redact_secrets() -> None:
    text = "api_key: sk-live-abc123 and password=hunter2"
    out = redact(text)
    assert "sk-live-abc123" not in out
    assert "hunter2" not in out


def test_redact_keep_emails() -> None:
    out = redact("mail bob@corp.test", keep_emails=True)
    assert "bob@corp.test" in out


def test_redact_phones() -> None:
    out = redact("call +34 600 111 222 today")
    assert "600 111 222" not in out


def test_sha256_bytes_deterministic() -> None:
    assert sha256_bytes(b"bam") == sha256_bytes(b"bam")
    assert sha256_bytes(b"bam") != sha256_bytes(b"bams")


def test_page_evidence_hash_and_redaction() -> None:
    raw = b"<html>email: secret@corp.test</html>"
    ev = page_evidence("https://x.test/", raw)
    assert ev.status == "OBSERVED"
    assert ev.sha256 == sha256_bytes(raw)
    assert "secret@corp.test" not in ev.excerpt
    assert ev.observed_at.endswith("+00:00")
