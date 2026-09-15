# BAM — Business Automation Machine

English | [Español](README.es.md)

**BAM** is a local-first operating system for a one-person tech business: it
finds companies, researches them with verifiable evidence, qualifies
opportunities with deterministic scoring, and drives the full commercial cycle
from outreach draft to delivery and payment — with **a human approving every
sensitive step**.

- **Local-first**: leads, evidence and money data live in a local SQLite
  database; no cloud, no accounts, no telemetry.
- **Evidence beats score**: every claim is OBSERVED (directly captured,
  SHA-256-hashed), INFERRED or UNKNOWN — and a score can never outrun its
  evidence.
- **No automatic outreach, ever**: BAM generates drafts; a human approves,
  sends and records outcomes.

## What it does

```text
DISCOVER  →  RESEARCH  →  QUALIFY  →  SALES BRIEF  →  DRAFT OUTREACH
   →  HUMAN APPROVAL  →  CONTACT  →  RESPONSE  →  QUOTE
   →  JOB  →  DELIVERY  →  PAYMENT
```

- **Discovery**: real companies from Google News RSS queries, manual URL
  lists, CSV files or free text — denylist-filtered, rate-limited.
- **Research**: guarded multi-page fetch (homepage + about/services/team/
  contact) with SSRF defenses, robots.txt awareness, per-domain request caps,
  byte/size/time budgets. Deterministic extraction (JSON-LD, meta, tech,
  closed-enum keywords), contact discovery (emails, phones, LinkedIn URLs,
  WHOIS fallback), SHA-256 evidence per page.
- **Qualification**: 8-dimension deterministic scoring (YAML weights). Fewer
  than 2 observed evidences ⇒ score capped ≤ 40; empty evidence ⇒ DO NOT
  QUALIFY. OBSERVED beats INFERRED; the optional LLM can never promote
  UNKNOWN → OBSERVED.
- **Sales workflow**: brief, outreach drafts (screen, `.eml` file, mailto —
  never sent), response classification, quotes, automatic follow-ups at
  human-gated transitions.
- **Delivery**: subprocess adapters run the existing local services and verify
  machine-readable reports before accepting a delivery (an exit code alone is
  never trusted).

## What it does NOT do

- It does **not** send emails, messages or connection requests.
- It does **not** scrape aggressively, bypass CAPTCHAs or log into platforms.
- It does **not** run a server or sync to the cloud.
- It does **not** invent data: no evidence, no observation — no claim.

## Available services

| Service | Status | What it does |
|---|---|---|
| `pdf-to-excel` | **available** | Local PDF → Excel/CSV extraction with per-file audit and SHA-256 manifests |
| `excel-cleaner` | **available** | Local Excel/CSV cleaning with change log, quarantine and batch audit summary |
| `qa-agent` | *disabled (Phase 2)* | QA automation agent — contract prepared, adapter pending its stable CLI |

Services are **not imported** — they run as subprocesses in their own
environments against frozen, versioned contracts (`docs/service-contracts.md`).

## Security model

- **Untrusted web content is data, never instructions.** Fetched pages feed
  deterministic extractors and a schema-validated, optional LLM digest.
- **SSRF defenses**: scheme allowlist, credentials-in-URL refusal, private/
  loopback/link-local/reserved IP refusal, IP-pinned connections (anti-DNS-
  rebinding), manually-guarded redirects, control-character rejection.
- **Money integrity**: amounts must be finite and non-negative; a payment
  requires a positive amount.
- **State machine**: 18 lead states; HUMAN transitions refuse generic code
  paths; every runtime write is audited.
- **Backups prove themselves**: every `bam backup` verifies integrity, row
  counts against the live DB, and performs a restore drill — an unproven
  backup is quarantined, never presented as valid. Retention: the last
  N verified backups are kept (`backup.keep` in `config.yaml`, default 5);
  older ones are pruned automatically after each verified backup.
- **Fail closed**: corrupted configs, corrupt backups and invalid states stop
  the operation instead of degrading silently.

## Installation

Requirements: **Windows 10/11**, Python **3.14+**, [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/v0idbrn/Autobussiness.git
cd Autobussiness
uv sync
uv run bam doctor
```

`bam doctor` checks config, database, dependencies and service preflights.

## Quick start

```bash
uv run bam discover --source rss --query "accounting firms" --limit 10
uv run bam research https://example-company.com
uv run bam leads
uv run bam next
```

The full commercial walkthrough is in [QUICKSTART.md](QUICKSTART.md)
([Español](QUICKSTART.es.md)).

## CLI examples

```bash
bam doctor                                   # environment check
bam discover --source rss --query "..."      # find companies (1 request/query)
bam research <url>                           # evidence-backed research
bam leads [--state approval_required]        # pipeline overview
bam next                                     # what to do next
bam sales-brief <id>                         # evidence-based brief
bam draft-outreach <id> [--file|--mailto]    # draft only - never sends
bam approve-contact <id> -y                  # HUMAN approval gate
bam contact <id> contacted                   # record an outcome you sent
bam quote <id> --files 10                    # quote suggestion
bam deliver <path> --service pdf-to-excel    # run a real service
bam pay <job> --amount 150                   # record real money only
bam digest                                   # metrics (no invented revenue)
bam backup                                   # self-verifying backup
bam services                                 # registry + preflight status
```

## Configuration

- `config/config.yaml` — fetch caps, LLM budget (disabled by default), paths.
- `config/weights.yaml` — scoring weights and thresholds.
- `config/service-registry.yaml` — service ids, frozen contract versions,
  preflight commands.
- `data/denylist.yaml` — domains/companies BAM must never research or contact.
- `BAM_ROOT` env var relocates the whole tree (used by tests; also how the
  EXE resolves its folder).

## Directory structure

```text
bam/            package: cli, store, pipeline, fetcher, evidence, extractors,
                scorer, llm, commercial, contacts, discovery, reporting,
                adapters, router, manifest, denylist, approvals, config
config/         YAML configuration (committed)
data/           runtime data (gitignored) + denylist policy (committed)
docs/           frozen service contracts
tests/          offline pytest suite (188 tests, no network)
backups/        self-verifying DB backups (gitignored)
```

## Testing

```bash
uv run pytest tests/ -q      # 188 tests, fully offline and deterministic
```

The suite never touches the network, production data or external services.

## Build (Windows EXE)

```bash
uv run pyinstaller bam.spec --noconfirm
```

The bundle is a **onedir** build in `dist/bam/`: `bam.exe` runs **without
Python installed**, keeps `config/` and `data/` next to the executable
(`BAM_ROOT` relocates them), and shows useful errors instead of crashing
silently. Build artifacts are reproducible via the committed `bam.spec`;
see `docs/BUILDING.md` for details. Note: some antivirus products flag
unsigned PyInstaller bundles — verify the hash before trusting a binary.

## Service adapter model

Each delivery runs the 9-step adapter protocol: sandbox → input copy →
subprocess run (fixed argv, no shell) → stdout/stderr capture → exit-code
check → machine-readable report validation → artifact hashing → run manifest →
normalized result. Excel Cleaner's exit-0-with-quarantined-files trap and
PDF→Excel's per-file reports are explicitly handled. Contract versions are
frozen: a registry mismatch refuses the run.

## Commercial workflow

See [QUICKSTART.md](QUICKSTART.md). Summary: research produces evidence and a
score; qualifying surfaces a lead at `approval_required`; `bam approve-contact`
records the human decision; drafts are delivered as files you send yourself;
outcomes, quotes, deliveries and payments are recorded as they really happen —
`bam digest` reports revenue only from recorded payments.

## Privacy

All data stays on your machine. Evidence excerpts are **redacted** (emails,
phones, API tokens) before storage; hashes and URLs identify sources. No data
leaves the machine except the HTTP requests you trigger yourself.

## Responsible use

BAM automates *research*, not persuasion. You are responsible for complying
with the laws and terms that apply to your outreach — see [LEGAL.md](LEGAL.md).
Discovery does not imply permission to contact; publicly listed contact data
is not consent. No bulk messaging.

## Limitations

- robots.txt that cannot be fetched fails open (hard caps still apply).
- Evidence stores a redacted excerpt + hash, not full HTML; re-verification
  means re-fetching.
- The optional LLM is disabled by default; INFERRED claims stay inactive.
- Discovery quality depends on Google News RSS availability.
- WHOIS extraction depends on whois.com's HTML layout (skips on failure).
- The QA Agent adapter awaits its Phase 2 CLI.

## Roadmap

- QA Agent adapter (Phase 2)
- bounded multi-query discovery batches
- CSV export for accounting handoff
- proposal-draft generation from approved profiles

## License

MIT — see [LICENSE](LICENSE). Third-party components are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). MIT covers this repository's
code, not third-party services, external data, or generated outputs.

## Author

Maintained by **v0idbrn**. Built with an evidence-first, human-gated
philosophy: the machine prepares, the human decides.
