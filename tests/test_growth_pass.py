"""Growth-pass tests: RSS discovery, multi-page research, follow-up
auto-creation, outreach delivery flags, WHOIS + LinkedIn extraction.

All offline: RSS/WHOIS parsing is fed bytes directly; the pipeline fetcher is
stubbed at the network boundary; CLI runs under BAM_ROOT isolation.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from bam.config import Config
from bam.contacts import (
    discover_contacts_from_whois,
    extract_contacts_from_html,
    extract_linkedin_urls,
)
from bam.discovery import parse_rss_feed
from bam.fetcher import FetchError
from bam.pipeline import research
from bam.store import Store

# ---------------------------------------------------------------- RSS (Part 1)

RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:source="https://news.google.com">
<channel>
  <title>acme search</title>
  <item>
    <title>Firmwise LLP expands data services - The Daily Ledger</title>
    <link>https://news.google.com/rss/articles/redirect1</link>
    <source url="https://www.firmwise-llp.test/">The Daily Ledger</source>
  </item>
  <item>
    <title>Google launches new thing - TechCrunch</title>
    <link>https://news.google.com/rss/articles/redirect2</link>
    <source url="https://news.google.com/">TechCrunch</source>
  </item>
  <item>
    <title>BeanCounters automates invoicing - Ledger Weekly</title>
    <link>https://news.google.com/rss/articles/redirect3</link>
    <source url="https://beancounters.test/about">Ledger Weekly</source>
  </item>
</channel>
</rss>"""


def test_parse_rss_extracts_publisher_domains() -> None:
    out = parse_rss_feed(RSS_XML, query="accounting firms",
                         source_url="https://news.google.com/rss", limit=10)
    domains = [c.domain for c in out]
    # publisher <source url> wins over the news.google.com redirect link...
    assert "firmwise-llp.test" in domains
    assert "beancounters.test" in domains
    # ...and aggregator domains are never company candidates
    assert "news.google.com" not in domains
    names = {c.name for c in out}
    assert any("Firmwise" in n for n in names)
    assert all(c.source == "rss" for c in out)
    assert all("accounting firms" in c.evidence for c in out)


def test_parse_rss_empty_feed() -> None:
    out = parse_rss_feed(
        b"<?xml version='1.0'?><rss version='2.0'><channel><title>x</title></channel></rss>",
        query="nothing", source_url=None)
    assert out == []


def test_parse_rss_malformed_bytes() -> None:
    assert parse_rss_feed(b"<not-xml at all", query="q", source_url=None) == []


def test_parse_rss_respects_limit_and_dedupes() -> None:
    xml = RSS_XML.replace(
        b"beancounters.test/about", b"firmwise-llp.test/again")  # duplicate domain
    out = parse_rss_feed(xml, query="q", source_url=None, limit=1)
    assert len(out) == 1


# ------------------------------------------------- multi-page research (Part 2)

HOME_HTML = b"""<html><head><title>Acme Home</title></head><body>
<a href="/about">About us</a><a href="/contact">Contact</a>
<p>Acme helps with spreadsheets</p></body></html>"""

ABOUT_HTML = b"""<html><head><title>About Acme</title>
<meta name="description" content="We clean messy data"></head><body>
<p>Our team cleans invoices and handles pdf extraction daily.
Contact ops@acme-example.test for anything.</p>
<a href="https://linkedin.com/in/jane-doe">Jane on LinkedIn</a>
</body></html>"""

TEAM_HTML = b"""<html><head><title>Team</title></head><body>
<a href="https://es.linkedin.com/in/john-smith-1234">John</a>
</body></html>"""


class DispatchFetcher:
    """Serves fixture pages by path; /contact mirrors the homepage (soft-404)."""

    pages = {
        "/": HOME_HTML,
        "/about": ABOUT_HTML,
        "/team": TEAM_HTML,
        "/contact": HOME_HTML,  # identical bytes -> must be skipped
    }

    def __init__(self, *a, **k) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a) -> None:
        pass

    def fetch(self, url: str):
        path = urlparse(url).path or "/"
        body = self.pages.get(path)
        if body is None:
            raise FetchError(f"no fixture for {url}")
        import hashlib
        return type("R", (), {
            "url": url, "final_url": url, "status": 200, "content": body,
            "sha256": hashlib.sha256(body).hexdigest(), "elapsed_s": 0.01, "hops": 0,
        })()


@pytest.fixture()
def multipage(monkeypatch: pytest.MonkeyPatch):
    import bam.pipeline as pl
    monkeypatch.setattr(pl, "Fetcher", DispatchFetcher)
    monkeypatch.setattr(pl, "_robots_allows", lambda *a, **k: True)


def test_multipage_research_merges_signals(store: Store, config: Config,
                                           multipage) -> None:
    res = research("https://acme-example.test/", store=store, config=config)
    # homepage + /about + /team fetched; /contact skipped (soft-404 mirror)
    assert res.profile_path and res.profile_path.exists()
    profile = json.loads(res.profile_path.read_text(encoding="utf-8"))
    signals = profile["signals"]
    fetched = signals["subpages_fetched"]
    assert any(u.endswith("/about") for u in fetched)
    assert any(u.endswith("/team") for u in fetched)
    assert not any(u.endswith("/contact") for u in fetched)
    # merged: email only present on /about, LinkedIn only on /team
    assert any("ops@acme-example.test" in e for e in signals["emails"])
    assert any("linkedin.com/in/jane-doe" in t for t in signals["team_links"])


def test_multipage_claims_carry_per_page_evidence(store: Store, config: Config,
                                                  multipage) -> None:
    res = research("https://acme-example.test/", store=store, config=config)
    evidence = store.evidence_for_lead(res.lead_id)
    ev_by_url = {e["url"]: e["id"] for e in evidence}
    # homepage + about + team = 3 evidence rows (contact was a soft-404 mirror)
    assert len(evidence) == 3
    assert any("/about" in u for u in ev_by_url)
    claims = store.claims_for_lead(res.lead_id)
    # the pdf-extraction claim comes from the /about page -> attributed there
    pdf_claims = [c for c in claims if c["value"] == "pdf-extraction"]
    assert pdf_claims, "about-page keyword must produce a claim"
    assert all(c["status"] == "OBSERVED" for c in pdf_claims)
    assert any(c["evidence_id"] == ev_by_url.get(
        "https://acme-example.test/about") for c in pdf_claims)


def test_multipage_zero_subpages_config(store: Store, config: Config,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    import bam.pipeline as pl
    monkeypatch.setattr(pl, "Fetcher", DispatchFetcher)
    monkeypatch.setattr(pl, "_robots_allows", lambda *a, **k: True)
    cfg = config
    object.__setattr__(cfg, "raw", {"fetch": {"max_subpages": 0}})
    res = research("https://acme-example.test/", store=store, config=cfg)
    profile = json.loads(res.profile_path.read_text(encoding="utf-8"))
    assert profile["signals"]["subpages_fetched"] == []
    assert len(store.evidence_for_lead(res.lead_id)) == 1


# -------------------------------------------- follow-up auto-creation (Part 3)

def _lead_to_approved(store: Store) -> int:
    cid = store.upsert_company("Acme", "acme.test")
    lid = store.upsert_lead(cid, "https://acme.test", "run-x")
    for st in ("researched", "qualified", "approval_required"):
        store.transition_lead(lid, st)
    store.record_approval(subject_type="lead", subject_id=lid,
                          kind="commercial", to_state="approved",
                          decided_by="operator")
    return lid


def test_follow_up_created_on_human_contact_transition(store: Store) -> None:
    lid = _lead_to_approved(store)
    assert not store.pending_follow_ups()
    store.record_approval(subject_type="lead", subject_id=lid,
                          kind="external_action", to_state="contacted",
                          decided_by="operator")
    pend = store.pending_follow_ups()
    assert [f.kind for f in pend] == ["first_contact"]
    assert pend[0].due_date is not None
    assert "reminder" in (pend[0].recommended_action or "").lower()


def test_follow_up_created_on_reply_transition(store: Store) -> None:
    lid = _lead_to_approved(store)
    store.record_approval(subject_type="lead", subject_id=lid,
                          kind="external_action", to_state="contacted",
                          decided_by="operator")
    store.transition_lead(lid, "replied")
    kinds = [f.kind for f in store.pending_follow_ups()]
    assert "reply_pending" in kinds


def test_follow_up_created_on_delivered_to_follow_up(store: Store) -> None:
    cid = store.upsert_company("Acme", "acme.test")
    lid = store.upsert_lead(cid, "https://acme.test", "run-x")
    for st in ("researched", "qualified", "approval_required"):
        store.transition_lead(lid, st)
    store.record_approval(subject_type="lead", subject_id=lid,
                          kind="commercial", to_state="approved",
                          decided_by="operator")
    store.record_approval(subject_type="lead", subject_id=lid,
                          kind="external_action", to_state="contacted",
                          decided_by="operator")
    for st in ("replied", "negotiating"):
        store.transition_lead(lid, st)
    store.record_approval(subject_type="lead", subject_id=lid,
                          kind="commercial", to_state="won", decided_by="operator")
    for st in ("onboarding", "delivery", "delivered", "follow_up"):
        store.transition_lead(lid, st)
    kinds = [f.kind for f in store.pending_follow_ups()]
    assert "repeat_opportunity" in kinds


def test_follow_up_created_when_quote_approved(store: Store) -> None:
    lid = _lead_to_approved(store)
    qid = store.create_quote(lid, service_id="pdf-to-excel")
    store.update_quote(qid, state="sent")
    store.update_quote(qid, state="approved")
    kinds = [f.kind for f in store.pending_follow_ups()]
    assert "quote_pending" in kinds


def test_no_follow_up_on_unrelated_transition(store: Store) -> None:
    cid = store.upsert_company("Acme", "acme.test")
    lid = store.upsert_lead(cid, "https://acme.test", "run-x")
    store.transition_lead(lid, "researched")
    store.transition_lead(lid, "qualified")
    assert store.pending_follow_ups() == []


# ----------------------------------------------- outreach delivery (Part 4)

def test_draft_outreach_file_writes_eml(tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch,
                                        capsys) -> None:
    monkeypatch.setenv("BAM_ROOT", str(tmp_path))
    from bam.cli import main
    from bam.store import Store

    store = Store()
    try:
        lid = _lead_to_approved(store)
    finally:
        store.close()
    rc = main(["draft-outreach", str(lid), "--file"])
    assert rc == 0
    drafts = list((tmp_path / "data" / "drafts").glob(f"{lid}_*.eml"))
    assert drafts, "an .eml draft file must be written under data/drafts"
    text = drafts[0].read_text(encoding="utf-8")
    assert text.startswith("From: ")
    assert "Subject:" in text
    assert "X-Unsent: 1" in text


# --------------------------------------------------- WHOIS + LinkedIn (5 + 6)

class _StaticFetcher:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def fetch(self, url: str):
        import hashlib
        return type("R", (), {
            "url": url, "final_url": url, "status": 200, "content": self._body,
            "sha256": hashlib.sha256(self._body).hexdigest(),
            "elapsed_s": 0.01, "hops": 0,
        })()


WHOIS_HTML = b"""<html><body>
<div class="whois-heading">Registrant Contact</div>
<pre>Name: ACME HOLDINGS
Email: registrar-owner@acme-registry.test
Phone: +34.600000000</pre>
</body></html>"""


def test_whois_extraction_finds_registrant_email() -> None:
    out = discover_contacts_from_whois("acme-registry.test",
                                       fetcher=_StaticFetcher(WHOIS_HTML))
    assert len(out) == 1
    assert "registrar-owner@acme-registry.test" in (out[0].email or "")
    assert out[0].confidence == "observed"
    assert "whois.com" in out[0].source


def test_whois_extraction_no_registrant_section() -> None:
    out = discover_contacts_from_whois("acme-registry.test",
                                       fetcher=_StaticFetcher(b"<html>No data</html>"))
    assert out == []


def test_linkedin_url_extraction() -> None:
    html = ('<a href="https://linkedin.com/in/jane-doe">Jane</a>'
            '<a href="https://es.linkedin.com/in/john-smith-1234/">John</a>'
            '<a href="https://linkedin.com/in/jane-doe">Jane again</a>')
    urls = extract_linkedin_urls(html)
    assert len(urls) == 2  # deduped
    assert "https://linkedin.com/in/jane-doe" in urls
    assert any("es.linkedin.com" in u for u in urls)


def test_contacts_from_html_includes_linkedin() -> None:
    html = ABOUT_HTML.decode("utf-8")
    contacts = extract_contacts_from_html(html, "https://acme-example.test/about")
    li = [c for c in contacts if "LinkedIn" in c.evidence]
    assert len(li) == 1
    assert "linkedin.com/in/jane-doe" in li[0].evidence
    assert li[0].confidence == "observed"
