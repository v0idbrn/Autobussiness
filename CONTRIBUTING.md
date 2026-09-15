# Contributing to BAM

Thanks for considering a contribution. BAM has a small, opinionated design;
these rules keep it safe and boring on purpose.

## Setup

```bash
git clone https://github.com/v0idbrn/Autobussiness.git
cd Autobussiness
uv sync          # runtime: httpx + pyyaml; dev: pytest
uv run pytest tests/ -q
```

## Non-negotiables

1. **Tests stay offline and deterministic.** No test may hit the network,
   Google News, whois.com or any external service. Stub at the network
   boundary (see `tests/test_pipeline.py`).
2. **Tests never touch production data.** Use the `store`/`config` fixtures
   (tmp paths) or the `BAM_ROOT` env var. A test that writes to `data/bam.db`
   is a bug.
3. **Service contracts are frozen.** `docs/service-contracts.md` and
   `bam/adapters.py` define versioned subprocess contracts. Changing a
   contract requires updating the contract doc, the registry version and the
   adapter together.
4. **The human gates stay.** No change may introduce automatic outreach,
   automatic external actions, or a path around `record_approval()`.
5. **Evidence rules stay.** The LLM never promotes UNKNOWN → OBSERVED;
   `< 2 observed ⇒ score ≤ 40`; empty evidence ⇒ DO NOT QUALIFY.
6. **Runtime deps stay minimal.** `httpx` + `pyyaml` only. Justify any new
   dependency in the PR description.

## Style

- Python 3.14, stdlib-first, `pathlib` everywhere, type hints on public
  functions.
- Boring, explicit code over clever abstractions.
- Errors must be honest: a failed operation must fail loudly (no `except:
  pass` around real failures).

## Commits & PRs

- Keep commits cohesive; one logical change per commit.
- Describe **why**, not just what.
- Before opening a PR: full suite green (`uv run pytest tests/ -q`),
  `uv run bam doctor` healthy, no new files under `data/` staged.
- Update docs (`README.md` **and** `README.es.md`, `QUICKSTART*`) when
  behavior or commands change.

## Security

Found a vulnerability? See [SECURITY.md](SECURITY.md) — do not open a public
issue.
