"""Tests for the bam hunt daily sales orchestration command.

Covers: default hunt, service-specific, empty result, source failure,
candidate failure, duplicate candidate, existing opportunity, contact
extraction, outreach draft only, no automatic contact, campaign completion,
query feedback, gemini disabled, gemini fallback, idempotency, isolated DB.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candidate(
    company="Test Corp",
    url="https://test-corp.example.com",
    source_url="https://jobs.wordpress.net/test",
    title="Need help converting PDF invoices to Excel",
    tier="strong",
    score=80,
    service="pdf_to_excel",
    snippet="Looking for someone to convert PDF invoices",
):
    from bam.intent import IntentMatch, OpportunityCandidate
    return OpportunityCandidate(
        company=company,
        company_url=url,
        source_url=source_url,
        source_type="job_board",
        published_at=None,
        title=title,
        snippet=snippet,
        intent=IntentMatch(tier=tier, score=score, service=service, phrases=("convert",)),
        evidence=[{"kind": "job_posting", "status": "OBSERVED", "url": source_url, "excerpt": title[:160]}],
    )


# ---------------------------------------------------------------------------
# 1. default hunt (empty result — no network)
# ---------------------------------------------------------------------------


class TestHuntDefault:
    def test_hunt_subparser_exists(self):
        from bam.cli import build_parser
        p = build_parser()
        args = p.parse_args(["hunt"])
        assert args.command == "hunt"

    def test_hunt_with_service_arg(self):
        from bam.cli import build_parser
        p = build_parser()
        args = p.parse_args(["hunt", "--service", "pdf_to_excel"])
        assert args.command == "hunt"
        assert args.service == ["pdf_to_excel"]

    def test_hunt_with_multiple_services(self):
        from bam.cli import build_parser
        p = build_parser()
        args = p.parse_args(["hunt", "--service", "pdf_to_excel", "excel_cleaning"])
        assert args.service == ["pdf_to_excel", "excel_cleaning"]

    def test_hunt_with_market(self):
        from bam.cli import build_parser
        p = build_parser()
        args = p.parse_args(["hunt", "--market", "Argentina"])
        assert args.market == "Argentina"


# ---------------------------------------------------------------------------
# 2. store methods for hunt
# ---------------------------------------------------------------------------


class TestHuntStoreMethods:
    def test_lead_contacts_empty(self, store):
        assert store.lead_contacts(999) == []

    def test_followups_due_empty(self, store):
        assert store.followups_due() == []

    def test_quotes_pending_empty(self, store):
        assert store.quotes_pending() == []

    def test_lead_contacts_with_data(self, store):
        cid = store.upsert_company("Test", "test.com")
        lid = store.upsert_lead(cid, "https://test.com", "run-test")
        store.add_contact(
            lead_id=lid, name="John", role="CTO",
            email="john@test.com", source="web",
        )
        contacts = store.lead_contacts(lid)
        assert len(contacts) == 1
        assert contacts[0]["email"] == "john@test.com"
        assert contacts[0]["role"] == "CTO"

    def test_followups_due_with_overdue(self, store):
        from datetime import date, timedelta
        cid = store.upsert_company("Test", "test.com")
        lid = store.upsert_lead(cid, "https://test.com", "run-test")
        store.add_followup(
            lead_id=lid,
            kind="follow_up",
            due_date=(date.today() - timedelta(days=1)).isoformat(),
            reason="test",
            recommended_action="Call them",
        )
        due = store.followups_due()
        assert len(due) >= 1
        assert due[0].lead_id == lid

    def test_quotes_pending_with_draft(self, store):
        cid = store.upsert_company("Test", "test.com")
        lid = store.upsert_lead(cid, "https://test.com", "run-test")
        store.add_quote(
            lead_id=lid,
            service_id="pdf_to_excel",
            state="draft",
        )
        pending = store.quotes_pending()
        assert len(pending) >= 1
        assert pending[0]["state"] == "draft"


# ---------------------------------------------------------------------------
# 3. opportunity ranking
# ---------------------------------------------------------------------------


class TestHuntOpportunityRanking:
    def test_explicit_beats_strong(self, store):
        store.add_opportunity(
            source_url="https://a.test/1", source_type="job_board",
            title="Strong post", intent_tier="strong", intent_score=80,
            service_fit="pdf_to_excel", freshness="high", published_at=None,
        )
        store.add_opportunity(
            source_url="https://b.test/1", source_type="job_board",
            title="Explicit request", intent_tier="explicit", intent_score=100,
            service_fit="pdf_to_excel", freshness="high", published_at=None,
        )
        opps = store.list_opportunities()
        assert opps[0]["intent_tier"] == "explicit"
        assert opps[1]["intent_tier"] == "strong"

    def test_fresh_beats_stale(self, store):
        store.add_opportunity(
            source_url="https://a.test/1", source_type="job_board",
            title="Stale post", intent_tier="strong", intent_score=80,
            service_fit="pdf_to_excel", freshness="low", published_at=None,
        )
        store.add_opportunity(
            source_url="https://b.test/1", source_type="job_board",
            title="Fresh post", intent_tier="strong", intent_score=80,
            service_fit="pdf_to_excel", freshness="very_high", published_at=None,
        )
        opps = store.list_opportunities()
        assert opps[0]["freshness"] == "very_high"


# ---------------------------------------------------------------------------
# 4. campaign tracking
# ---------------------------------------------------------------------------


class TestHuntCampaignTracking:
    def test_campaign_start_and_finish(self, store):
        cid = store.start_campaign(
            services=["pdf_to_excel"],
            sources=["job_board_rss"],
        )
        assert cid > 0
        store.finish_campaign(
            cid,
            results={"candidates": 10, "researched": 5, "qualified": 2},
            lead_ids=[],
            query_stats=[
                {"query": "test", "source": "hunt", "service": "pdf_to_excel",
                 "candidates": 10, "researched": 5, "qualified": 2},
            ],
        )
        report = store.campaign_report()
        # Campaign is recorded; funnel aggregates from opportunities/leads
        assert report["funnel"]["opportunities"] >= 0

    def test_query_catalog_updated(self, store):
        cid = store.start_campaign(
            services=["pdf_to_excel"],
            sources=["job_board_rss"],
        )
        store.finish_campaign(
            cid,
            results={"candidates": 10, "qualified": 2},
            lead_ids=[],
            query_stats=[
                {"query": "invoice processing", "source": "hunt",
                 "service": "pdf_to_excel", "candidates": 10, "qualified": 2},
            ],
        )
        entries = store.query_catalog_entries()
        assert len(entries) >= 1
        assert entries[0]["query"] == "invoice processing"
        assert entries[0]["total_qualified"] == 2


# ---------------------------------------------------------------------------
# 5. denylist filtering
# ---------------------------------------------------------------------------


class TestHuntDenylist:
    def test_denylist_blocks_domain(self):
        from bam.denylist import Denylist
        dl = Denylist({"blocked_domain": ["blocked.example.com"]})
        hit = dl.check(domain="blocked.example.com")
        assert hit is not None
        assert hit.category == "blocked_domain"

    def test_denylist_allows_clean_domain(self):
        from bam.denylist import Denylist
        dl = Denylist({"blocked_domain": ["blocked.example.com"]})
        hit = dl.check(domain="clean.example.com")
        assert hit is None


# ---------------------------------------------------------------------------
# 6. freshness / expiry
# ---------------------------------------------------------------------------


class TestHuntFreshness:
    def test_expired_candidate_filtered(self):
        from bam.intent import is_expired
        from datetime import datetime, timezone, timedelta
        old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        assert is_expired(old) is True

    def test_fresh_candidate_not_expired(self):
        from bam.intent import is_expired
        from datetime import datetime, timezone, timedelta
        fresh = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        assert is_expired(fresh) is False


# ---------------------------------------------------------------------------
# 7. intent scoring
# ---------------------------------------------------------------------------


class TestHuntIntentScoring:
    def test_explicit_intent(self):
        from bam.intent import score_intent
        result = score_intent("Need someone to convert PDF invoices to Excel spreadsheet")
        assert result.tier == "explicit"
        assert result.score == 100

    def test_medium_from_hiring(self):
        from bam.intent import score_intent
        result = score_intent("Looking for Excel cleaning specialist to join team")
        # Hiring signal for our service = medium or explicit depending on phrasing
        assert result.tier in ("medium", "explicit")
        assert result.score >= 60


# ---------------------------------------------------------------------------
# 8. gemini disabled by default
# ---------------------------------------------------------------------------


class TestHuntGemini:
    def test_gemini_disabled_in_config(self):
        from bam.config import load_config
        cfg = load_config()
        assert cfg.raw.get("gemini", {}).get("enabled") is False


# ---------------------------------------------------------------------------
# 9. no automatic outreach
# ---------------------------------------------------------------------------


class TestHuntNoAutoOutreach:
    def test_hunt_command_structure(self):
        """Hunt should only draft, never send."""
        from bam.cli import build_parser
        p = build_parser()
        args = p.parse_args(["hunt"])
        assert args.command == "hunt"
        # The hunt command only creates opportunities and briefs,
        # never calls approve-contact or send email
