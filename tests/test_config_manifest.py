"""Config + manifest tests."""

from __future__ import annotations

import json

from bam.config import load_config, load_weights
from bam.manifest import RunManifest, atomic_write_json, new_run_id


def test_load_config_defaults() -> None:
    cfg = load_config()
    assert cfg.fetch.max_pages == 5
    assert cfg.llm.enabled is False          # V1: zero LLM calls by default
    assert cfg.llm.max_calls == 1
    assert cfg.fetch.max_requests_per_domain == 8


def test_weights_sum_to_one() -> None:
    w = load_weights()
    assert abs(sum(w.dimensions.values()) - 1.0) < 1e-6
    assert w.capped_score == 40


def test_run_manifest_roundtrip(tmp_path) -> None:
    m = RunManifest(run_id=new_run_id(), kind="test")
    m.add_input("page", "ab" * 32, url="https://x.test")
    m.add_step("step1", elapsed_s=0.5)
    m.finish("ok", outcome={"lead_id": 1})
    path = m.write(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "ok"
    assert data["inputs"][0]["sha256"] == "ab" * 32
    assert data["bam_version"]


def test_atomic_write_leaves_no_tmp(tmp_path) -> None:
    p = tmp_path / "x.json"
    atomic_write_json(p, {"a": 1})
    assert p.exists()
    assert not p.with_suffix(".json.tmp").exists()
