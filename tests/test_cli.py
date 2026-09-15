"""CLI tests through main() - human gates, job flow, digest."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from bam.cli import main


@pytest.fixture()
def fake_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fake pdf-to-excel service wired into a tmp registry + config."""
    fake = tmp_path / "fake_cli.py"
    fake.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "a=sys.argv[1:]\nout=Path(a[a.index('-o')+1])\n"
        "out.mkdir(parents=True,exist_ok=True)\n"
        "(out/'report.json').write_text(json.dumps({'total_files':1,"
        "'results':[{'error':None}]}),encoding='utf-8')\n"
        "(out/'result.xlsx').write_bytes(b'x')\nsys.exit(0)\n",
        encoding="utf-8",
    )
    registry = tmp_path / "config" / "service-registry.yaml"
    registry.parent.mkdir(exist_ok=True)
    registry.write_text(
        "services:\n"
        "  pdf-to-excel:\n"
        "    contract_version: 1\n"
        "    enabled: true\n"
        "    risk: low\n"
        f"    working_directory: {json.dumps(str(tmp_path))}\n"
        f"    interpreter: {json.dumps(sys.executable)}\n"
        f"    entry: {json.dumps(fake.name)}\n"
        "    preflight: null\n"
        "    validation:\n"
        "      exit_code: 0\n"
        "      report: report.json\n"
        "      report_check: none\n"
        "    timeout_s: 60\n",
        encoding="utf-8",
    )
    # Full isolation: BAM_ROOT redirects config/weights/denylist/db paths.
    monkeypatch.setenv("BAM_ROOT", str(tmp_path))
    return fake


def test_approve_requires_full_chain(fake_service, capsys) -> None:
    # no lead 5
    assert main(["approve", "5", "-y"]) == 2
    # build a lead at approval_required through the store
    from bam.store import Store

    store = Store()
    try:
        cid = store.upsert_company("Acme", "acme.test")
        lid = store.upsert_lead(cid, "https://acme.test", "run-1")
        store.transition_lead(lid, "researched")
        store.transition_lead(lid, "qualified")
        store.transition_lead(lid, "approval_required")
        assert main(["approve", str(lid), "-y", "--reason", "ok"]) == 0
        assert store.get_lead(lid).state == "approved"
        # contact gate: only after approval
        assert main(["contact", str(lid), "contacted", "--reason", "called"]) == 0
        assert store.get_lead(lid).state == "contacted"
        # approval history exists with full transaction
        hist = store._conn().execute(
            "SELECT COUNT(*) c FROM approvals").fetchone()["c"]
        assert hist >= 2
    finally:
        store.close()


def test_approve_rejects_wrong_state(fake_service, capsys) -> None:
    from bam.store import Store

    store = Store()
    try:
        cid = store.upsert_company("Acme", "acme.test")
        lid = store.upsert_lead(cid, "https://acme.test", "run-1")
        assert main(["approve", str(lid), "-y"]) == 2  # discovered -> approved illegal
    finally:
        store.close()


def test_deliver_end_to_end(fake_service, tmp_path: Path, capsys) -> None:
    inp = tmp_path / "invoice.pdf"
    inp.write_bytes(b"%PDF-1.4")
    code = main(["deliver", str(inp), "--service", "pdf-to-excel",
                 "--client", "Test Client", "--quoted", "80", "--agreed", "70",
                 "--yes"])
    assert code == 0
    from bam.store import Store

    store = Store()
    try:
        job = store.list_jobs()[-1]
        assert job.state == "delivered"
        assert job.agreed_amount == 70.0
        assert job.client_id is not None
        assert main(["pay", str(job.id), "--amount", "70"]) == 0
        d = store.digest()
        assert d["revenue_paid"] == 70.0
    finally:
        store.close()


def test_digest_command_runs(fake_service, capsys) -> None:
    assert main(["digest"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "lead_to_client_conversion" in data
    assert "revenue_per_manual_hour" in data
    # sales machine §28: tiers and source/query quality are part of the digest
    assert data["tiers"] == {"A": 0, "B": 0, "C": 0, "D": 0}
    assert data["source_quality"] == [] and data["query_quality"] == []


def test_digest_includes_tier_counts(fake_service, capsys) -> None:
    from bam.store import Store

    store = Store()
    try:
        cid = store.upsert_company("Tier Co", "tierco.test")
        lid = store.upsert_lead(cid, "https://tierco.test", "run-tier")
        store.transition_lead(lid, "researched")
        data = store.digest()
        assert sum(data["tiers"].values()) >= 1
    finally:
        store.close()


def test_next_counts_real_contact_budget(fake_service, capsys) -> None:
    """bam next feeds its anti-spam cap from the audit trail: after one real
    contact approval, the queue's remaining budget drops by one."""
    from bam.copilot import build_daily_queue
    from bam.store import Store

    store = Store()
    try:
        cid = store.upsert_company("Budget Co", "budgetco.test")
        lid = store.upsert_lead(cid, "https://budgetco.test", "run-budget")
        store.transition_lead(lid, "researched")
        store.transition_lead(lid, "qualified")
        store.transition_lead(lid, "approval_required")
        store.record_approval(subject_type="lead", subject_id=lid,
                              kind="commercial", to_state="approved",
                              decided_by="operator")
        store.record_approval(subject_type="lead", subject_id=lid,
                              kind="external_action", to_state="contacted",
                              decided_by="operator")
        assert store.contact_actions_last_7_days() == 1

        # the cap function the queue uses reflects the spent budget:
        # with max 5/day, 4 contact-actions remain for new leads
        candidates = store.queue_candidates()
        entries, _ = build_daily_queue(candidates, limit=5,
                                       contacted_this_week=1,
                                       max_daily_outreach=5)
        contact_today = sum(1 for e in entries if e.action == "CONTACT TODAY")
        assert contact_today <= 4
    finally:
        store.close()
    assert main(["next"]) == 0
    out = capsys.readouterr().out
    assert "TODAY'S SALES QUEUE" in out
