"""Subprocess adapters (plan v3.1 §18) - the ONLY bridge to existing services.

9 fixed steps: sandbox -> input -> run -> capture -> exit-code check ->
JSON validation -> hashing -> manifest -> normalized result.
Never imports service internals; fixed argv; no shell.
"""

from __future__ import annotations

import datetime
import json
import shutil
import subprocess  # noqa: S404 - fixed argv, no shell, by design
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bam.evidence import redact
from bam.manifest import RunManifest, atomic_write_json, new_run_id
from bam.router import ServiceSpec
from bam.store import JobState, Store

# Frozen contract versions (docs/service-contracts.md). The registry must
# declare a matching contract_version or the run is refused (plan v3.1 §20).
FROZEN_CONTRACT_VERSIONS: dict[str, int] = {
    "pdf-to-excel": 1,
    "excel-cleaner": 1,
    "qa-agent": 0,
}


class AdapterError(RuntimeError):
    """Service run failed validation (exit code or machine-readable report)."""


@dataclass
class AdapterResult:
    ok: bool
    service_id: str
    job_id: int | None
    sandbox_dir: Path
    exit_code: int
    artifacts: list[dict[str, str]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    run_id: str = ""


def _sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_interpreter(spec: ServiceSpec) -> str:
    """Resolve the service interpreter (env override wins)."""
    env_key = f"PROJECT_{spec.id.upper().replace('-', '_')}_PYTHON"
    import os

    return os.environ.get(env_key, spec.interpreter)


def run_service(
    spec: ServiceSpec,
    input_path: Path,
    *,
    store: Store,
    job_id: int | None = None,
    extra_args: list[str] | None = None,
) -> AdapterResult:
    """Execute one service against one input under the full adapter protocol."""
    if not spec.enabled:
        return AdapterResult(False, spec.id, job_id, Path(), -1,
                             error=f"service {spec.id} is disabled in the registry")
    frozen = FROZEN_CONTRACT_VERSIONS.get(spec.id)
    if frozen is not None and spec.contract_version != frozen:
        return AdapterResult(
            False, spec.id, job_id, Path(), -1,
            error=(f"contract mismatch for {spec.id}: registry declares "
                   f"v{spec.contract_version}, frozen contract is v{frozen} - "
                   f"update docs/service-contracts.md first"),
        )
    if not Path(spec.working_directory).exists():
        return AdapterResult(False, spec.id, job_id, Path(), -1,
                             error=f"working directory not found: {spec.working_directory}")

    run_id = new_run_id()
    manifest = RunManifest(run_id=run_id, kind=f"service:{spec.id}")

    # 1. sandbox
    jobs_root = store.config.paths.jobs_dir
    sandbox = jobs_root / f"job{job_id or 'manual'}_{run_id}"
    sandbox.mkdir(parents=True, exist_ok=True)
    out_dir = sandbox / "output"
    out_dir.mkdir(exist_ok=True)
    # 2. input: copy files in (originals never touched)
    #    excel-cleaner expects a directory; wrap single files into one.
    if input_path.is_dir():
        work_input = sandbox / "input"
        shutil.copytree(input_path, work_input, dirs_exist_ok=True)
    elif spec.id == "excel-cleaner":
        work_input = sandbox / "input"
        work_input.mkdir(exist_ok=True)
        shutil.copy2(input_path, work_input / input_path.name)
    else:
        work_input = sandbox / input_path.name
        shutil.copy2(input_path, work_input)
    manifest.add_input("job_input", _sha256_file(input_path) if input_path.is_file()
                       else "directory", path=str(input_path))

    if job_id:
        store.update_job(job_id, state=JobState.DELIVERY.value)

    # 3. run (fixed argv, no shell)
    interpreter = _resolve_interpreter(spec)
    if spec.id == "pdf-to-excel":
        argv = [interpreter, spec.entry, "-i", str(work_input), "-o", str(out_dir)]
    elif spec.id == "excel-cleaner":
        argv = [interpreter, spec.entry, str(work_input), str(out_dir)]
    else:
        return AdapterResult(False, spec.id, job_id, sandbox, -1,
                             error=f"no adapter implemented for service {spec.id}",
                             run_id=run_id)
    if spec.id == "pdf-to-excel" and extra_args:
        argv.extend(extra_args)

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=spec.working_directory,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=spec.timeout_s,
        )
    except subprocess.TimeoutExpired:
        manifest.finish("failed", error="timeout")
        _write_manifest(manifest, store, run_id)
        return AdapterResult(False, spec.id, job_id, sandbox, -1,
                             error=f"service timed out after {spec.timeout_s}s",
                             run_id=run_id)
    except OSError as exc:
        manifest.finish("failed", error=str(exc))
        _write_manifest(manifest, store, run_id)
        return AdapterResult(False, spec.id, job_id, sandbox, -1,
                             error=f"failed to start service: {exc}", run_id=run_id)
    elapsed = time.monotonic() - t0
    manifest.add_step("service_run", elapsed_s=elapsed,
                      detail={"exit_code": proc.returncode,
                              "argv": argv[:3] + ["..."],  # keep manifest small
                              "stdout_tail": redact(proc.stdout[-500:]),
                              "stderr_tail": redact(proc.stderr[-500:])})

    # 4/5. exit-code check
    if proc.returncode != spec.validation_exit_code:
        manifest.finish("failed", error=f"exit code {proc.returncode}")
        _write_manifest(manifest, store, run_id)
        if job_id:
            _fail_job(store, job_id, f"exit code {proc.returncode}")
        return AdapterResult(False, spec.id, job_id, sandbox, proc.returncode,
                             error=f"service exited {proc.returncode}: "
                                   f"{proc.stderr[-300:] or proc.stdout[-300:]}",
                             run_id=run_id)

    # 6. JSON report validation
    report_path = out_dir / spec.validation_report
    summary: dict[str, Any] = {}
    if spec.validation_report:
        if not report_path.exists():
            msg = f"missing machine-readable report: {spec.validation_report}"
            manifest.finish("failed", error=msg)
            _write_manifest(manifest, store, run_id)
            if job_id:
                _fail_job(store, job_id, msg)
            return AdapterResult(False, spec.id, job_id, sandbox, proc.returncode,
                                 error=msg, run_id=run_id)
        try:
            summary = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            msg = f"report {spec.validation_report} is not valid JSON: {exc}"
            manifest.finish("failed", error=msg)
            _write_manifest(manifest, store, run_id)
            if job_id:
                _fail_job(store, job_id, msg)
            return AdapterResult(False, spec.id, job_id, sandbox, proc.returncode,
                                 error=msg, run_id=run_id)

    if spec.id == "excel-cleaner":
        totals = summary.get("totals") or {}
        errors = totals.get("errors", summary.get("errors"))
        if errors is None or int(errors) != 0:
            msg = (f"excel-cleaner report_check failed: errors={errors!r} "
                   f"(quarantined files still exit 0; report is authoritative)")
            manifest.finish("failed", error=msg)
            _write_manifest(manifest, store, run_id)
            if job_id:
                _fail_job(store, job_id, msg)
            return AdapterResult(False, spec.id, job_id, sandbox, proc.returncode,
                                 error=msg, run_id=run_id)

    if spec.id == "pdf-to-excel":
        results = summary.get("results") or summary.get("files") or []
        if isinstance(results, list):
            fatal = [r for r in results
                     if isinstance(r, dict) and r.get("error") not in (None, "")]
            if fatal:
                msg = f"pdf-to-excel report_check failed: {len(fatal)} fatal file error(s)"
                manifest.finish("failed", error=msg)
                _write_manifest(manifest, store, run_id)
                if job_id:
                    _fail_job(store, job_id, msg)
                return AdapterResult(False, spec.id, job_id, sandbox, proc.returncode,
                                     error=msg, run_id=run_id)

    # 7. hash outputs
    artifacts = [
        {"path": str(p.relative_to(sandbox)), "sha256": _sha256_file(p)}
        for p in sorted(out_dir.rglob("*")) if p.is_file()
    ]

    # 8. manifest
    manifest.finish("ok", outcome={"artifacts": len(artifacts), "summary": _slim(summary)})
    manifest_path = _write_manifest(manifest, store, run_id)

    # 9. normalized result + job transitions
    if job_id:
        store.update_job(job_id, state=JobState.DELIVERED.value, sandbox_dir=str(sandbox),
                         delivery_date=datetime.date.today().isoformat())
        store.metric(name="job_delivered", value=1, unit="count", job_id=job_id)

    return AdapterResult(True, spec.id, job_id, sandbox, proc.returncode,
                         artifacts=artifacts, summary=summary, run_id=run_id)


def preflight(spec: ServiceSpec) -> tuple[bool, str]:
    """Cheap environment check: --self-test when the contract defines one."""
    if spec.preflight in (None, "fixture-smoke"):
        # excel-cleaner has no self-test; existence of interpreter+entry is the check
        interpreter = _resolve_interpreter(spec)
        if spec.preflight == "fixture-smoke":
            ok = Path(spec.interpreter).exists() and Path(
                spec.working_directory, spec.entry).exists()
            return ok, "interpreter+entry present" if ok else "interpreter or entry missing"
        return True, "no preflight defined"
    argv = [_resolve_interpreter(spec), spec.entry, spec.preflight]
    try:
        proc = subprocess.run(argv, cwd=spec.working_directory, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"preflight failed to run: {exc}"
    return proc.returncode == 0, (proc.stdout or proc.stderr)[-300:]


def _fail_job(store: Store, job_id: int, reason: str) -> None:
    store.update_job(job_id, state=JobState.FAILED.value)
    store.audit_standalone(actor="system", action="job.fail",
                           entity_type="job", entity_id=job_id,
                           payload={"reason": reason})


def _write_manifest(manifest: RunManifest, store: Store, run_id: str) -> Path:
    path = manifest.write(store.config.paths.runs_dir)
    store.save_run_manifest(run_id, kind=manifest.kind,
                            status=manifest.status, manifest_path=str(path))
    return path


def _slim(summary: dict[str, Any]) -> dict[str, Any]:
    keep: dict[str, Any] = {}
    for k in ("total_files", "total_tables", "elapsed_seconds", "duration_seconds"):
        if k in summary:
            keep[k] = summary[k]
    totals = summary.get("totals") or {}
    for k in ("processed_ok", "processed_invalid", "errors", "changes"):
        if k in totals:
            keep[k] = totals[k]
    for k in ("processed_ok", "processed_invalid", "errors", "total_changes"):
        if k in summary:
            keep[k] = summary[k]
    return keep
