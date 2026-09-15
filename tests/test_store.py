"""Store tests: schema, state machine classes, approvals, jobs, digest."""

from __future__ import annotations

import pytest

from bam.store import JobState, Store, TransitionError


def test_migrations_create_schema(store: Store) -> None:
    conn = store._conn()
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"companies", "leads", "evidence", "claims", "approvals", "jobs",
            "clients", "run_manifests", "audit_log", "metrics"} <= tables


def test_company_and_lead_upsert(store: Store) -> None:
    cid = store.upsert_company("Acme", "acme.test", "services")
    cid2 = store.upsert_company("Acme", "acme.test")
    assert cid == cid2  # dedupe by domain
    lid = store.upsert_lead(cid, "https://acme.test", "run-1")
    lid2 = store.upsert_lead(cid, "https://acme.test", "run-2")
    assert lid == lid2


def test_automatic_transitions_ok(store: Store) -> None:
    cid = store.upsert_company("Acme", "acme.test")
    lid = store.upsert_lead(cid, "https://acme.test", "run-1")
    store.transition_lead(lid, "researched", score=72.0, confidence="high")
    store.transition_lead(lid, "qualified")
    store.transition_lead(lid, "approval_required", reason="score")
    lead = store.get_lead(lid)
    assert lead.state == "approval_required"
    assert lead.score == 72.0


def test_human_transition_blocked_without_approval(store: Store) -> None:
    cid = store.upsert_company("Acme", "acme.test")
    lid = store.upsert_lead(cid, "https://acme.test", "run-1")
    store.transition_lead(lid, "researched")
    store.transition_lead(lid, "qualified")
    store.transition_lead(lid, "approval_required")
    with pytest.raises(TransitionError):
        store.transition_lead(lid, "approved", actor="agent")


def test_illegal_transition_raises(store: Store) -> None:
    cid = store.upsert_company("Acme", "acme.test")
    lid = store.upsert_lead(cid, "https://acme.test", "run-1")
    with pytest.raises(TransitionError):
        store.transition_lead(lid, "won")  # discovered -> won is illegal


def test_record_approval_performs_human_transition(store: Store) -> None:
    cid = store.upsert_company("Acme", "acme.test")
    lid = store.upsert_lead(cid, "https://acme.test", "run-1")
    store.transition_lead(lid, "researched")
    store.transition_lead(lid, "qualified")
    store.transition_lead(lid, "approval_required")
    approval_id = store.record_approval(
        subject_type="lead", subject_id=lid, kind="commercial",
        to_state="approved", decided_by="operator", reason="fit confirmed",
        run_id="run-1",
    )
    assert approval_id > 0
    assert store.get_lead(lid).state == "approved"
    # approval record is complete: from-state, actor, reason
    hist = store._conn().execute("SELECT * FROM approvals").fetchone()
    assert hist["state_before"] == "approval_required"
    assert hist["state_after"] == "approved"
    assert hist["decided_by"] == "operator"


def test_contact_requires_approval_chain(store: Store) -> None:
    cid = store.upsert_company("Acme", "acme.test")
    lid = store.upsert_lead(cid, "https://acme.test", "run-1")
    store.transition_lead(lid, "researched")
    with pytest.raises(TransitionError):
        store.record_approval(subject_type="lead", subject_id=lid,
                              kind="external_action", to_state="contacted",
                              decided_by="operator")


def test_every_write_is_audited(store: Store) -> None:
    cid = store.upsert_company("Acme", "acme.test")
    lid = store.upsert_lead(cid, "https://acme.test", "run-1")
    store.add_evidence(lid, kind="page", url="https://acme.test",
                       sha256="ab" * 32, observed_at="2026-09-14T00:00:00+00:00",
                       excerpt="hello", status="OBSERVED")
    actions = {r["action"] for r in store._conn().execute(
        "SELECT action FROM audit_log")}
    assert {"company.upsert", "lead.create", "evidence.add"} <= actions


def test_job_lifecycle_and_payment(store: Store) -> None:
    jid = store.create_job(service_id="pdf-to-excel", title="test.pdf",
                           input_path="input/test.pdf", quoted_amount=100.0,
                           agreed_amount=90.0)
    store.update_job(jid, state=JobState.DELIVERED.value)
    store.record_payment(jid, paid_amount=90.0, payment_date="2026-09-14")
    job = store.get_job(jid)
    assert job.state == "delivered"
    assert job.payment_status == "paid"
    assert job.paid_amount == 90.0
    with pytest.raises(Exception):
        store.update_job(jid, state="quantum")  # invalid job state rejected


def test_digest_metrics(store: Store) -> None:
    cid = store.upsert_company("Acme", "acme.test")
    lid = store.upsert_lead(cid, "https://acme.test", "run-1")
    store.transition_lead(lid, "researched")
    store.metric(name="manual_minutes", value=30)
    store.metric(name="engineering_minutes", value=120)
    jid = store.create_job(service_id="pdf-to-excel", title="t.pdf",
                           input_path="t.pdf")
    store.update_job(jid, state=JobState.DELIVERED.value)
    store.record_payment(jid, paid_amount=150.0)
    d = store.digest()
    assert d["leads_researched"] >= 1
    assert d["revenue_paid"] == 150.0
    assert d["manual_minutes"] == 30.0
