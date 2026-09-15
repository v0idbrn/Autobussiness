"""Sales Copilot tests: tiers, WHY explanations, offers, objection playbook,
daily queue with anti-spam caps, intent signals, campaign feedback loop.

All offline and deterministic. Golden-lead fixtures are constructed as pure
signal dicts (§33) — the same shapes the pipeline produces.
"""

from __future__ import annotations

import pytest

from bam.copilot import (
    build_daily_queue,
    classify_tier,
    enforce_hypothesis_honesty,
    explain_why,
    playbook_for,
    suggested_contact_roles,
    tier_action,
)
from bam.signals import (
    OFFERS,
    extract_intent_signals,
    recommend_offer,
    score_contactability,
)

# ---------------------------------------------------------------- golden leads


def _golden_pdf_lead(**over):
    """GOOD PDF LEAD: document-processing services company, contactable."""
    base = {
        "id": 1,
        "company_name": "Acme Back Office",
        "domain": "acmebackoffice.test",
        "state": "approval_required",
        "score": 72.0,
        "recommended_service": "pdf_to_excel",
        "evidence": [
            {"kind": "page", "status": "OBSERVED", "excerpt": "invoice processing"},
            {"kind": "page", "status": "OBSERVED", "excerpt": "accounts payable"},
        ],
        "commercial": {
            "candidate_type": "company",
            "recommended_service": "pdf_to_excel",
            "base_score": 8,
            "total_score": 11,
            "pages_matched": {"pdf_to_excel": 2},
        },
        "contactability": {"score": 5, "level": "high"},
        "contact": "info@acmebackoffice.test",
    }
    base.update(over)
    return base


def _golden_publisher(**over):
    base = {
        "id": 2, "company_name": "Wire News", "domain": "news-wire.test",
        "state": "researched", "score": 60.0, "recommended_service": "pdf_to_excel",
        "evidence": [], "contactability": {"score": 0, "level": "low"},
        "contact": None,
        "commercial": {"candidate_type": "publisher", "base_score": 5,
                       "total_score": 5, "recommended_service": "pdf_to_excel"},
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------- tiers


def test_golden_pdf_lead_is_tier_A() -> None:
    c = _golden_pdf_lead()
    tier = classify_tier(score=c["score"], commercial=c["commercial"],
                         contactability=c["contactability"],
                         recommended_service=c["recommended_service"],
                         lead_state=c["state"])
    assert tier == "A"
    assert tier_action("A") == "CONTACT TODAY"


def test_publisher_is_always_D() -> None:
    c = _golden_publisher()
    tier = classify_tier(score=c["score"], commercial=c["commercial"],
                         contactability=c["contactability"],
                         recommended_service=c["recommended_service"],
                         lead_state=c["state"])
    assert tier == "D"


def test_good_fit_without_contact_caps_at_B() -> None:
    c = _golden_pdf_lead(contact=None, contactability={"score": 1, "level": "low"})
    tier = classify_tier(score=c["score"], commercial=c["commercial"],
                         contactability=c["contactability"],
                         recommended_service=c["recommended_service"],
                         lead_state=c["state"])
    assert tier == "B"


def test_weak_evidence_is_C_or_D() -> None:
    tier = classify_tier(score=35.0, commercial={"candidate_type": "company",
                                                 "base_score": 1},
                         contactability={"score": 0}, recommended_service=None,
                         lead_state="researched")
    assert tier in ("C", "D")


def test_disqualified_lead_is_D() -> None:
    c = _golden_pdf_lead(state="disqualified")
    tier = classify_tier(score=c["score"], commercial=c["commercial"],
                         contactability=c["contactability"],
                         recommended_service=c["recommended_service"],
                         lead_state=c["state"])
    assert tier == "D"


# ----------------------------------------------------------------- why / offer


def test_explain_why_grounds_in_evidence() -> None:
    c = _golden_pdf_lead()
    why = explain_why(c, c["evidence"], c["commercial"], c["contactability"])
    assert "pdf_to_excel" in why.bullets[0]
    assert why.offer and why.offer["name"] == OFFERS["pdf_to_excel"]["name"]
    assert why.next_action == "CONTACT TODAY"
    assert not why.hypothesis


def test_explain_why_hypothesis_when_no_evidence() -> None:
    c = _golden_pdf_lead(evidence=[], commercial={"candidate_type": "company",
                                                  "base_score": 3,
                                                  "recommended_service": None})
    why = explain_why(c, [], c["commercial"], c["contactability"])
    assert why.hypothesis
    assert any("no OBSERVED evidence" in u for u in why.unknowns)


def test_explain_why_publisher_rejects_cleanly() -> None:
    c = _golden_publisher()
    why = explain_why(c, [], c["commercial"], c["contactability"])
    assert "never pursue" in why.headline.lower()


def test_recommend_offer_known_and_unknown_service() -> None:
    assert recommend_offer("pdf_to_excel")["name"] == "PDF → Excel Conversion"
    assert recommend_offer("nope") is None
    assert recommend_offer(None) is None


def test_contact_roles_per_service() -> None:
    assert "operations" in suggested_contact_roles("pdf_to_excel")
    assert "cto" in suggested_contact_roles("qa_automation")
    assert "finance" in suggested_contact_roles("excel_cleaning")


# ---------------------------------------------------------------- intent (§6)


def test_intent_requires_context_for_tool_mentions() -> None:
    # 'uses Excel' alone is NOT intent
    weak = extract_intent_signals("we work with excel every day")
    assert weak["score"] == 0
    # 'Excel for monthly reporting' IS gated intent
    strong = extract_intent_signals("manual excel reporting for monthly closes")
    assert strong["score"] > 0 and strong["context_gated_tools"]


def test_intent_strong_phrases() -> None:
    r = extract_intent_signals("we are hiring a data entry team for outsourcing work")
    assert r["strong"] and r["score"] >= 6


# --------------------------------------------------------------- contactability


def test_contactability_never_invents() -> None:
    r = score_contactability({})
    assert r["score"] == 0 and r["level"] == "low" and not r["has_email"]


# ----------------------------------------------------------------- daily queue


def test_queue_orders_by_tier_then_score() -> None:
    a = _golden_pdf_lead()
    b = _golden_pdf_lead(id=3, score=50.0, commercial={
        **a["commercial"], "base_score": 3, "total_score": 3},
        contactability={"score": 1, "level": "low"}, contact=None)
    d = _golden_publisher()
    entries, stats = build_daily_queue([d, b, a], limit=5)
    assert [e.tier for e in entries] == ["A", "B", "D"]
    assert stats["A"] == 1 and stats["D"] == 1


def test_queue_anti_spam_cap() -> None:
    leads = [_golden_pdf_lead(id=i, score=70.0 + i) for i in range(1, 9)]
    entries, _ = build_daily_queue(leads, limit=5, contacted_this_week=4,
                                   max_daily_outreach=5)
    contact_actions = [e for e in entries if e.action == "CONTACT TODAY"]
    assert len(contact_actions) <= 1  # budget 5 - 4 already sent this week
    assert any(e.action.startswith("QUEUED FOR TOMORROW") for e in entries)


def test_queue_tier_a_without_contact_flags_review() -> None:
    c = _golden_pdf_lead(contact=None, contactability={"score": 5, "level": "high"})
    entries, _ = build_daily_queue([c], limit=5)
    assert entries[0].action == "REVIEW CONTACT PATH"


# --------------------------------------------------------------- honesty (§15)


def test_hypothesis_honesty_enforces_conditional() -> None:
    body = "Hi,\n\nI saw your site. We help with document processing."
    out = enforce_hypothesis_honesty(body, hypothesis=True)
    assert "If this is part of your workflow" in out
    # non-hypothesis drafts pass through untouched
    assert enforce_hypothesis_honesty(body, hypothesis=False) == body


# ----------------------------------------------------------- objection playbook


@pytest.mark.parametrize("cls", ["pricing", "not_interested", "needs_more_information",
                                 "question", "unclear"])
def test_playbook_covers_main_classes(cls: str) -> None:
    play = playbook_for(cls)
    assert play and play["say"] and play["next"]
    assert "means" in play


def test_playbook_pricing_needs_quote() -> None:
    assert playbook_for("pricing")["quote_needed"] is True


# ------------------------------------------------------- feedback loop (§27-§31)


def test_campaign_feedback_loop(store) -> None:
    cid = store.start_campaign(services=["pdf_to_excel"], sources=["directory"],
                               queries=["invoice processing services company"],
                               limits={"per_query": 5})
    store.finish_campaign(
        cid, results={"candidates": 6, "publishers_filtered": 2}, lead_ids=[1, 2],
        query_stats=[{"query": "invoice processing services company",
                      "source": "directory", "service": "pdf_to_excel",
                      "candidates": 6, "researched": 4, "qualified": 2,
                      "contactable": 2}])
    sq = store.source_quality()
    assert sq and sq[0]["source"] == "directory" and sq[0]["qualified"] == 2
    qq = store.query_quality()
    assert qq and "invoice processing" in qq[0]["query"]
    # campaign run is audited
    n = store._conn().execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='campaign.run'").fetchone()[0]
    assert n == 1


# ------------------------------------------------- store-backed cap + digest (§16/§28-30)


def test_contact_actions_last_7_days_counts_real_actions(store) -> None:
    """The anti-spam cap reads REAL contact actions from the audit trail:
    both the HUMAN approval path (external_action -> contacted) and direct
    transitions. Other audit noise must not inflate the count."""
    cid = store.upsert_company("Cap Co", "capco.test")
    lid = store.upsert_lead(cid, "https://capco.test", "run-cap")
    store.transition_lead(lid, "researched")
    store.transition_lead(lid, "qualified")
    store.transition_lead(lid, "approval_required")
    assert store.contact_actions_last_7_days() == 0

    # human-gated contact (the real path: bam approve -> bam contact)
    store.record_approval(subject_type="lead", subject_id=lid,
                          kind="commercial", to_state="approved",
                          decided_by="operator")
    store.record_approval(subject_type="lead", subject_id=lid,
                          kind="external_action", to_state="contacted",
                          decided_by="operator")
    assert store.contact_actions_last_7_days() == 1

    # a second lead contacted the same way also counts
    cid2 = store.upsert_company("Cap 2", "cap2.test")
    lid2 = store.upsert_lead(cid2, "https://cap2.test", "run-cap")
    store.transition_lead(lid2, "researched")
    store.transition_lead(lid2, "qualified")
    store.transition_lead(lid2, "approval_required")
    store.record_approval(subject_type="lead", subject_id=lid2,
                          kind="commercial", to_state="approved",
                          decided_by="operator")
    store.record_approval(subject_type="lead", subject_id=lid2,
                          kind="external_action", to_state="contacted",
                          decided_by="operator")
    assert store.contact_actions_last_7_days() == 2


# --------------------------------------------------------- queue from the store


def test_store_queue_candidates_excludes_dead_states(store) -> None:
    cid = store.upsert_company("Q Co", "qco.test")
    lid = store.upsert_lead(cid, "https://qco.test", "run-q")
    store.transition_lead(lid, "researched")
    cands = store.queue_candidates()
    assert len(cands) == 1 and cands[0]["id"] == lid
    assert cands[0]["evidence"] == []
    # disqualify: must vanish from the queue
    store.transition_lead(lid, "disqualified")
    assert store.queue_candidates() == []
