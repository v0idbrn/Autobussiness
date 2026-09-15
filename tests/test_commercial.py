"""Tests for commercial intelligence: discovery, contacts, briefs, outreach, quotes."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from bam.store import LeadState, Store


@pytest.fixture()
def store(tmp_path):
    db = tmp_path / "test.db"
    s = Store(db_path=db)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Discovery tests
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_discover_from_urls(self):
        from bam.discovery import discover_from_urls

        result = discover_from_urls([
            "https://example.com",
            "https://httpbin.org",
            "",
            "# comment",
        ])
        assert len(result) == 2
        assert result[0].domain == "example.com"
        assert result[1].domain == "httpbin.org"

    def test_discover_from_csv(self, tmp_path):
        from bam.discovery import discover_from_csv

        csv_file = tmp_path / "companies.csv"
        csv_file.write_text(
            "name,url,industry\n"
            "ACME Corp,https://acme.com,manufacturing\n"
            "Beta Inc,https://beta.org,tech\n",
            encoding="utf-8",
        )
        result = discover_from_csv(csv_file)
        assert len(result) == 2
        assert result[0].name == "ACME Corp"
        assert result[0].domain == "acme.com"
        assert result[1].industry == "tech"

    def test_discover_from_text(self):
        from bam.discovery import discover_from_text

        result = discover_from_text(
            "Check out https://example.com and test.org"
        )
        assert len(result) == 2
        domains = {r.domain for r in result}
        assert "example.com" in domains
        assert "test.org" in domains

    def test_deduplicate(self):
        from bam.discovery import DiscoveredCompany, deduplicate

        companies = [
            DiscoveredCompany("A", "a.com", "https://a.com", "manual", None, None, "test"),
            DiscoveredCompany("A2", "a.com", "https://a.com/2", "manual", None, None, "test2"),
            DiscoveredCompany("B", "b.com", "https://b.com", "manual", None, None, "test"),
        ]
        result = deduplicate(companies)
        assert len(result) == 2

    def test_filter_denied(self, tmp_path):
        from bam.discovery import DiscoveredCompany, filter_denied

        companies = [
            DiscoveredCompany("Allowed", "allowed.com", "https://allowed.com",
                            "manual", None, None, "test"),
        ]
        allowed, denied = filter_denied(companies)
        assert len(allowed) == 1
        assert len(denied) == 0


# ---------------------------------------------------------------------------
# Contact discovery tests
# ---------------------------------------------------------------------------


class TestContacts:
    def test_extract_contacts_from_html(self):
        from bam.contacts import extract_contacts_from_html

        html = """
        <html>
        <body>
        <a href="mailto:info@example.com">Email us</a>
        <p>Call us at +1-555-123-4567</p>
        </body>
        </html>
        """
        contacts = extract_contacts_from_html(html, "https://example.com")
        emails = [c.email for c in contacts if c.email]
        phones = [c.phone for c in contacts if c.phone]
        assert len(emails) >= 1
        assert "info@example.com" in emails[0]

    def test_add_contact_to_lead(self, store):
        cid = store.upsert_company("Test Co", "test.com")
        lid = store.upsert_lead(cid, "https://test.com", "run1")

        contact_id = store.add_contact(
            lid,
            name="John Doe",
            role="CEO",
            email="john@test.com",
            source="https://test.com/team",
            evidence="team page",
            confidence="observed",
        )
        assert contact_id > 0

        contacts = store.list_contacts(lid)
        assert len(contacts) == 1
        assert contacts[0].email == "john@test.com"
        assert contacts[0].confidence == "observed"


# ---------------------------------------------------------------------------
# Sales brief tests
# ---------------------------------------------------------------------------


class TestSalesBrief:
    def test_deterministic_brief(self):
        from bam.commercial import generate_sales_brief

        brief = generate_sales_brief(
            lead_data={
                "company_name": "ACME Corp",
                "domain": "acme.com",
                "source_url": "https://acme.com",
                "score": 75,
                "confidence": "high",
                "recommended_service": "pdf-to-excel",
                "reason": "service match",
            },
            evidence=[
                {"status": "OBSERVED", "kind": "page", "excerpt": "We process PDFs"},
            ],
            claims=[
                {"status": "OBSERVED", "kind": "detected_service", "value": "pdf-extraction"},
            ],
            signals={"title": "ACME Corp - PDF Solutions"},
        )
        assert brief.who == "ACME Corp (acme.com)"
        assert brief.recommended_service == "pdf-to-excel"
        assert brief.llm_used is False

    def test_brief_no_evidence(self):
        from bam.commercial import generate_sales_brief

        brief = generate_sales_brief(
            lead_data={"company_name": "Unknown", "domain": "unknown.com"},
            evidence=[],
            claims=[],
            signals={},
        )
        assert "do not claim" in brief.what_not_to_claim.lower()


# ---------------------------------------------------------------------------
# Outreach copilot tests
# ---------------------------------------------------------------------------


class TestOutreach:
    def test_deterministic_outreach(self):
        from bam.commercial import generate_outreach, SalesBrief

        brief = SalesBrief(
            who="ACME",
            what_they_do="PDFs",
            why_us="They need PDF extraction",
            evidence_summary="OBSERVED: PDF processing",
            recommended_service="pdf-to-excel",
            what_not_to_claim="Don't invent facts",
            contact_path="email: info@acme.com",
            first_message_goal="Introduce services",
            suggested_cta="Schedule a call",
            unknowns=["budget"],
        )

        draft = generate_outreach(
            company_name="ACME Corp",
            domain="acme.com",
            evidence=[
                {"status": "OBSERVED", "kind": "page", "excerpt": "PDF processing"},
            ],
            brief=brief,
            contact_path="email: info@acme.com",
        )
        assert draft.channel == "email"
        assert "ACME Corp" in draft.body
        assert draft.llm_used is False

    def test_outreach_form_channel(self):
        from bam.commercial import generate_outreach

        brief = type("Brief", (), {
            "recommended_service": "pdf-to-excel",
        })()

        draft = generate_outreach(
            company_name="ACME",
            domain="acme.com",
            evidence=[],
            brief=brief,
            contact_path="contact page: https://acme.com/contact",
        )
        assert draft.channel == "contact_form"


# ---------------------------------------------------------------------------
# Response classifier tests
# ---------------------------------------------------------------------------


class TestResponseClassifier:
    def test_classify_positive(self):
        from bam.commercial import classify_response

        result = classify_response("Yes, I'm interested in learning more!")
        assert result.classification == "positive"

    def test_classify_pricing(self):
        from bam.commercial import classify_response

        result = classify_response("How much does this cost?")
        assert result.classification == "pricing"

    def test_classify_not_interested(self):
        from bam.commercial import classify_response

        result = classify_response("No thank you, we're not interested.")
        assert result.classification == "not_interested"

    def test_classify_question(self):
        from bam.commercial import classify_response

        result = classify_response("What exactly do you offer?")
        assert result.classification == "question"

    def test_classify_short(self):
        from bam.commercial import classify_response

        result = classify_response("OK")
        assert result.classification == "unclear"


# ---------------------------------------------------------------------------
# Quote tests
# ---------------------------------------------------------------------------


class TestQuote:
    def test_deterministic_quote(self):
        from bam.commercial import generate_quote

        quote = generate_quote(
            service_id="pdf-to-excel",
            company_name="ACME",
            scope="Extract tables from 5 PDF reports",
            estimated_files=5,
            complexity="moderate",
        )
        assert quote.suggested_price > 0
        assert quote.currency == "EUR"
        assert quote.llm_used is False

    def test_quote_simple_vs_complex(self):
        from bam.commercial import generate_quote

        simple = generate_quote("pdf-to-excel", "A", estimated_files=1, complexity="simple")
        complex = generate_quote("pdf-to-excel", "B", estimated_files=1, complexity="complex")
        assert simple.suggested_price < complex.suggested_price

    def test_quote_volume_discount(self):
        from bam.commercial import generate_quote

        few = generate_quote("pdf-to-excel", "A", estimated_files=2)
        many = generate_quote("pdf-to-excel", "B", estimated_files=20)
        # Volume discount should make per-file cost lower
        assert many.suggested_price / 20 < few.suggested_price / 2


# ---------------------------------------------------------------------------
# Store: contacts, interactions, quotes, follow_ups
# ---------------------------------------------------------------------------


class TestStoreCommercial:
    def test_add_interaction(self, store):
        cid = store.upsert_company("Test", "t.com")
        lid = store.upsert_lead(cid, "https://t.com", "r1")
        iid = store.add_interaction(
            lid, channel="email", direction="outbound",
            kind="cold_email", subject="Hello", body="Hi there",
        )
        assert iid > 0
        interactions = store.list_interactions(lid)
        assert len(interactions) == 1
        assert interactions[0].body == "Hi there"

    def test_create_quote(self, store):
        cid = store.upsert_company("Test", "t.com")
        lid = store.upsert_lead(cid, "https://t.com", "r1")
        qid = store.create_quote(
            lid, service_id="pdf-to-excel",
            suggested_price=150.0, scope="5 files",
        )
        assert qid > 0
        quote = store.get_quote(qid)
        assert quote.suggested_price == 150.0
        assert quote.state == "draft"

    def test_update_quote(self, store):
        cid = store.upsert_company("Test", "t.com")
        lid = store.upsert_lead(cid, "https://t.com", "r1")
        qid = store.create_quote(lid, service_id="pdf-to-excel")
        store.update_quote(qid, state="approved", approved_by="operator")
        quote = store.get_quote(qid)
        assert quote.state == "approved"
        assert quote.approved_by == "operator"

    def test_create_follow_up(self, store):
        cid = store.upsert_company("Test", "t.com")
        lid = store.upsert_lead(cid, "https://t.com", "r1")
        fid = store.create_follow_up(
            lid, kind="follow_up", due_date="2026-09-20",
            reason="Follow up on proposal", recommended_action="Send email",
        )
        assert fid > 0
        pending = store.pending_follow_ups()
        assert len(pending) >= 1

    def test_complete_follow_up(self, store):
        cid = store.upsert_company("Test", "t.com")
        lid = store.upsert_lead(cid, "https://t.com", "r1")
        fid = store.create_follow_up(lid, kind="follow_up")
        store.complete_follow_up(fid)
        pending = store.pending_follow_ups()
        assert not any(f.id == fid for f in pending)

    def test_next_lead(self, store):
        cid = store.upsert_company("Test", "t.com")
        lid = store.upsert_lead(cid, "https://t.com", "r1")
        # Transition to qualified (needs outreach)
        store.transition_lead(lid, "researched", run_id="test")
        store.transition_lead(lid, "qualified", run_id="test")

        next_lead = store.next_lead()
        assert next_lead is not None
        assert next_lead["id"] == lid

    def test_digest_commercial_pipeline(self, store):
        digest = store.digest()
        assert "commercial_pipeline" in digest
        assert "opportunities" in digest["commercial_pipeline"]
        assert "quotes_sent" in digest["commercial_pipeline"]


# ---------------------------------------------------------------------------
# Integration: full flow
# ---------------------------------------------------------------------------


class TestCommercialFlow:
    def test_full_flow(self, store):
        """Test: discover → qualify → brief → outreach → approve → contact → response → quote"""
        # Discover
        cid = store.upsert_company("Flow Test Co", "flowtest.com")
        lid = store.upsert_lead(cid, "https://flowtest.com", "run_flow")

        # Qualify
        store.transition_lead(lid, "researched", run_id="run_flow")
        store.transition_lead(lid, "qualified", run_id="run_flow",
                             recommended_service="pdf-to-excel")
        # Auto-transition to approval_required
        store.transition_lead(lid, "approval_required", run_id="run_flow")

        # Approve (HUMAN transition)
        store.record_approval(
            subject_type="lead", subject_id=lid,
            kind="commercial", to_state="approved",
            decided_by="operator", reason="test",
        )

        # Contact (HUMAN transition)
        store.record_approval(
            subject_type="lead", subject_id=lid,
            kind="external_action", to_state="contacted",
            decided_by="operator",
        )

        # Record interaction
        store.add_interaction(
            lid, channel="email", direction="outbound",
            kind="cold_email", body="Hello, we help with PDF extraction",
        )

        # Response
        store.transition_lead(lid, "replied", reason="positive response")

        # Quote
        qid = store.create_quote(
            lid, service_id="pdf-to-excel",
            suggested_price=200.0, scope="10 files",
        )
        store.update_quote(qid, state="approved", approved_by="operator")

        # Win
        store.transition_lead(lid, "negotiating")
        store.record_approval(
            subject_type="lead", subject_id=lid,
            kind="commercial", to_state="won",
            decided_by="operator", reason="deal closed",
        )

        # Verify final state
        lead = store.get_lead(lid)
        assert lead.state == "won"

        # Verify interactions
        interactions = store.list_interactions(lid)
        assert len(interactions) >= 1

        # Verify quotes
        quotes = store.list_quotes(lid)
        assert len(quotes) == 1
        assert quotes[0].state == "approved"
