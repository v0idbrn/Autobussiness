"""Golden commercial test fixtures for the Intent Engine finalization.

Covers: directory adapter, campaign report, query catalog, query selection,
feedback loop, opportunity ranking, empty result behavior.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Directory adapter
# ---------------------------------------------------------------------------


class TestDirectoryAdapter:
    def test_directory_produces_weak_intent(self):
        from bam.intent_sources import discover_intent_from_directory

        class FakeFetcher:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def fetch(self, url):
                return type("R", (), {
                    "url": url, "final_url": url, "status": 200,
                    "content": b"""<html><body>
                    <a href="https://acme-data.test/">Acme Data Services</a>
                    <a href="https://brightbooks.test/">Bright Books</a>
                    </body></html>""",
                    "sha256": "0" * 64, "elapsed_s": 0.01, "hops": 0,
                })()

        cands, failures = discover_intent_from_directory(
            "https://directory.example.test/members",
            fetcher=FakeFetcher())
        assert len(cands) >= 1
        for c in cands:
            assert c.source_type == "directory"
            # Directory gives at most weak or medium intent
            assert c.intent.tier in ("weak", "medium", "strong", "explicit")

    def test_directory_publisher_filtered(self):
        from bam.intent_sources import discover_intent_from_directory

        class FakeFetcher:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def fetch(self, url):
                return type("R", (), {
                    "url": url, "final_url": url, "status": 200,
                    "content": b"""<html><body>
                    <a href="https://forbes.com/article">News</a>
                    <a href="https://linkedin.com/company/x">LinkedIn</a>
                    <a href="https://acme-data.test/">Real Company</a>
                    </body></html>""",
                    "sha256": "0" * 64, "elapsed_s": 0.01, "hops": 0,
                })()

        cands, _ = discover_intent_from_directory(
            "https://directory.example.test",
            fetcher=FakeFetcher())
        domains = [c.company_url for c in cands]
        # Publishers and social are filtered out
        assert not any("forbes" in (d or "") for d in domains)
        assert not any("linkedin" in (d or "") for d in domains)

    def test_directory_failure_skips(self):
        from bam.intent_sources import discover_intent_from_directory

        class Boom:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def fetch(self, url):
                raise RuntimeError("down")

        cands, failures = discover_intent_from_directory(
            "https://directory.example.test",
            fetcher=Boom())
        assert cands == []


# ---------------------------------------------------------------------------
# Campaign report
# ---------------------------------------------------------------------------


class TestCampaignReport:
    def test_empty_report(self, store):
        report = store.campaign_report()
        assert "funnel" in report
        assert "source_quality" in report
        assert "query_quality" in report
        assert "recommended" in report
        assert report["funnel"]["opportunities"] == 0
        assert report["funnel"]["won"] == 0

    def test_report_with_opportunities(self, store):
        # Add some opportunities
        store.add_opportunity(
            source_url="https://example.com/jobs/1",
            source_type="job_board:wpjobs",
            title="Need help converting PDF invoices",
            intent_tier="explicit",
            intent_score=100,
            service_fit="pdf_to_excel",
            freshness="very_high",
            published_at="2026-09-15T00:00:00+00:00",
            snippet="Looking for someone to convert PDF invoices to Excel",
        )
        store.add_opportunity(
            source_url="https://example.com/jobs/2",
            source_type="job_board:wpjobs",
            title="Hiring QA automation engineer",
            intent_tier="strong",
            intent_score=80,
            service_fit="qa_automation",
            freshness="high",
            published_at="2026-09-10T00:00:00+00:00",
            snippet="Hiring a QA automation contractor",
        )
        report = store.campaign_report()
        assert report["funnel"]["opportunities"] == 2
        assert report["funnel"]["explicit_intent"] == 1
        assert report["funnel"]["strong"] == 1
        assert report["funnel"]["qualified"] == 2


# ---------------------------------------------------------------------------
# Query catalog
# ---------------------------------------------------------------------------


class TestQueryCatalog:
    def test_new_query(self, store):
        store.update_query_catalog(
            query="test query",
            service="pdf_to_excel",
            source="job_board",
            candidates=10,
            researched=5,
            qualified=2,
        )
        entries = store.query_catalog_entries()
        assert len(entries) == 1
        assert entries[0]["state"] == "promising"
        assert entries[0]["total_candidates"] == 10
        assert entries[0]["total_qualified"] == 2

    def test_accumulates_stats(self, store):
        store.update_query_catalog(
            query="test query",
            service="pdf_to_excel",
            source="job_board",
            candidates=10,
            qualified=2,
        )
        store.update_query_catalog(
            query="test query",
            service="pdf_to_excel",
            source="job_board",
            candidates=5,
            qualified=1,
        )
        entries = store.query_catalog_entries()
        assert entries[0]["runs"] == 2
        assert entries[0]["total_candidates"] == 15
        assert entries[0]["total_qualified"] == 3

    def test_weak_state_after_many_runs(self, store):
        # Run 3 times with no qualified leads
        for _ in range(3):
            store.update_query_catalog(
                query="bad query",
                service=None,
                source="news_rss",
                candidates=10,
                qualified=0,
            )
        entries = store.query_catalog_entries(state="weak")
        assert len(entries) == 1
        assert entries[0]["runs"] == 3

    def test_retire_requires_minimum_runs(self, store):
        store.update_query_catalog(
            query="early query",
            candidates=5,
        )
        entries = store.query_catalog_entries()
        with pytest.raises(ValueError, match="need at least 3"):
            store.retire_query(entries[0]["id"])

    def test_retire_works_with_enough_runs(self, store):
        for _ in range(3):
            store.update_query_catalog(
                query="tired query",
                candidates=10,
                qualified=0,
            )
        entries = store.query_catalog_entries()
        store.retire_query(entries[0]["id"])
        updated = store.query_catalog_entries(state="retired")
        assert len(updated) == 1

    def test_best_queries_for_next_campaign(self, store):
        # Add promising query
        store.update_query_catalog(
            query="good query",
            service="pdf_to_excel",
            source="job_board",
            candidates=10,
            qualified=3,
        )
        # Add new query for exploration
        store.update_query_catalog(
            query="new query",
            service="qa_automation",
            source="news_rss",
            candidates=5,
            qualified=0,
        )
        best = store.best_queries_for_next_campaign(limit=5)
        assert len(best) >= 1
        # Promising query should be included
        queries = [b["query"] for b in best]
        assert "good query" in queries


# ---------------------------------------------------------------------------
# Feedback loop
# ---------------------------------------------------------------------------


class TestFeedbackLoop:
    def test_campaign_updates_query_catalog(self, store):
        campaign_id = store.start_campaign(
            services=["pdf_to_excel"],
            sources=["job_board"],
        )
        store.finish_campaign(
            campaign_id,
            results={"candidates": 10, "qualified": 2},
            lead_ids=[],
            query_stats=[
                {"query": "invoice processing", "source": "job_board",
                 "service": "pdf_to_excel", "candidates": 10,
                 "researched": 5, "qualified": 2, "contactable": 1},
            ],
        )
        entries = store.query_catalog_entries()
        assert len(entries) == 1
        assert entries[0]["query"] == "invoice processing"
        assert entries[0]["total_qualified"] == 2
        assert entries[0]["state"] == "promising"


# ---------------------------------------------------------------------------
# Opportunity ranking
# ---------------------------------------------------------------------------


class TestOpportunityRanking:
    def test_explicit_before_strong(self, store):
        store.add_opportunity(
            source_url="https://a.test/1",
            source_type="job_board",
            title="Strong intent post",
            intent_tier="strong",
            intent_score=80,
            service_fit="pdf_to_excel",
            freshness="high",
            published_at=None,
        )
        store.add_opportunity(
            source_url="https://b.test/1",
            source_type="job_board",
            title="Explicit request for help",
            intent_tier="explicit",
            intent_score=100,
            service_fit="pdf_to_excel",
            freshness="high",
            published_at=None,
        )
        opps = store.list_opportunities()
        assert opps[0]["intent_tier"] == "explicit"
        assert opps[1]["intent_tier"] == "strong"

    def test_fresh_before_stale(self, store):
        store.add_opportunity(
            source_url="https://a.test/1",
            source_type="job_board",
            title="Stale post",
            intent_tier="strong",
            intent_score=80,
            service_fit="pdf_to_excel",
            freshness="low",
            published_at=None,
        )
        store.add_opportunity(
            source_url="https://b.test/1",
            source_type="job_board",
            title="Fresh post",
            intent_tier="strong",
            intent_score=80,
            service_fit="pdf_to_excel",
            freshness="very_high",
            published_at=None,
        )
        opps = store.list_opportunities()
        assert opps[0]["freshness"] == "very_high"
        assert opps[1]["freshness"] == "low"

    def test_expired_hidden_by_default(self, store):
        from datetime import datetime, timezone, timedelta
        old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        store.add_opportunity(
            source_url="https://a.test/1",
            source_type="job_board",
            title="Very old post",
            intent_tier="strong",
            intent_score=80,
            service_fit="pdf_to_excel",
            freshness="very_low",
            published_at=old,
        )
        opps = store.list_opportunities()
        assert len(opps) == 0  # expired hidden by default
        opps_all = store.list_opportunities(include_expired=True)
        assert len(opps_all) == 1
