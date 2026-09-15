"""Delivery router: registry loading + rule-based job classification.

The registry (config/service-registry.yaml) mirrors docs/service-contracts.md.
Classification is rule-based (file extension + requested outcome); the LLM is
never involved in routing (plan v3.1 §12, §17).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bam.config import project_root


@dataclass(frozen=True)
class ServiceSpec:
    id: str
    contract_version: int
    enabled: bool
    risk: str
    purpose: str
    input_desc: str
    output_desc: str
    working_directory: str
    interpreter: str
    entry: str
    preflight: str | None
    validation_exit_code: int
    validation_report: str
    validation_report_check: str
    timeout_s: int
    command: str = ""

    @classmethod
    def from_dict(cls, sid: str, d: dict[str, Any]) -> "ServiceSpec":
        val = d.get("validation") or {}
        return cls(
            id=sid,
            contract_version=int(d.get("contract_version", 1)),
            enabled=bool(d.get("enabled", True)),
            risk=str(d.get("risk", "medium")),
            purpose=str(d.get("purpose", "")),
            input_desc=str(d.get("input", "")),
            output_desc=str(d.get("output", "")),
            working_directory=str(d.get("working_directory", "")),
            interpreter=str(d.get("interpreter", "python")),
            entry=str(d.get("entry", "")),
            preflight=d.get("preflight"),
            validation_exit_code=int(val.get("exit_code", 0)),
            validation_report=str(val.get("report", "")),
            validation_report_check=str(val.get("report_check", "")),
            timeout_s=int(d.get("timeout_s", 300)),
            command=str(d.get("command", "")),
        )


def load_registry(root: Path | None = None) -> dict[str, ServiceSpec]:
    root = root or project_root()
    path = root / "config" / "service-registry.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        sid: ServiceSpec.from_dict(sid, spec)
        for sid, spec in (data.get("services") or {}).items()
    }


class ClassificationError(RuntimeError):
    pass


def classify_job(input_path: Path, requested: str | None = None) -> str:
    """Rule-based routing. Returns a service id or raises ClassificationError.

    Rules (deterministic, boring):
    - PDF files -> pdf-to-excel
    - CSV/XLSX/XLS -> excel-cleaner
    - a directory -> by contents if unambiguous, else error
    """
    if input_path.is_dir():
        names = [p.suffix.lower() for p in input_path.iterdir() if p.is_file()]
        pdfs = sum(1 for s in names if s == ".pdf")
        sheets = sum(1 for s in names if s in {".csv", ".xlsx", ".xls"})
        if pdfs and not sheets:
            return "pdf-to-excel"
        if sheets and not pdfs:
            return "excel-cleaner"
        raise ClassificationError(
            "directory contains mixed or unrecognized inputs; "
            "pass --service explicitly"
        )
    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        return "pdf-to-excel"
    if suffix in {".csv", ".xlsx", ".xls"}:
        return "excel-cleaner"
    raise ClassificationError(f"cannot classify input {input_path.name!r}; pass --service")


def _spec_dict(spec: ServiceSpec) -> dict[str, Any]:
    return dataclasses.asdict(spec)
