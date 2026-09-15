# BAM — Business Automation Machine

Local-first prospect → delivery operating system for a one-person tech business.

```
PROSPECT → OPPORTUNITY → APPROVAL → CLIENT → JOB → DELIVERY → PAYMENT → REPEAT
```

- **Local-first**: Python 3.14, SQLite (WAL), zero cloud for your data.
- **Evidence-first**: every claim is OBSERVED / INFERRED / UNKNOWN, with
  SHA-256-hashed evidence. Score never outranks evidence.
- **LLM-optional**: the pipeline completes with zero LLM calls. One optional
  budget-capped call adds synthesis; any failure degrades to deterministic mode.
- **Human-gated**: no outreach, publication, or payment without a recorded
  human approval. The state machine enforces it structurally.
- **Services by contract**: existing tools (PDF→Excel, Excel Cleaner, QA Agent)
  are invoked by subprocess against frozen, versioned contracts — never modified.

## Quick start

```bash
uv sync
uv run pytest                       # offline test suite
uv run bam doctor                   # environment + services check
uv run bam research https://example.com
uv run bam leads
uv run bam approve <lead-id> --reason "fit confirmed"
uv run bam deliver <path-to-pdf> --service pdf-to-excel
uv run bam digest
```

## Documentation

- `docs/service-contracts.md` — frozen subprocess contracts for the three services
- `AGENTS.md` — rules for humans and agents working on this repo
- `CONTEXT.md` — domain vocabulary
- `docs/adr/` — architecture decision records

## Status

Phase 1 implementation per the approved v3.1 plan. Runtime dependencies:
`httpx`, `pyyaml` only. `pytest` for development; `uv` as tooling.
