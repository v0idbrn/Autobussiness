"""Evidence capture + redaction.

Every piece of evidence carries: URL, SHA-256 of the raw bytes, UTC timestamp,
and a redacted excerpt (plan v3.1 §3/§6). Redaction patterns are deliberately
small and readable - no cleverness.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone

# Redaction: emails/phones/tokens never stored raw in excerpts.
_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RE_PHONE = re.compile(r"(?<![\w])\+?\d[\d\s().-]{7,}\d(?![\w])")
_RE_TOKEN = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|bearer|authorization)\b\s*[:=]\s*\S+"
)
_RE_BEARER = re.compile(r"(?i)\b(?:bearer|sk|pk)[-_][A-Za-z0-9._-]{8,}")


# Line-break control chars that can fragment markdown tables / header values
# when untrusted text is embedded verbatim (incl. NEL, U+2028, U+2029, NUL).
_RE_LINEBREAK = re.compile(r"[\r\n\x0b\x0c\x85\u2028\u2029\x00]+")


def _collapse_lines(text: str) -> str:
    return _RE_LINEBREAK.sub(" ", text)


def redact(text: str, *, keep_emails: bool = False) -> str:
    """Redact PII/secrets from an excerpt before it is stored or shared.

    Also collapses every line-break variant to a single space: untrusted
    excerpts land in single-line contexts (markdown tables, header values)
    where embedded newlines corrupt structure.
    """
    out = _collapse_lines(text)
    out = _RE_TOKEN.sub(r"\1=[REDACTED]", out)
    out = _RE_BEARER.sub("[REDACTED_TOKEN]", out)
    if not keep_emails:
        out = _RE_EMAIL.sub("[REDACTED_EMAIL]", out)
    out = _RE_PHONE.sub("[REDACTED_PHONE]", out)
    return out


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Evidence:
    kind: str            # page | pdf | repo | file
    url: str
    sha256: str
    observed_at: str
    excerpt: str         # already redacted
    status: str          # OBSERVED | INFERRED | UNKNOWN

    def to_row(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "url": self.url,
            "sha256": self.sha256,
            "observed_at": self.observed_at,
            "excerpt": self.excerpt,
            "status": self.status,
        }


def page_evidence(url: str, raw_bytes: bytes, *, excerpt_chars: int = 600) -> Evidence:
    """Build OBSERVED evidence from a fetched page (bytes hashed pre-redaction)."""
    try:
        text = raw_bytes.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - decode with replace never raises
        text = ""
    excerpt = redact(text[:excerpt_chars])
    return Evidence(
        kind="page",
        url=url,
        sha256=sha256_bytes(raw_bytes),
        observed_at=utc_now_iso(),
        excerpt=excerpt,
        status="OBSERVED",
    )
