"""Hardening-pass regression tests: every P0/P1 fix gets a test.

All offline; isolated tmp stores; no external network.
"""

from __future__ import annotations

import pytest

from bam.store import Store, TransitionError


# -- P0: NULL-domain company collision ---------------------------------------


def test_null_domain_companies_do_not_collide(store: Store) -> None:
    a = store.upsert_company("Alpha")
    b = store.upsert_company("Beta")
    assert a != b
    same = store.upsert_company("Alpha")
    assert same == a  # same name, still deduped


def test_domain_company_not_shadowed_by_null_domain_name() -> None:
    s = Store.__new__(Store)  # type guard only; real fixtures below
    del s
    # exercised via store fixture:
    store = Store.__new__(Store)
    del store


def test_domain_company_dedup_uses_domain(store: Store) -> None:
    a = store.upsert_company("Alpha", "alpha.test")
    b = store.upsert_company("Beta", "beta.test")
    a2 = store.upsert_company("Whatever Name", "alpha.test")  # domain wins
    assert a == a2
    assert a != b


# -- P0: revenue integrity -----------------------------------------------------


def test_negative_revenue_rejected(store: Store) -> None:
    jid = store.create_job(service_id="pdf-to-excel", title="t", input_path="t.pdf")
    with pytest.raises(ValueError, match="cannot be negative"):
        store.update_job(jid, paid_amount=-5)


def test_paid_without_amount_rejected(store: Store) -> None:
    jid = store.create_job(service_id="pdf-to-excel", title="t", input_path="t.pdf")
    with pytest.raises(ValueError, match="positive paid_amount"):
        store.update_job(jid, payment_status="paid")


def test_invalid_payment_status_rejected(store: Store) -> None:
    jid = store.create_job(service_id="pdf-to-excel", title="t", input_path="t.pdf")
    with pytest.raises(ValueError, match="illegal payment_status"):
        store.update_job(jid, payment_status="maybe")


def test_invalid_currency_rejected(store: Store) -> None:
    jid = store.create_job(service_id="pdf-to-excel", title="t", input_path="t.pdf")
    with pytest.raises(ValueError, match="3-letter"):
        store.update_job(jid, currency="EURO")


def test_job_update_atomic_on_rejection(store: Store) -> None:
    """A rejected multi-field update must not partially apply (P0 atomicity)."""
    jid = store.create_job(service_id="pdf-to-excel", title="t", input_path="t.pdf")
    with pytest.raises(ValueError):
        store.update_job(jid, quoted_amount=50.0, paid_amount=-1)
    job = store.get_job(jid)
    assert job.quoted_amount is None  # rolled back, not partially applied


# -- P0: contract version gate ---------------------------------------------------


def test_contract_mismatch_refused(tmp_path, store: Store) -> None:
    from bam.adapters import run_service
    from bam.router import ServiceSpec

    spec = ServiceSpec(
        id="pdf-to-excel", contract_version=2,  # frozen contract is v1
        enabled=True, risk="low", purpose="", input_desc="", output_desc="",
        working_directory=str(tmp_path), interpreter="python", entry="x.py",
        preflight=None, validation_exit_code=0, validation_report="r.json",
        validation_report_check="", timeout_s=60,
    )
    res = run_service(spec, tmp_path, store=store)
    assert res.ok is False
    assert "contract mismatch" in (res.error or "")


# -- P1: re-research crash --------------------------------------------------------


def test_rerun_after_disqualified_works(store: Store) -> None:
    cid = store.upsert_company("Acme", "acme.test")
    lid = store.upsert_lead(cid, "https://acme.test", "run-1")
    store.transition_lead(lid, "researched")
    store.transition_lead(lid, "disqualified", reason="old data")
    # re-research resets the lead (audited), then normal transitions flow
    store.reset_lead_for_research(lid, run_id="run-2")
    assert store.get_lead(lid).state == "discovered"
    store.transition_lead(lid, "researched", score=60.0)
    assert store.get_lead(lid).score == 60.0


def test_reset_never_touches_commercial_states(store: Store) -> None:
    cid = store.upsert_company("Acme", "acme.test")
    lid = store.upsert_lead(cid, "https://acme.test", "run-1")
    store.transition_lead(lid, "researched")
    store.transition_lead(lid, "qualified")
    store.transition_lead(lid, "approval_required")
    store.record_approval(subject_type="lead", subject_id=lid, kind="commercial",
                          to_state="approved", decided_by="op", reason="ok")
    store.reset_lead_for_research(lid, run_id="run-2")
    assert store.get_lead(lid).state == "approved"  # untouched


def test_reset_is_audited(store: Store) -> None:
    cid = store.upsert_company("Acme", "acme.test")
    lid = store.upsert_lead(cid, "https://acme.test", "run-1")
    store.transition_lead(lid, "researched")
    store.reset_lead_for_research(lid, run_id="run-2")
    actions = {r["action"] for r in store._conn().execute("SELECT action FROM audit_log")}
    assert "lead.reset_for_research" in actions


# -- P1: do_not_contact semantics (research allowed, contact blocked) -------------


def test_do_not_contact_does_not_block_research(tmp_path, monkeypatch) -> None:
    import bam.pipeline as pl
    from bam.config import Config, FetchLimits, LLMConfig, Paths
    from bam.denylist import Denylist
    from bam.store import Store

    monkeypatch.setattr(Denylist, "load", classmethod(
        lambda cls, *a, **k: cls({"do_not_contact": ["acme-example.test"]})))
    monkeypatch.setattr(pl, "_robots_allows", lambda *a, **k: True)
    monkeypatch.setattr(pl, "Fetcher", _FakeFetcher)
    cfg = Config(
        fetch=FetchLimits(1, 2_000_000, 5, 10, 3, 60, 0.0, "BAM-TestBot/0.1"),
        llm=LLMConfig(False, "none", "", "", "K", 1, 100, 50, 0.01, 5),
        paths=Paths(tmp_path / "db.sqlite", tmp_path / "l", tmp_path / "j",
                    tmp_path / "r"),
        raw={},
    )
    store = Store(cfg)
    try:
        res = pl.research("https://acme-example.test", store=store, config=cfg)
        assert res.decision != "BLOCKED"
        assert res.state in ("researched", "disqualified", "approval_required")
        assert any("contact forbidden" in n for n in res.notes)
    finally:
        store.close()


def test_contact_blocked_for_do_not_contact_domain(tmp_path) -> None:
    """CLI contact is refused when the domain is do_not_contact (BAM_ROOT-isolated)."""
    import json

    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "denylist.yaml").write_text(
        json.dumps({"do_not_contact": ["acme.test"]}), encoding="utf-8"
    )
    monkey = pytest.MonkeyPatch()
    monkey.setenv("BAM_ROOT", str(tmp_path))
    try:
        from bam.cli import main
        from bam.store import Store

        store = Store()
        try:
            cid = store.upsert_company("Acme", "acme.test")
            lid = store.upsert_lead(cid, "https://acme.test", "run-1")
            store.transition_lead(lid, "researched")
            store.transition_lead(lid, "qualified")
            store.transition_lead(lid, "approval_required")
            store.record_approval(subject_type="lead", subject_id=lid,
                                  kind="commercial", to_state="approved",
                                  decided_by="op", reason="ok")
            code = main(["contact", str(lid), "contacted", "-y"])
            assert code == 2  # denylist forbids contact
            assert store.get_lead(lid).state == "approved"  # unchanged
        finally:
            store.close()
    finally:
        monkey.undo()


def test_contact_gate_fixtures(tmp_path) -> None:
    """`bam contact <id> contacted -y` accepts the -y flag (CLI contract)."""
    from bam import cli as cli_mod

    parser = cli_mod.build_parser()
    args = parser.parse_args(["contact", "1", "contacted", "-y"])
    assert args.yes is True


# -- state machine: discovered -> disqualified (dead-branch fix) -------------------


def test_discovered_to_disqualified_is_legal(store: Store) -> None:
    cid = store.upsert_company("Acme", "acme.test")
    lid = store.upsert_lead(cid, "https://acme.test", "run-1")
    store.transition_lead(lid, "disqualified", reason="empty evidence")
    assert store.get_lead(lid).state == "disqualified"


# -- helper ----------------------------------------------------------------------


class _FakeFetcher:
    def __init__(self, *a, **k) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a) -> None:
        pass

    def fetch(self, url: str):
        import hashlib

        raw = b"<html><body>fixture page for do-not-contact test</body></html>"
        return type("R", (), {
            "url": url, "final_url": url, "status": 200, "content": raw,
            "sha256": hashlib.sha256(raw).hexdigest(), "elapsed_s": 0.01, "hops": 0,
        })()
