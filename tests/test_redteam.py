"""Red-team hardening tests (production-hardening pass).

Every test here pins a DEMONSTRATED failure mode:
- non-finite money corrupting digest revenue (P0)
- state values unknown to the schema being accepted by transitions (P1)
- CRLF header injection into .eml outreach drafts (P1)
- negative CLI numerics reaching quote generation (P1)
- fetcher refusing dangerous schemes/credentials (security, pre-existing)
- RSS parser respecting its limit under hostile input (adversarial)
- lead artifact dirs staying inside data/ for hostile domains (path safety)

All offline.
"""

from __future__ import annotations

import pytest

from bam import cli
from bam.discovery import parse_rss_feed
from bam.fetcher import FetchError, validate_url
from bam.reporting import _safe_dir_name
from bam.store import Store


# --------------------------------------------------------------- money (P0)

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -1, 0])
def test_record_payment_rejects_nonfinite_and_nonpositive(store: Store, bad: float) -> None:
    cid = store.upsert_company("T", "t.test")
    jid = store.create_job(input_path="x.pdf", service_id="pdf-to-excel", title="T")
    with pytest.raises(ValueError):
        store.record_payment(jid, paid_amount=bad)
    job = store.get_job(jid)
    assert job.paid_amount is None and job.payment_status != "paid"


def test_update_job_rejects_nonfinite_quoted(store: Store) -> None:
    cid = store.upsert_company("T", "t.test")
    jid = store.create_job(input_path="x.pdf", service_id="pdf-to-excel", title="T")
    with pytest.raises(ValueError):
        store.update_job(jid, agreed_amount=float("inf"))
    assert store.get_job(jid).agreed_amount is None


# ------------------------------------------------- state machine schema (P1)

def test_transition_rejects_state_missing_from_schema(store: Store) -> None:
    cid = store.upsert_company("T", "t.test")
    lid = store.upsert_lead(cid, "https://t.test", "run-1")

    with pytest.raises(ValueError):
        store.transition_lead(lid, "archived")  # not in LeadState enum


def test_record_approval_rejects_state_missing_from_schema(store: Store) -> None:
    cid = store.upsert_company("T", "t.test")
    lid = store.upsert_lead(cid, "https://t.test", "run-1")
    with pytest.raises(Exception):
        store.record_approval(subject_type="lead", subject_id=lid,
                              kind="commercial", to_state="admin_won",
                              decided_by="operator")


# --------------------------------------------------- .eml header injection (P1)

def test_eml_draft_never_injects_headers(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BAM_ROOT", str(tmp_path))
    from bam.store import Store

    store = Store()
    try:
        cid = store.upsert_company("Acme\r\nBcc: victim@evil.test\r\nX-Evil: 1",
                                   "evil.test")
        lid = store.upsert_lead(cid, "https://evil.test", "run-1")
        for st in ("researched", "qualified", "approval_required"):
            store.transition_lead(lid, st)
        store.record_approval(subject_type="lead", subject_id=lid,
                              kind="commercial", to_state="approved",
                              decided_by="operator")
    finally:
        store.close()
    rc = cli.main(["draft-outreach", str(lid), "--file"])
    assert rc == 0
    eml = next((tmp_path / "data" / "drafts").glob(f"{lid}_*.eml")).read_bytes()
    text = eml.decode("utf-8")
    headers, _sep, body = text.partition("\r\n\r\n")
    # a hostile name must never add a new header line
    assert "\r\nBcc:" not in headers and "\r\nX-Evil" not in headers
    # line endings stay CRLF on every platform (no \r\r\n artifacts)
    assert b"\r\r" not in eml
    assert body  # message body survives


# ------------------------------------------------------- CLI numerics (P1)

def test_quote_rejects_negative_counts(capsys) -> None:
    with pytest.raises(SystemExit):
        cli.main(["quote", "1", "--files", "-5"])
    assert "must be >= 0" in capsys.readouterr().err


def test_pay_rejects_nan_cleanly(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("BAM_ROOT", str(tmp_path))
    from bam.store import Store

    store = Store()
    try:
        cid = store.upsert_company("T", "t.test")
        store.create_job(input_path="x.pdf", service_id="pdf-to-excel", title="T")
    finally:
        store.close()
    rc = cli.main(["pay", "1", "--amount", "nan"])
    assert rc == 1
    assert "finite" in capsys.readouterr().err


# ------------------------------------------------------- fetcher URL security

@pytest.mark.parametrize("bad", [
    "javascript:alert(1)",
    "file:///C:/Windows/win.ini",
    "ftp://example.com/x",
    "https://user:pass@example.com/",
    "https://example.com:99999/",
    "   ",
])
def test_validate_url_refuses_dangerous_urls(bad: str) -> None:
    with pytest.raises(FetchError):
        validate_url(bad)


# ------------------------------------------------------- RSS adversarial

def test_rss_limit_is_enforced_under_flood() -> None:
    items = "".join(
        f"<item><title>Company {i} - Pub</title>"
        f"<source url=\"https://c{i}.test/\">P</source></item>"
        for i in range(500)
    )
    xml = f"<?xml version='1.0'?><rss version='2.0'><channel>{items}</channel></rss>"
    out = parse_rss_feed(xml.encode("utf-8"), query="q", source_url=None, limit=7)
    assert len(out) == 7


def test_rss_xml_bomb_yields_no_companies() -> None:
    # recursive entity expansion bomb: expat refuses it, parser returns []
    bomb = (b"<?xml version='1.0'?><!DOCTYPE r [<!ENTITY a '&b;'><!ENTITY b '&a;'>]>"
            b"<rss version='2.0'><channel><item><title>&a;</title>"
            b"<source url='https://x.test/'>P</source></item></channel></rss>")
    assert parse_rss_feed(bomb, query="q", source_url=None) == []


def test_rss_item_without_any_url_is_skipped() -> None:
    xml = (b"<?xml version='1.0'?><rss version='2.0'><channel>"
           b"<item><title>No link at all</title></item>"
           b"</channel></rss>")
    assert parse_rss_feed(xml, query="q", source_url=None) == []


# ------------------------------------------------------- path safety

@pytest.mark.parametrize("evil", [
    "../../etc/passwd",
    "..\\..\\Windows\\system32",
    "C:\\Temp\\evil",
    "con",
    "nul.txt",
    "a/b/c/../../d",
    "aid\\srv",
])
def test_lead_dir_name_stays_inside_data_root(evil: str, tmp_path) -> None:
    from bam.reporting import lead_artifact_dir

    root = tmp_path / "leads"
    root.mkdir()
    lead_dir = lead_artifact_dir(root, evil)
    resolved_root = root.resolve()
    assert resolved_root in lead_dir.resolve().parents
    assert "/" not in lead_dir.name and "\\" not in lead_dir.name
    assert ":" not in lead_dir.name
