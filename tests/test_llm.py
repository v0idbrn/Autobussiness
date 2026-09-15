"""LLM adapter tests: every failure path degrades to deterministic mode."""

from __future__ import annotations

import json
from typing import Any

import pytest

from bam.config import LLMConfig
from bam.llm import LLMSchemaError, build_digest, synthesize


def _cfg(**over: Any) -> LLMConfig:
    base = dict(enabled=True, provider="openai-compatible", model="test-model",
                base_url="http://localhost:9", api_key_env="BAM_LLM_API_KEY",
                max_calls=1, max_input_tokens=4000, max_output_tokens=1200,
                max_cost_per_lead=0.10, provider_timeout_s=30)
    base.update(over)
    return LLMConfig(**base)


def _good_json(_messages, _model, _url, _timeout, _max_out):
    payload = {
        "detected_services": ["data-cleaning"],
        "observable_problems": ["manual-excel-work"],
        "reasoning_summary": "Homepage mentions spreadsheet cleaning services.",
        "unknowns": ["pricing unknown"],
        "risks": [],
    }
    return json.dumps(payload), {"input_tokens": 100, "output_tokens": 50}


def test_disabled_by_default_means_no_calls() -> None:
    res = synthesize({"signals": {}}, _cfg(enabled=False))
    assert res.ok is False
    assert res.degraded_reason == "llm_disabled"
    assert res.calls_made == 0


def test_no_api_key_degrades() -> None:
    monkey = pytest.MonkeyPatch()
    monkey.delenv("BAM_LLM_API_KEY", raising=False)
    res = synthesize({"signals": {}}, _cfg())
    assert res.ok is False
    assert res.degraded_reason == "llm_unavailable"


def test_provider_error_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAM_LLM_API_KEY", "k")

    def boom(*a, **kw):
        raise RuntimeError("connection refused")

    res = synthesize({"signals": {}}, _cfg(), call_fn=boom)
    assert res.ok is False
    assert res.degraded_reason.startswith("llm_provider_error")


def test_valid_response_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAM_LLM_API_KEY", "k")
    res = synthesize({"signals": {"title": "t"}}, _cfg(), call_fn=_good_json)
    assert res.ok is True
    assert res.data["detected_services"] == ["data-cleaning"]
    assert res.usage["calls"] == 1


def test_garbage_then_good_succeeds_on_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAM_LLM_API_KEY", "k")
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return "not json at all", {}
        return _good_json(*a, **kw)

    res = synthesize({"signals": {}}, _cfg(), call_fn=flaky)
    assert res.ok is True
    assert calls["n"] == 2  # exactly one retry


def test_garbage_twice_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAM_LLM_API_KEY", "k")

    def bad(*a, **kw):
        return "garbage", {"input_tokens": 10, "output_tokens": 5}

    res = synthesize({"signals": {}}, _cfg(), call_fn=bad)
    assert res.ok is False
    assert res.degraded_reason.startswith("llm_schema")


def test_out_of_enum_value_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAM_LLM_API_KEY", "k")

    def bad_enum(*a, **kw):
        payload = {"detected_services": ["nuclear-launch"],
                   "observable_problems": [], "reasoning_summary": "x",
                   "unknowns": [], "risks": []}
        return json.dumps(payload), {}

    res = synthesize({"signals": {}}, _cfg(), call_fn=bad_enum)
    assert res.ok is False
    assert res.degraded_reason.startswith("llm_schema")


def test_cost_budget_stops_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAM_LLM_API_KEY", "k")

    def expensive(*a, **kw):
        return _good_json(*a, **kw)[0], {"input_tokens": 900_000, "output_tokens": 900_000}

    res = synthesize({"signals": {}}, _cfg(max_cost_per_lead=0.01), call_fn=expensive)
    assert res.ok is False
    assert res.degraded_reason == "llm_budget_cost"


def test_schema_rejects_missing_keys() -> None:
    with pytest.raises(LLMSchemaError):
        import bam.llm as llm

        llm._validate({"detected_services": []})


def test_digest_is_compact_and_deterministic() -> None:
    profile = {"signals": {"title": "T", "technologies": ["a", "b"],
                           "keyword_services": ["data-cleaning"]}}
    d1 = build_digest(profile)
    d2 = build_digest(profile)
    assert d1 == d2
    assert len(d1) <= 3500
    assert "data-cleaning" in d1
