"""Shared fixtures: isolated tmp config/store per test; no network anywhere."""

from __future__ import annotations

from pathlib import Path

import pytest

from bam.config import Config, FetchLimits, LLMConfig, Paths
from bam.store import Store


def make_config(tmp_path: Path) -> Config:
    return Config(
        fetch=FetchLimits(
            max_pages=1,
            max_bytes_per_page=2 * 1024 * 1024,
            max_redirects=5,
            timeout_s=15,
            max_requests_per_domain=3,
            max_run_seconds=60,
            min_interval_per_domain_s=0.0,
            user_agent="BAM-TestBot/0.1",
        ),
        llm=LLMConfig(
            enabled=False,
            provider="none",
            model="",
            base_url="",
            api_key_env="BAM_LLM_API_KEY",
            max_calls=1,
            max_input_tokens=4000,
            max_output_tokens=1200,
            max_cost_per_lead=0.10,
            provider_timeout_s=30,
        ),
        paths=Paths(
            database=tmp_path / "bam.db",
            evidence_dir=tmp_path / "leads",
            jobs_dir=tmp_path / "jobs",
            runs_dir=tmp_path / "runs",
        ),
        raw={},
    )


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    return make_config(tmp_path)


@pytest.fixture()
def store(config: Config) -> Store:
    s = Store(config)
    yield s
    s.close()
