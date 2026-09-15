## Summary

What and why.

## Checklist

- [ ] `uv run pytest tests/ -q` green (suite is fully offline)
- [ ] No test writes to real `data/` (fixtures / `BAM_ROOT` isolation intact)
- [ ] No new runtime dependency (httpx + pyyaml only), or justified in writing
- [ ] Service contracts untouched, or updated with docs + registry together
- [ ] Human approval gates and evidence rules preserved
- [ ] Secrets, runtime DBs, logs and personal data NOT included
- [ ] Docs updated when commands/behavior changed (README EN/ES, QUICKSTART)
