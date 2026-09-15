"""Router + adapter tests (offline; services simulated by local fake scripts)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from bam.adapters import run_service
from bam.router import ClassificationError, ServiceSpec, classify_job, load_registry
from bam.store import Store

# fake pdf-to-excel: argv = [fake.py, -i, INPUT, -o, OUTDIR]
FAKE_PDF = r'''
import json, sys
from pathlib import Path
a = sys.argv[1:]
outdir = Path(a[a.index("-o") + 1])
outdir.mkdir(parents=True, exist_ok=True)
(outdir / "report.json").write_text(json.dumps({
    "total_files": 1, "total_tables": 1, "results": [{"error": None}]
}), encoding="utf-8")
(outdir / "out.xlsx").write_bytes(b"fake-xlsx")
sys.exit(0)
'''

# fake excel-cleaner: argv = [fake.py, INPUT, OUTDIR]; errors=2 in report, exit 0
# (input may be a single copied file or a directory - accept either)
FAKE_CLEANER = r'''
import json, sys
from pathlib import Path
a = sys.argv[1:]
outdir = Path(a[-1])
outdir.mkdir(parents=True, exist_ok=True)
(outdir / "batch_audit_summary.json").write_text(json.dumps(
    {"processed_ok": 1, "processed_invalid": 0, "errors": 2, "total_changes": 5}
), encoding="utf-8")
sys.exit(0)
'''


def _pdf_spec(tmp_path: Path, **over) -> ServiceSpec:
    fake = tmp_path / "fake_service.py"
    fake.write_text(FAKE_PDF, encoding="utf-8")
    kwargs = dict(
        id="pdf-to-excel", contract_version=1, enabled=True, risk="low",
        purpose="test", input_desc="pdf", output_desc="xlsx",
        working_directory=str(tmp_path), interpreter=sys.executable,
        entry=fake.name, preflight=None, validation_exit_code=0,
        validation_report="report.json",
        validation_report_check="no fatal per-file errors", timeout_s=60,
    )
    kwargs.update(over)
    return ServiceSpec(**kwargs)


def _cleaner_spec(tmp_path: Path) -> ServiceSpec:
    fake = tmp_path / "fake_cleaner.py"
    fake.write_text(FAKE_CLEANER, encoding="utf-8")
    return ServiceSpec(
        id="excel-cleaner", contract_version=1, enabled=True, risk="medium",
        purpose="test", input_desc="dir", output_desc="dir",
        working_directory=str(tmp_path), interpreter=sys.executable,
        entry=fake.name, preflight=None, validation_exit_code=0,
        validation_report="batch_audit_summary.json",
        validation_report_check="errors == 0", timeout_s=60,
    )


# -- classification ----------------------------------------------------------


def test_classify_pdf(tmp_path: Path) -> None:
    p = tmp_path / "invoice.pdf"
    p.write_bytes(b"%PDF-1.4")
    assert classify_job(p) == "pdf-to-excel"


def test_classify_sheet(tmp_path: Path) -> None:
    p = tmp_path / "messy.csv"
    p.write_text("a,b\n1,2")
    assert classify_job(p) == "excel-cleaner"


def test_classify_dir_by_contents(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    assert classify_job(tmp_path) == "pdf-to-excel"
    d2 = tmp_path / "sheets"
    d2.mkdir()
    (d2 / "x.xlsx").write_bytes(b"xx")
    assert classify_job(d2) == "excel-cleaner"


def test_classify_unknown_raises(tmp_path: Path) -> None:
    p = tmp_path / "mystery.zzz"
    p.write_bytes(b"?")
    with pytest.raises(ClassificationError):
        classify_job(p)


def test_registry_loads() -> None:
    reg = load_registry()
    assert "pdf-to-excel" in reg and "excel-cleaner" in reg
    spec = reg["pdf-to-excel"]
    assert spec.contract_version == 1
    assert spec.validation_report == "report.json"
    # the frozen contract values survive into the registry
    assert reg["excel-cleaner"].validation_report == "batch_audit_summary.json"


# -- adapter protocol ----------------------------------------------------------


def test_adapter_happy_path(tmp_path: Path, store: Store) -> None:
    spec = _pdf_spec(tmp_path)
    inp = tmp_path / "in.pdf"
    inp.write_bytes(b"%PDF-1.4 fake")
    res = run_service(spec, inp, store=store, job_id=None)
    assert res.ok is True
    assert any(a["path"].endswith("out.xlsx") for a in res.artifacts)
    assert all(a["sha256"] for a in res.artifacts)


def test_adapter_nonzero_exit_fails(tmp_path: Path, store: Store) -> None:
    fake = tmp_path / "fail_service.py"
    fake.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    spec = _pdf_spec(tmp_path, entry=fake.name)
    inp = tmp_path / "in.pdf"
    inp.write_bytes(b"%PDF")
    res = run_service(spec, inp, store=store)
    assert res.ok is False
    assert "exited 1" in (res.error or "")


def test_adapter_missing_report_fails(tmp_path: Path, store: Store) -> None:
    spec = _pdf_spec(tmp_path, validation_report="definitely_missing.json")
    inp = tmp_path / "in.pdf"
    inp.write_bytes(b"%PDF")
    res = run_service(spec, inp, store=store)
    assert res.ok is False
    assert "missing machine-readable report" in (res.error or "")


def test_excel_cleaner_quarantine_trap_caught(tmp_path: Path, store: Store) -> None:
    """Exit 0 + errors>0 in batch_audit_summary.json MUST fail (contract rule)."""
    spec = _cleaner_spec(tmp_path)
    inp = tmp_path / "sheets"
    inp.mkdir()
    (inp / "m.csv").write_text("a,b\n1,2")
    res = run_service(spec, inp, store=store)
    assert res.ok is False
    assert "report_check failed" in (res.error or "")


def test_adapter_updates_job_state(tmp_path: Path, store: Store) -> None:
    spec = _pdf_spec(tmp_path)
    inp = tmp_path / "in.pdf"
    inp.write_bytes(b"%PDF")
    jid = store.create_job(service_id="pdf-to-excel", title="in.pdf",
                           input_path=str(inp))
    res = run_service(spec, inp, store=store, job_id=jid)
    assert res.ok
    assert store.get_job(jid).state == "delivered"
    assert store.get_job(jid).sandbox_dir


def test_adapter_disabled_service_refused(tmp_path: Path, store: Store) -> None:
    spec = _pdf_spec(tmp_path, enabled=False)
    inp = tmp_path / "in.pdf"
    inp.write_bytes(b"%PDF")
    res = run_service(spec, inp, store=store)
    assert res.ok is False
    assert "disabled" in (res.error or "")


def test_adapter_missing_workdir_fails(tmp_path: Path, store: Store) -> None:
    spec = _pdf_spec(tmp_path, working_directory=str(tmp_path / "nope"))
    inp = tmp_path / "in.pdf"
    inp.write_bytes(b"%PDF")
    res = run_service(spec, inp, store=store)
    assert res.ok is False
    assert "working directory" in (res.error or "")


def test_adapter_times_out(tmp_path: Path, store: Store) -> None:
    fake = tmp_path / "slow_service.py"
    fake.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    spec = _pdf_spec(tmp_path, entry=fake.name, timeout_s=1)
    inp = tmp_path / "in.pdf"
    inp.write_bytes(b"%PDF")
    res = run_service(spec, inp, store=store)
    assert res.ok is False
    assert "timed out" in (res.error or "")


def test_adapter_contract_version_mismatch_rejected(tmp_path: Path, store: Store) -> None:
    """Mismatch between registry contract_version and BAM frozen version MUST be rejected
    BEFORE the service is invoked."""
    # Create a fake service that would write a marker file if actually executed
    marker = tmp_path / "service_was_called.marker"
    fake = tmp_path / "fake_pdf_v2.py"
    fake.write_text(f'''
import json, sys
from pathlib import Path
a = sys.argv[1:]
outdir = Path(a[a.index("-o") + 1])
outdir.mkdir(parents=True, exist_ok=True)
# Write marker to prove execution
Path(r"{marker}").write_text("executed", encoding="utf-8")
(outdir / "report.json").write_text(json.dumps({{
    "total_files": 1, "total_tables": 1, "results": [{{"error": None}}]
}}), encoding="utf-8")
(outdir / "out.xlsx").write_bytes(b"fake-xlsx")
sys.exit(0)
''', encoding="utf-8")

    # BAM frozen version is 1, but registry declares 2
    spec = ServiceSpec(
        id="pdf-to-excel", contract_version=2, enabled=True, risk="low",
        purpose="test", input_desc="pdf", output_desc="xlsx",
        working_directory=str(tmp_path), interpreter=sys.executable,
        entry=fake.name, preflight=None, validation_exit_code=0,
        validation_report="report.json",
        validation_report_check="no fatal per-file errors", timeout_s=60,
    )

    inp = tmp_path / "in.pdf"
    inp.write_bytes(b"%PDF")

    res = run_service(spec, inp, store=store)

    # Should be rejected due to version mismatch
    assert res.ok is False
    assert "contract mismatch" in (res.error or "").lower()
    assert "v2" in (res.error or "")
    assert "v1" in (res.error or "")

    # Service should NOT have been executed (marker file not created)
    assert not marker.exists(), "Service was executed despite version mismatch!"
