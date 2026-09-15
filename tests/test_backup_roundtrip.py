"""Backup roundtrip verification: every backup must PROVE it can be
restored (integrity + row counts vs live + restore drill), and restore()
must refuse to touch the live DB when the backup is corrupt.

Covers the demonstrated gaps: silent unverified backups, VACUUM INTO SQL
literal breakage on quoted paths, WAL frames replaying over a restored file,
and un-audited restores.
"""

from __future__ import annotations

import sqlite3

import pytest

from bam.store import Store


@pytest.fixture()
def populated(store: Store) -> Store:
    cid = store.upsert_company("Roundtrip Co", "rt.test")
    lid = store.upsert_lead(cid, "https://rt.test", "run-rt")
    store.transition_lead(lid, "researched")
    return store


def test_backup_is_verified_roundtrip(store: Store, tmp_path) -> None:
    cid = store.upsert_company("Roundtrip Co", "rt.test")
    store.upsert_lead(cid, "https://rt.test", "run-rt")
    result = store.backup(tmp_path / "backups")

    assert result["verified"] is True
    assert result["tables"] >= 13
    assert result["rows"] > 0
    assert (tmp_path / "backups" / "bam_2026.db").parent.exists()
    import glob

    files = glob.glob(str(tmp_path / "backups" / "bam_*.db"))
    assert len(files) == 1 and not any(f.endswith(".failed") for f in files)
    # audit trail records the verified backup
    audit = store._conn().execute(
        "SELECT payload_json FROM audit_log WHERE action='db.backup'"
        " ORDER BY id DESC LIMIT 1").fetchone()
    assert audit is not None


def test_backup_detects_row_divergence(populated: Store, tmp_path) -> None:
    result = populated.backup(tmp_path / "backups")
    assert result["verified"] is True

    # diverge the live DB after the snapshot
    cid = populated.upsert_company("Late Co", "late.test")
    populated.upsert_lead(cid, "https://late.test", "run-late")

    with pytest.raises(RuntimeError, match="diverge"):
        populated._verify_backup_roundtrip(
            next((tmp_path / "backups").glob("bam_*.db")))


def test_backup_fails_closed_and_quarantines(store: Store, tmp_path,
                                             monkeypatch) -> None:
    store.upsert_company("T", "t.test")

    def boom(_self, _path):
        raise RuntimeError("simulated verification failure")

    monkeypatch.setattr(Store, "_verify_backup_roundtrip", boom)
    with pytest.raises(RuntimeError, match="simulated"):
        store.backup(tmp_path / "backups")
    failed = list((tmp_path / "backups").glob("*.failed"))
    assert failed, "unproven backup must be quarantined, never presented as valid"
    assert not list((tmp_path / "backups").glob("bam_*.db"))
    audit = store._conn().execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='db.backup_failed'").fetchone()[0]
    assert audit == 1


def test_backup_path_with_quotes(store: Store, tmp_path) -> None:
    # the old f-string SQL literal would break on the apostrophe
    store.upsert_company("T", "t.test")
    result = store.backup(tmp_path / "bob's backups")
    assert result["verified"] is True


def test_restore_refuses_corrupt_backup_fail_closed(populated: Store,
                                                    tmp_path) -> None:
    populated.backup(tmp_path / "backups")
    live_size = populated.config.paths.database.stat().st_size

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"this is not a sqlite database at all")
    with pytest.raises(RuntimeError, match="not a valid database"):
        populated.restore(corrupt)

    # live DB untouched
    assert populated.config.paths.database.stat().st_size == live_size
    assert populated.get_lead(1) is not None
    assert populated.get_lead(1).company_name == "Roundtrip Co"


def test_restore_roundtrip_preserves_and_audits(populated: Store,
                                                tmp_path) -> None:
    populated.backup(tmp_path / "backups")

    # mutate live AFTER the snapshot
    cid = populated.upsert_company("Post-Backup Co", "pb.test")
    populated.upsert_lead(cid, "https://pb.test", "run-pb")
    assert populated.find_lead_by_domain("pb.test") is not None

    backup_file = next((tmp_path / "backups").glob("bam_*.db"))
    assert populated.restore(backup_file) is True

    # post-backup mutation is gone; snapshot data is back
    assert populated.find_lead_by_domain("pb.test") is None
    lead = populated.find_lead_by_domain("rt.test")
    assert lead is not None and lead.state == "researched"

    # restore is audited in the restored DB, safety copy exists
    audit = populated._conn().execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='db.restore'").fetchone()[0]
    assert audit >= 1
    safety = list(populated.config.paths.database.parent.glob("*.pre_restore_*.db"))
    assert safety, "pre-restore safety copy must exist"


def test_restore_rejects_missing_file(populated: Store, tmp_path) -> None:
    with pytest.raises(ValueError, match="not found"):
        populated.restore(tmp_path / "nope.db")


# -- retention policy ----------------------------------------------------------


def _with_keep(config, keep):
    """Config clone whose raw YAML dict carries backup.keep."""
    import dataclasses

    return dataclasses.replace(
        config, raw={"backup": {"keep": keep}})


def test_backup_prunes_to_keep_limit(config, tmp_path) -> None:
    cfg = _with_keep(config, 3)
    s = Store(cfg)
    try:
        cid = s.upsert_company("Reten Co", "reten.test")
        s.upsert_lead(cid, "https://reten.test", "run-rt")
        bdir = tmp_path / "backups"
        total_pruned = 0
        for _ in range(5):
            r = s.backup(bdir)
            assert r["verified"] is True
            total_pruned += len(r["pruned"])
        files = sorted(bdir.glob("bam_*.db"))
        assert len(files) == 3, "retention must keep exactly `keep` backups"
        assert r["kept"] == 3
        # each run prunes only its own excess: none, none, none, 1, 1
        assert total_pruned == 2
        assert files == sorted(bdir.glob("bam_*.db"))
        # pruning is audited
        audits = s._conn().execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='db.backup_pruned'").fetchone()[0]
        assert audits >= 1
    finally:
        s.close()


def test_backup_retention_default_five(config, tmp_path) -> None:
    s = Store(config)  # raw={} -> default keep=5
    try:
        cid = s.upsert_company("Default Co", "default.test")
        s.upsert_lead(cid, "https://default.test", "run-d")
        bdir = tmp_path / "backups"
        for _ in range(7):
            s.backup(bdir)
        assert len(list(bdir.glob("bam_*.db"))) == 5
    finally:
        s.close()


def test_backup_pruning_never_touches_quarantine(config, tmp_path) -> None:
    cfg = _with_keep(config, 1)
    s = Store(cfg)
    try:
        cid = s.upsert_company("Quar Co", "quar.test")
        s.upsert_lead(cid, "https://quar.test", "run-q")
        bdir = tmp_path / "backups"
        s.backup(bdir)
        # a quarantined backup from a past verification failure
        failed = bdir / "bam_2020-01-01_00-00-00.db.failed"
        failed.write_bytes(b"quarantine evidence")
        r = s.backup(bdir)
        assert r["verified"] is True
        assert failed.exists(), "*.failed files must never be pruned"
        assert len(list(bdir.glob("bam_*.db"))) == 1
    finally:
        s.close()


def test_backup_same_second_no_collision(config, tmp_path) -> None:
    """Regression: two backups within the same second must not collide on
    the timestamp filename (VACUUM INTO fails on an existing target)."""
    s = Store(config)
    try:
        cid = s.upsert_company("Fast Co", "fast.test")
        s.upsert_lead(cid, "https://fast.test", "run-f")
        bdir = tmp_path / "backups"
        r1 = s.backup(bdir)
        r2 = s.backup(bdir)
        assert r1["path"] != r2["path"]
        assert len(list(bdir.glob("bam_*.db"))) == 2
    finally:
        s.close()


def test_backup_keep_validation(config, tmp_path) -> None:
    s = Store(_with_keep(config, 0))
    with pytest.raises(ValueError, match="keep must be >= 1"):
        s.backup(tmp_path / "backups")
    s.close()
    s2 = Store(_with_keep(config, "three"))
    with pytest.raises(ValueError, match="must be an integer"):
        s2.backup(tmp_path / "backups")
    s2.close()
