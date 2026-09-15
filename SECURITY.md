# Security Policy

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Use GitHub's private vulnerability reporting on this repository
(Security tab → Report a vulnerability). If that is unavailable, contact the
maintainer through a GitHub profile contact channel and ask for a private
channel before disclosing details.

Please include:

- the affected version/commit;
- the component (e.g. `fetcher`, `store`, `adapters`, CLI command);
- minimal steps to reproduce;
- the impact you observed;
- any proof-of-concept (keep it minimal and do not include real secrets or
  real personal data).

You will get an acknowledgment and a fix or a mitigation plan. Responsible
coordination: please allow a reasonable window before any public disclosure.

## Scope notes

- BAM is a **local** tool: there is no server, no account system and no
  telemetry. The meaningful attack surfaces are: untrusted web content
  (parsing, SSRF), subprocess adapters, the local SQLite store, and CLI input
  handling.
- Out of scope: vulnerabilities in the separately maintained service projects
  (PDF→Excel, Excel Cleaner, QA Agent) — report those in their own
  repositories; and social-engineering of the operator.

## Never do this

- Do not commit secrets, API keys, tokens or `.env` files.
- Do not include credentials in issues, PRs or logs.
- Do not point BAM at production systems of others while testing.

## Supported configuration

Security fixes land on `master` and are published as new commits; there are no
release branches. Run from a fresh `git pull` + `uv sync`, or a freshly built
EXE from the committed `bam.spec`.
