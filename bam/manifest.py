"""Run manifests: one JSON record per pipeline/job run.

Makes every BAM action reconstructible (plan v3.1 §18 step 8): inputs hashed,
commands, versions, timings, actor, outcome, errors.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_VERSION = "0.1.0"


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """tmp -> fsync -> rename (pattern copied minimally from QA Agent)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


@dataclass
class RunManifest:
    run_id: str
    kind: str
    actor: str = "agent"
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    finished_at: str | None = None
    status: str = "running"
    inputs: list[dict[str, str]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    outcome: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def add_input(self, label: str, sha256: str, *, url: str | None = None,
                  path: str | None = None) -> None:
        entry = {"label": label, "sha256": sha256}
        if url:
            entry["url"] = url
        if path:
            entry["path"] = path
        self.inputs.append(entry)

    def add_step(self, name: str, *, status: str = "ok",
                 detail: dict[str, Any] | None = None, elapsed_s: float = 0.0) -> None:
        self.steps.append({
            "step": name,
            "status": status,
            "elapsed_s": round(elapsed_s, 3),
            "detail": detail or {},
        })

    def finish(self, status: str = "ok", outcome: dict[str, Any] | None = None,
               error: str | None = None) -> None:
        self.status = status
        self.outcome = outcome or {}
        self.error = error
        self.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "kind": self.kind,
            "actor": self.actor,
            "bam_version": _VERSION,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "inputs": self.inputs,
            "steps": self.steps,
            "outcome": self.outcome,
            "error": self.error,
        }

    def write(self, dir_path: Path) -> Path:
        path = dir_path / f"run_{self.run_id}.json"
        atomic_write_json(path, self.to_dict())
        return path
