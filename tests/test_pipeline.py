"""End-to-end pipeline tests - 100% offline.

The fetcher and robots check are stubbed at the network boundary (the only
place where network could enter); everything downstream is real.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import pytest

from bam.config import Config
from bam.denylist import Denylist
from bam.fetcher import FetchError
from bam.pipeline import research
from bam.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "acme_homepage.html"


@pytest.fixture()
def online_fetch(monkeypatch: pytest.MonkeyPatch):
    """Serve the local fixture for any guarded fetch of acme-example.test."""
    raw = FIXTURE.read_bytes()

    class FakeResponse:
        status_code = 200
        content = raw

        def __init__(self) -> None:
            self.url = "https://acme-example.test/"

    class FakeFetcher:
        def __init__(self, *_a, **_k) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a) -> None:
            pass

        def fetch(self, url: str):
            host = urlparse(url).hostname or ""
            if host.endswith("acme-example.test"):
                return type("R", (), {
                    "url": url, "final_url": "https://acme-example.test/",
                    "status": 200, "content": raw,
                    "sha256": __import__("hashlib").sha256(raw).hexdigest(),
                    "elapsed_s": 0.01, "hops": 0,
                })()
            raise FetchError(f"no fixture for {url!r}")

    import bam.pipeline as pl

    monkeypatch.setattr(pl, "Fetcher", FakeFetcher)
    monkeypatch.setattr(pl, "_robots_allows", lambda *a, **k: True)
    return FakeFetcher


def test_research_happy_path(store: Store, config: Config, online_fetch) -> None:
    res = research("https://acme-example.test", store=store, config=config,
                   company_name="Acme Example")
    assert res.lead_id > 0
    assert res.state in ("researched", "approval_required", "disqualified")
    assert res.profile_path and res.profile_path.exists()
    assert res.report_path and res.report_path.exists()
    lead = store.get_lead(res.lead_id)
    assert lead.score is not None
    ev = store.evidence_for_lead(res.lead_id)
    assert len(ev) == 1
    assert ev[0]["status"] == "OBSERVED"
    claims = store.claims_for_lead(res.lead_id)
    assert claims, "fixture should produce at least one claim"
    # every claim is grounded: OBSERVED from extractors or INFERRED from LLM (off here)
    assert all(c["status"] in ("OBSERVED", "UNKNOWN") for c in claims)


def test_research_persists_manifest(store: Store, config: Config, online_fetch) -> None:
    res = research("https://acme-example.test", store=store, config=config)
    from bam.manifest import atomic_write_json  # noqa: F401

    manifests = list(config.paths.runs_dir.glob("run_*.json"))
    assert manifests, "run manifest must be written"
    import json

    data = json.loads(manifests[0].read_text(encoding="utf-8"))
    steps = [s["step"] for s in data["steps"]]
    assert {"validate_url", "denylist", "robots_check", "fetch", "extract",
            "evidence", "score"} <= set(steps)


def test_research_blocked_by_denylist(store: Store, config: Config,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Denylist, "load", classmethod(
        lambda cls, *a, **k: cls({"blocked_domain": ["blocked.test"]})))
    res = research("https://blocked.test", store=store, config=config)
    assert res.state == "blocked"
    assert "BLOCKED" in (res.decision or "") or "blocked" in (res.decision or "")
    lead = store.get_lead(res.lead_id)
    assert lead.state == "blocked"


def test_research_blocked_by_robots(store: Store, config: Config,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    import bam.pipeline as pl

    monkeypatch.setattr(pl, "_robots_allows", lambda *a, **k: False)
    res = research("https://acme-example.test", store=store, config=config)
    assert res.decision == "failed"
    assert "robots" in (res.llm_degraded_reason or "")


def test_research_non_200_fails_cleanly(store: Store, config: Config,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    import bam.pipeline as pl

    class F:
        def __init__(self, *a, **k) -> None: ...
        def __enter__(self):
            return self

        def __exit__(self, *a) -> None: ...

        def fetch(self, url: str):
            return type("R", (), {
                "url": url, "final_url": url, "status": 404,
                "content": b"nope", "sha256": "0" * 64, "elapsed_s": 0.01, "hops": 0,
            })()

    monkeypatch.setattr(pl, "Fetcher", F)
    monkeypatch.setattr(pl, "_robots_allows", lambda *a, **k: True)
    res = research("https://acme-example.test", store=store, config=config)
    assert res.decision == "failed"
    assert res.state == "blocked"


def test_report_is_honest(store: Store, config: Config, online_fetch) -> None:
    res = research("https://acme-example.test", store=store, config=config)
    text = res.report_path.read_text(encoding="utf-8")
    assert "Confidence:" in text
    assert "Observed evidence:" in text
    assert "not a conversion probability" in text
    assert "SHA-256" in text or "sha256" in text.lower()
