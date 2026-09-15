# BAM — Frozen Service Contracts

> **Source of truth:** these contracts were captured by direct inspection of the real
> projects in `F:/Gigs` (READMEs, `cli.py`, `main.py`, `batch_processor.py`, venv
> interpreters, `dist/` builds) on 2026-09-14. BAM integrates via **subprocess only**
> and must never import service internals. If a service's CLI changes, update the
> contract version here and in `service-registry.yaml`, then update the adapter.

---

## `pdf-to-excel@1`

| Field | Value |
|---|---|
| Project path | `F:/Gigs/Excel to PDF` *(folder is misnamed; project is PDF→Excel Engine)* |
| Purpose | Deterministic, zero-cloud extraction of tables from vector PDFs into XLSX/CSV with a Zero-Trust audit trail |
| CLI | `python cli.py -i <PDF_OR_DIR...> -o <OUT_DIR> [--csv] [--no-hints] [--day-first] [--null-token ""] [--audit-xlsx]` |
| Preflight | `python cli.py --self-test` → exit `0` = environment OK |
| Machine-readable outputs | `<OUT_DIR>/report.json` (global batch), `<OUT_DIR>/<name>.audit.json` (per file: SHA-256, class, pages, tables, issues, validation, outputs) |
| Human-readable outputs | `<name>.xlsx`, optional `<name>.csv`, console table |
| Exit codes | `0` = all files OK or cleanly skipped (Class B) · `1` = one or more fatal file errors, or invalid arguments |
| Document classes | A vector tables (full) · B no tables (clean skip) · C scanned (rejected) · D complex (best effort) · E encrypted (rejected) |
| Validation mechanism | Adapter validates: exit code `0` **and** `report.json` parses **and** per-file results contain no fatal errors |
| Idempotency | Yes (deterministic; source files read-only) |
| Interpreter | Own venv absent → invoke with a Python ≥3.14 interpreter; `dist/PDF_to_Excel_Engine/PDF_to_Excel_Engine.exe` is the packaged fallback |
| Integration difficulty | LOW (best contract of the three) |

---

## `excel-cleaner@1`

| Field | Value |
|---|---|
| Project path | `F:/Gigs/Excel Cleaner` |
| Purpose | Deterministic Excel/CSV cleaning with zero-trust validation and full audit deliverables |
| CLI | `python batch_processor.py <INPUT_DIR> <OUTPUT_DIR> [--zip]` |
| Preflight | **No `--self-test`** → adapter must smoke-test with a small fixture file (one clean CSV through the batch) |
| Machine-readable outputs | `<OUT_DIR>/batch_audit_summary.json` (fields include `processed_ok`, `processed_invalid`, `errors`, `total_changes`, `output_dir`) |
| Human-readable outputs | cleaned files, per-file audit `.txt/.json/.html`, embedded `_Reporte_Auditoria` sheet, `batch_audit_summary.txt`, optional ZIP |
| Exit codes | `0` = run finished · `1` = ≥1 processing error · `2` = usage error / `BatchError` |
| ⚠️ Critical nuance | Files moved to the `errors/` quarantine do **not** count as errors and still exit `0`. The adapter MUST validate `batch_audit_summary.json` (`errors == 0` required for success) and must never trust the exit code alone |
| Validation mechanism | Zero-trust mathematical barrier inside the engine; zero-write export when validation fails |
| Idempotency | Yes (deterministic; inputs read-only) |
| Interpreter | `F:/Gigs/Excel Cleaner/.venv/Scripts/python.exe` (Python 3.14.3, pandas 3.0.5 pinned) |
| Integration difficulty | MEDIUM (JSON summary verification required) |

---

## `qa-agent@0` (provisional — Phase 2)

| Field | Value |
|---|---|
| Project path | `F:/Gigs/QA_Automation_Agent` |
| Purpose | Bounded autonomous QA audit cycle (discovery → planning → execution → evidence → findings → quality gate → reports) |
| CLI | `python main.py discover\|plan\|run\|report\|validate\|install-browsers <target> [--profile safe\|standard\|deep\|ci] [--headless] [--output DIR] [--budget k=v,...] [--url URL]` |
| Machine-readable outputs | `<OUT>/audit_report.json`, `<OUT>/run_manifest.json`, `<OUT>/run_evidence.json` |
| Exit codes | `0` = PASS / PASS_WITH_WARNINGS · `1` = gate FAIL / interrupted · `2` = escalation (NEEDS_HUMAN) / config error · `130` = SIGINT |
| Status | Contract **provisional**: the agent is still evolving. BAM must adapt to its stable contract when Phase 2 arrives — never modify QA Agent for BAM's convenience. Adapter deferred (roadmap step 10) |
| Integration difficulty | LOW–MEDIUM |

---

## Integration rules (binding for all adapters)

1. Subprocess only; fixed argv; no shell; no imports from service packages.
2. Sandbox directory per job under `data/jobs/<job_id>/`; inputs copied in, outputs hashed.
3. Validate exit code **and** the machine-readable report; on mismatch → job `failed` with structured reason.
4. Enforce `contract_version` from `service-registry.yaml`; refuse to run on mismatch.
5. Capture stdout/stderr (tail kept in the run manifest), enforce per-service timeout.
6. Every write BAM performs at runtime is recorded in `audit_log`.
