"""Discovery V2 tests: deterministic commercial signals, publisher filtering,
directory discovery, and the optional minimal Gemini opinion layer.

All offline: pure functions, monkeypatched fetchers, injected Gemini call_fn.
"""

from __future__ import annotations

import dataclasses
import json
from urllib.parse import quote_plus

import pytest

import bam.discovery as disc
import bam.gemini as gem
import bam.signals as sig
from bam.signals import (
    AMBIGUOUS_HIGH,
    aggregate_commercial,
    classify_candidate,
    extract_page_signals,
    query_catalog,
    score_contactability,
    should_ask_gemini,
)

# ---------------------------------------------------------------- query catalog


def test_query_catalog_small_and_measurable() -> None:
    for svc, queries in query_catalog().items():
        assert 0 < len(queries) <= 6, f"{svc}: keep the catalog small"
        assert all(q.strip() for q in queries)


# ------------------------------------------------------------------ classifiers


@pytest.mark.parametrize("host,kind", [
    ("news.example.com", "publisher"),
    ("times-of-billing.com", "publisher"),
    ("forbes.com", "aggregator"),
    ("finance.yahoo.com", "aggregator"),
    ("linkedin.com", "social"),
    ("businesswire.com", "aggregator"),
    ("yelp.com", "directory"),
    ("acme-data-services.com", "company"),
    ("brightbookkeeping.co.uk", "company"),
])
def test_classify_candidate_hosts(host: str, kind: str) -> None:
    assert classify_candidate(host).kind == kind


def test_classify_title_pattern_catches_generic_publishers() -> None:
    ct = classify_candidate("unknown-shop.net",
                            title="Market Research Report 2026 - Global Forecast")
    assert ct.kind == "publisher"


def test_classify_company_title_stays_company() -> None:
    ct = classify_candidate("acme-data-services.com",
                            title="Acme Data Services - Invoice Processing")
    assert ct.kind == "company"


# ------------------------------------------------------------ weighted signals


def test_extract_page_signals_weighted_strong_vs_medium() -> None:
    strong = extract_page_signals(
        "<html><body>We provide invoice processing and accounts payable "
        "automation for mid-market firms.</body></html>",
        url="https://a.test/", is_homepage=True)
    assert strong.scores["pdf_to_excel"] >= 2 * sig.STRONG_WEIGHT
    assert strong.strong_hit

    weak = extract_page_signals(
        "<html><body>We mention OCR once.</body></html>",
        url="https://b.test/", is_homepage=True)
    assert weak.scores["pdf_to_excel"] == sig.MEDIUM_WEIGHT
    assert not weak.strong_hit


def test_aggregate_homepage_strong_outranks_subpage_only() -> None:
    home = extract_page_signals("<html><body>document processing services</body></html>",
                                url="https://a.test/", is_homepage=True)
    sub = extract_page_signals("<html><body>test automation services</body></html>",
                               url="https://a.test/services", is_homepage=False)
    cp = aggregate_commercial("a.test", [home, sub])
    assert cp.homepage_strong
    assert cp.recommended_service == "pdf_to_excel"
    # homepage strong evidence adds bonus weight over subpage-only signals
    assert cp.service_scores["pdf_to_excel"] > cp.service_scores["qa_automation"]


def test_aggregate_ambiguity_band() -> None:
    # medium-only single hit => score 1 (below band: not ambiguous, but weak)
    home = extract_page_signals("<html><body>We mention OCR.</body></html>",
                                url="https://a.test/", is_homepage=True)
    cp = aggregate_commercial("a.test", [home])
    assert cp.total_score == sig.MEDIUM_WEIGHT
    assert not cp.ambiguous

    # exactly one strong hit => base 3 (bonus lifts total to 6) => ambiguous
    home2 = extract_page_signals("<html><body>data entry services provider</body></html>",
                                 url="https://b.test/", is_homepage=True)
    cp2 = aggregate_commercial("b.test", [home2])
    assert sig.AMBIGUOUS_LOW <= cp2.base_score <= AMBIGUOUS_HIGH
    assert cp2.ambiguous
    assert cp2.total_score == cp2.base_score + sig.STRONG_WEIGHT


def test_contactability_observed_only() -> None:
    high = score_contactability({"emails": ["info@acme.test"],
                                 "has_contact_form": True,
                                 "contact_url": "/contact", "mailto_count": 1})
    assert high["level"] == "high" and high["has_email"]
    none = score_contactability({})
    assert none["level"] == "low" and none["score"] == 0
    # never invents: missing keys stay False
    assert none["has_contact_page"] is False


# ------------------------------------------------------------ gemini gate (§7)


def test_should_ask_gemini_only_ambiguous_companies() -> None:
    home = extract_page_signals("<html><body>data entry services provider</body></html>",
                                url="https://a.test/", is_homepage=True)
    cp = aggregate_commercial("a.test", [home])
    assert cp.ambiguous and should_ask_gemini(cp)

    pub = aggregate_commercial("forbes.com", [home])
    assert not should_ask_gemini(pub)

    empty = aggregate_commercial("b.test", [])
    assert not should_ask_gemini(empty)


# ---------------------------------------------------------- directory discovery


_DIR_HTML = """
<html><body>
<a href="/about">About this directory</a>
<a href="https://acme-data.test/">Acme Data Services</a>
<a href="https://www.brightbooks.test/contact">Bright Books LLP</a>
<a href="https://forbes.com/some-article">News article</a>
<a href="https://linkedin.com/company/acme">Acme on LinkedIn</a>
<a href="https://news.example.test/story">Local News</a>
</body></html>
"""


class _DirFetcher:
    def __init__(self, html: str) -> None:
        self.html = html.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def fetch(self, url: str):
        return type("R", (), {
            "url": url, "final_url": url, "status": 200,
            "content": self.html, "sha256": "0" * 64,
            "elapsed_s": 0.01, "hops": 0,
        })()


def test_discover_from_directory_filters_publishers_and_social() -> None:
    out = disc.discover_from_directory(
        "https://directory.example.test/members",
        fetcher=_DirFetcher(_DIR_HTML))
    domains = [c.domain for c in out]
    assert "acme-data.test" in domains
    assert "brightbooks.test" in domains
    assert all(d not in domains for d in
               ("forbes.com", "linkedin.com", "news.example.test"))
    c = next(c for c in out if c.domain == "acme-data.test")
    assert c.source == "directory"
    assert "directory.example.test" in c.evidence


def test_discover_from_directory_failure_skips_cleanly() -> None:
    class Boom:
        def __init__(self, *_a, **_k) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def fetch(self, url: str):
            raise RuntimeError("down")

    assert disc.discover_from_directory(
        "https://directory.example.test", fetcher=Boom()) == []


# -------------------------------------------------------------- web search parse


def test_parse_web_search_offline() -> None:
    ddg = f"""
    <html><body>
    <a href="/l/?uddg={quote_plus('https://acme-data.test/')}">r1</a>
    <a href="/l/?uddg={quote_plus('https://www.brightbooks.test/')}">r2</a>
    <a href="/y.js?clid=1">Ad</a>
    <a href="/l/?uddg={quote_plus('https://acme-data.test/')}">dup</a>
    </body></html>
    """
    out = disc.parse_web_search(ddg, query="q", source_url="https://x", limit=8)
    domains = [c.domain for c in out]
    assert domains == ["acme-data.test", "brightbooks.test"]
    assert all(c.source == "web_search" for c in out)


# ------------------------------------------------------- commercial_candidates


def test_commercial_candidates_runs_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_queries: list[str] = []

    def fake_ws(query: str, *, limit: int = 8, fetcher=None):
        seen_queries.append(query)
        return [disc.DiscoveredCompany(
            name="C", domain=f"{len(seen_queries)}.test", url=f"https://{len(seen_queries)}.test",
            source="web_search", source_url=None, industry=None,
            evidence=f"found via web search: {query}")]

    def fake_rss(query: str, *, limit: int = 10, fetcher=None):
        return []

    monkeypatch.setattr(disc, "discover_from_web_search", fake_ws)
    monkeypatch.setattr(disc, "discover_from_rss", fake_rss)
    out = disc.commercial_candidates(["pdf_to_excel"], per_query=2)
    assert seen_queries == list(disc.QUERY_CATALOG["pdf_to_excel"])
    assert len(out) == len(seen_queries)
    assert all(c.industry == "pdf_to_excel" for c in out)


# --------------------------------------------------- pipeline integration (§3)


def test_pipeline_persists_commercial_signals(store, config, monkeypatch,
                                              tmp_path) -> None:
    import hashlib
    from pathlib import Path
    from urllib.parse import urlparse

    raw = (Path(__file__).parent / "fixtures" / "acme_homepage.html").read_bytes()

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
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "elapsed_s": 0.01, "hops": 0,
                })()
            raise RuntimeError(f"no fixture for {url!r}")

    import bam.pipeline as pl

    monkeypatch.setattr(pl, "Fetcher", FakeFetcher)
    monkeypatch.setattr(pl, "_robots_allows", lambda *a, **k: True)

    from bam.pipeline import research

    res = research("https://acme-example.test", store=store, config=config)
    profile = json.loads(res.profile_path.read_text(encoding="utf-8"))
    comm = profile["signals"].get("commercial")
    assert comm is not None
    assert set(comm) >= {"service_scores", "recommended_service",
                         "total_score", "candidate_type", "contactability"}
    assert comm["candidate_type"] == "company"
    manifests = list(config.paths.runs_dir.glob("run_*.json"))
    steps = [s["step"] for m in manifests for s in json.loads(
        m.read_text(encoding="utf-8"))["steps"]]
    assert "commercial_signals" in steps


# ---------------------------------------------------------------- gemini (§6-§10)


def _gem_cfg(**over):
    return gem.GeminiConfig(enabled=True, **over)


def _gem_config_raw(config, **raw_over):
    raw = {"gemini": {"enabled": False, **raw_over}}
    return dataclasses.replace(config, raw=raw)


_GOOD = json.dumps({"service_fit": "excel_cleaning",
                    "commercial_relevance": "high",
                    "reason": "digest shows data entry services language",
                    "uncertainties": ["pricing unknown"]})


def test_gemini_disabled_by_default(config, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = gem.load_gemini_config(config)  # raw has no gemini block
    assert cfg.enabled is False
    client = gem.GeminiClient(cfg)
    r = client.ask({"content_hash": "x"})
    assert not r.ok and r.reason == "gemini_disabled"


def test_gemini_missing_key_fails_closed(config, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = gem.load_gemini_config(
        _gem_config_raw(config, enabled=True))
    assert cfg.enabled is True  # config may say enabled...
    client = gem.GeminiClient(cfg)
    r = client.ask({"content_hash": "x"})
    assert not r.ok and r.reason == "gemini_no_api_key"  # ...but no key, no call


def test_gemini_key_alone_never_enables(config, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k-test")
    cfg = gem.load_gemini_config(config)
    assert cfg.enabled is False


def test_gemini_success_then_cache_hit(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k-test")
    calls = {"n": 0}

    def call_fn(prompt, cfg):
        calls["n"] += 1
        assert "k-test" not in prompt  # key never leaks into the prompt
        return _GOOD, {"input_tokens": 50, "output_tokens": 20}

    client = gem.GeminiClient(_gem_cfg(), call_fn=call_fn)
    d = {"content_hash": "abc", "phrases": ["data entry"]}
    r1 = client.ask(d)
    r2 = client.ask(d)
    assert r1.ok and not r1.cached
    assert r2.ok and r2.cached
    assert calls["n"] == 1 and client.cache_hits == 1
    assert r2.data["service_fit"] == "excel_cleaning"


def test_gemini_cache_keys_on_content_hash(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k-test")
    calls = {"n": 0}

    def call_fn(prompt, cfg):
        calls["n"] += 1
        return _GOOD, {}

    client = gem.GeminiClient(_gem_cfg(), call_fn=call_fn)
    client.ask({"content_hash": "a"})
    client.ask({"content_hash": "b"})  # different evidence => real call
    assert calls["n"] == 2


def test_gemini_run_budget(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k-test")
    client = gem.GeminiClient(_gem_cfg(max_calls_per_run=1), call_fn=lambda p, c: (_GOOD, {}))
    r1 = client.ask({"content_hash": "a"})
    r2 = client.ask({"content_hash": "b"})
    assert r1.ok and not r2.ok and r2.reason == "gemini_budget_exhausted"


def test_gemini_malformed_json(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k-test")
    client = gem.GeminiClient(_gem_cfg(), call_fn=lambda p, c: ("not json", {}))
    r = client.ask({"content_hash": "a"})
    assert not r.ok and r.reason.startswith("gemini_schema")
    assert client.failures == 1


def test_gemini_rejects_hallucinated_url_and_email(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k-test")
    bad = json.dumps({"service_fit": "other", "commercial_relevance": "low",
                      "reason": "visit https://evil.test or mail sales@evil.test",
                      "uncertainties": []})
    client = gem.GeminiClient(_gem_cfg(), call_fn=lambda p, c: (bad, {}))
    r = client.ask({"content_hash": "a"})
    assert not r.ok and "hallucinated" not in (r.data or {})
    assert r.reason and "URL/email" in r.reason


def test_gemini_timeout_fails_clean(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k-test")

    def boom(prompt, cfg):
        raise TimeoutError("timed out")

    client = gem.GeminiClient(_gem_cfg(), call_fn=boom)
    r = client.ask({"content_hash": "a"})
    assert not r.ok and r.reason.startswith("gemini_provider_error")


def test_gemini_prompt_is_compact_digest(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k-test")
    seen: dict[str, str] = {}

    def call_fn(prompt, cfg):
        seen["prompt"] = prompt
        return _GOOD, {}

    gem.GeminiClient(_gem_cfg(), call_fn=call_fn).ask({"content_hash": "a"})
    assert "DIGEST:" in seen["prompt"]
    assert "<html" not in seen["prompt"]


def test_gemini_digest_never_carries_raw_html() -> None:
    d = gem.build_gemini_digest(
        aggregate_commercial("a.test", []),
        {"title": "T", "meta_description": "D", "keyword_services": ["data-cleaning"]})
    assert d["content_hash"]
    assert "raw_html" not in d
