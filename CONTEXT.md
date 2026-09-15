# CONTEXT.md — BAM ubiquitous language

- **Lead** — a company under research, not a person. Lives in the pipeline
  from `discovered` until a terminal state.
- **Evidence** — captured proof of a claim: URL, SHA-256 of the bytes,
  timestamp, redacted excerpt. Status is `OBSERVED` (directly seen in fetched
  content), `INFERRED` (derived from observed evidence by deterministic rules
  or the schema-validated LLM synthesis), or `UNKNOWN` (absent).
- **Claim** — a statement about a company (`detected_service`,
  `detected_technology`, `observable_problem`). Always carries evidence status.
- **Finding** — a claim inside a report, with confidence and reasoning.
- **Score** — deterministic prioritization number (0–100) from 8 weighted
  dimensions. Not a conversion probability. Shown with confidence + evidence
  count + unknowns.
- **Qualification** — the automatic act of marking a lead research-complete and
  potentially contactable (`researched → qualified`). Distinct from approval.
- **Approval** — a recorded human decision (`approval_required → approved`,
  or the contact gate). A transaction: who, when, what, from-state, to-state,
  reason, run ID. Never implicit.
- **Contact gate** — the hard rule that `approved → contacted` is recorded by a
  human after a human performed the outreach manually.
- **Service** — an external capability invoked by subprocess
  (`pdf-to-excel`, `excel-cleaner`, later `qa-agent`), described by a frozen,
  versioned contract.
- **Service Registry** — declarative YAML of services: id, purpose, input,
  output, command, working directory, environment, validation, risk, enabled,
  contract version.
- **Adapter** — BAM-side subprocess wrapper implementing the 9 fixed steps
  (sandbox → input → run → capture → exit-code check → JSON validation →
  hashing → manifest → normalized result). Imports nothing from the service.
- **Job** — a unit of paid work routed to one Service. Has states
  (`intake → classified → assigned → delivery → delivered/failed`) and revenue
  fields.
- **Run** — one execution of any BAM pipeline step or job, recorded in a
  **RunManifest** (inputs hashed, commands, versions, timings, outcome,
  human vs automatic).
- **Audit log** — append-only record of every runtime write BAM performs
  (actor = human|agent|system).
- **Denylist** — policy lists (`do_not_research`, `do_not_contact`,
  `blocked_domain`, `blocked_company`) checked before any action.
- **Digest** — CLI business-metrics report: conversion, revenue, hours,
  savings. No dashboards in V1.
- **Deterministic mode** — pipeline state when the LLM is disabled, exhausted,
  or failed: profile completes from OBSERVED evidence only, INFERRED fields
  empty with `reason: llm_unavailable`.
