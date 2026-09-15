"""E2E production flow test: full prospect → payment cycle.

Uses the isolated tmp-store fixture - this test previously wrote to the
PRODUCTION data/bam.db on every pytest run (red-team finding, fixed).
"""

from __future__ import annotations

import time

from bam.store import Store, TransitionError


def test_full_e2e_flow(store: Store):
    ts = int(time.time())

    # 1. Create fresh lead for testing (unique domain)
    domain = f"e2e-{ts}.test"
    cid = store.upsert_company("E2E Corp", domain)
    lid = store.upsert_lead(cid, f"https://{domain}", "run-e2e-1")
    lead = store.get_lead(lid)
    print(f"1. Fresh lead created: id={lid}, state={lead.state}")
    assert lead.state == "discovered"

    # 2. Research → researched
    store.transition_lead(lid, "researched", score=75.0, confidence="high")
    lead = store.get_lead(lid)
    print(f"2. After researched: state={lead.state}, score={lead.score}")
    assert lead.state == "researched"

    # 3. qualified → approval_required
    store.transition_lead(lid, "qualified")
    store.transition_lead(lid, "approval_required", reason="score >= 70",
                          recommended_service="pdf-to-excel")
    lead = store.get_lead(lid)
    print(f"3. After approval_required: state={lead.state}")
    assert lead.state == "approval_required"

    # 4. Human approval
    store.record_approval(
        subject_type="lead", subject_id=lid, kind="commercial",
        to_state="approved", decided_by="operator", reason="E2E test",
    )
    lead = store.get_lead(lid)
    print(f"4. After approval: state={lead.state}")
    assert lead.state == "approved"

    # 5. Record contact
    store.record_approval(
        subject_type="lead", subject_id=lid, kind="external_action",
        to_state="contacted", decided_by="operator", reason="email sent",
    )
    store.metric(name="contact_recorded", value=1, unit="count", lead_id=lid)
    lead = store.get_lead(lid)
    print(f"5. After contact: state={lead.state}")
    assert lead.state == "contacted"

    # 6. Create client
    client_id = store.create_client("E2E Test Client")
    print(f"6. Client created: id={client_id}")

    # 7. Create job
    job_id = store.create_job(
        service_id="pdf-to-excel", title="test.pdf",
        input_path="test.pdf", client_id=client_id,
        agreed_amount=150.0, currency="EUR",
    )
    print(f"7. Job created: id={job_id}")

    # 8. Job lifecycle
    store.update_job(job_id, state="classified")
    store.update_job(job_id, state="assigned")
    store.update_job(job_id, state="delivered")
    job = store._conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    print(f"8. Job state: {job['state']}")
    assert job["state"] == "delivered"

    # 9. Record payment
    store.record_payment(job_id, paid_amount=150.0, currency="EUR")
    job = store._conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    print(f"9. Payment recorded: paid={job['paid_amount']}")
    assert job["paid_amount"] == 150.0

    # 10. Revenue integrity
    digest = store.digest()
    print(f"10. Digest: revenue={digest['revenue_paid']}, jobs={digest['jobs_by_state']}")
    assert digest["revenue_paid"] >= 150.0

    # 11. Audit trail
    audit = store._conn().execute(
        "SELECT COUNT(*) as cnt FROM audit_log WHERE entity_type='lead' AND entity_id=?",
        (lid,),
    ).fetchone()
    print(f"11. Audit trail for lead #{lid}: {audit['cnt']} entries")
    assert audit["cnt"] >= 5

    # 12. Re-research works on non-commercial states only
    domain2 = f"rerun-{ts}.test"
    cid2 = store.upsert_company("Rerun Corp", domain2)
    lid2 = store.upsert_lead(cid2, f"https://{domain2}", "run-rerun")
    store.transition_lead(lid2, "researched")
    store.transition_lead(lid2, "disqualified", reason="test")
    lead2 = store.get_lead(lid2)
    print(f"12a. Lead #{lid2} state: {lead2.state}")
    assert lead2.state == "disqualified"

    store.reset_lead_for_research(lid2, run_id="e2e-rerun")
    lead2 = store.get_lead(lid2)
    print(f"12b. After reset: state={lead2.state}")
    assert lead2.state == "discovered"

    # 13. Commercial states are NOT reset
    store.reset_lead_for_research(lid, run_id="e2e-rerun")
    lead = store.get_lead(lid)
    print(f"13. Commercial lead #{lid} NOT reset: state={lead.state}")
    assert lead.state == "contacted"

    # 14. do_not_contact blocks contact transition
    domain3 = f"blocked-{ts}.test"
    cid3 = store.upsert_company("BlockedCo", domain3)
    lid3 = store.upsert_lead(cid3, f"https://{domain3}", "run-block")
    store.transition_lead(lid3, "researched")
    store.transition_lead(lid3, "qualified")
    store.transition_lead(lid3, "approval_required")
    store.record_approval(subject_type="lead", subject_id=lid3, kind="commercial",
                          to_state="approved", decided_by="operator")
    try:
        store.transition_lead(lid3, "contacted", actor="human")
        print("14. FAIL: contact should have been blocked")
        assert False
    except TransitionError:
        print("14. OK: contact blocked for do_not_contact")

    print("\n=== E2E FLOW COMPLETE ===")
