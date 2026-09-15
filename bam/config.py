"""Configuration loading for BAM.

All runtime knobs live in config/config.yaml; scoring weights in
config/weights.yaml. Nothing is hardcoded truth: the LLM cost limit and
fetch caps are deliberately configurable (plan v3.1 §8, §4).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_ROOT = Path(__file__).resolve().parent.parent


def project_root() -> Path:
    """BAM_ROOT env override (tests/isolation), else the repo root."""
    env = os.environ.get("BAM_ROOT")
    return Path(env) if env else DEFAULT_ROOT


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


@dataclass(frozen=True)
class FetchLimits:
    max_pages: int
    max_bytes_per_page: int
    max_redirects: int
    timeout_s: float
    max_requests_per_domain: int
    max_run_seconds: int
    min_interval_per_domain_s: float
    user_agent: str


@dataclass(frozen=True)
class LLMConfig:
    enabled: bool
    provider: str
    model: str
    base_url: str
    api_key_env: str
    max_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_cost_per_lead: float
    provider_timeout_s: int


@dataclass(frozen=True)
class Paths:
    database: Path
    evidence_dir: Path
    jobs_dir: Path
    runs_dir: Path


@dataclass(frozen=True)
class Config:
    fetch: FetchLimits
    llm: LLMConfig
    paths: Paths
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


@dataclass(frozen=True)
class Weights:
    dimensions: dict[str, float]
    auto_queue_min: int
    review_min: int
    min_observed_for_full_score: int
    capped_score: int
    high_min_observed: int
    medium_min_observed: int


def load_config(root: Path | None = None) -> Config:
    root = root or project_root()
    cfg = _load_yaml(root / "config" / "config.yaml")
    fetch = cfg.get("fetch", {})
    llm = cfg.get("llm", {})
    paths = cfg.get("paths", {})
    db_path = Path(paths.get("database", cfg.get("database", {}).get("path", "data/bam.db")))
    if not db_path.is_absolute():
        db_path = root / db_path
    return Config(
        fetch=FetchLimits(
            max_pages=int(fetch.get("max_pages", 1)),
            max_bytes_per_page=int(fetch.get("max_bytes_per_page", 2 * 1024 * 1024)),
            max_redirects=int(fetch.get("max_redirects", 5)),
            timeout_s=float(fetch.get("timeout_s", 15)),
            max_requests_per_domain=int(fetch.get("max_requests_per_domain", 3)),
            max_run_seconds=int(fetch.get("max_run_seconds", 60)),
            min_interval_per_domain_s=float(fetch.get("min_interval_per_domain_s", 1.0)),
            user_agent=str(fetch.get("user_agent", "BAM-ResearchBot/0.1")),
        ),
        llm=LLMConfig(
            enabled=bool(llm.get("enabled", False)),
            provider=str(llm.get("provider", "none")),
            model=str(llm.get("model", "")),
            base_url=str(llm.get("base_url", "")),
            api_key_env=str(llm.get("api_key_env", "BAM_LLM_API_KEY")),
            max_calls=int(llm.get("max_calls", 1)),
            max_input_tokens=int(llm.get("max_input_tokens", 4000)),
            max_output_tokens=int(llm.get("max_output_tokens", 1200)),
            max_cost_per_lead=float(llm.get("max_cost_per_lead", 0.10)),
            provider_timeout_s=int(llm.get("provider_timeout_s", 30)),
        ),
        paths=Paths(
            database=db_path,
            evidence_dir=root / paths.get("evidence_dir", "data/leads"),
            jobs_dir=root / paths.get("jobs_dir", "data/jobs"),
            runs_dir=root / paths.get("runs_dir", "data/runs"),
        ),
        raw=cfg,
    )


def load_weights(root: Path | None = None) -> Weights:
    root = root or project_root()
    w = _load_yaml(root / "config" / "weights.yaml")
    dims = w.get("dimensions", {})
    thresholds = w.get("thresholds", {})
    caps = w.get("caps", {})
    conf = w.get("confidence", {})
    total = sum(float(v) for v in dims.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"scoring weights must sum to 1.0 (got {total})")
    return Weights(
        dimensions={k: float(v) for k, v in dims.items()},
        auto_queue_min=int(thresholds.get("auto_queue_min", 70)),
        review_min=int(thresholds.get("review_min", 50)),
        min_observed_for_full_score=int(caps.get("min_observed_for_full_score", 2)),
        capped_score=int(caps.get("capped_score", 40)),
        high_min_observed=int(conf.get("high_min_observed", 5)),
        medium_min_observed=int(conf.get("medium_min_observed", 2)),
    )


def load_denylist_yaml(root: Path | None = None) -> dict[str, list[str]]:
    return _load_yaml((root or project_root()) / "data" / "denylist.yaml")
