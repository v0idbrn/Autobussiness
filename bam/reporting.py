"""Reporting: profile.json + report.md per researched lead.

The report is honest by design: score is shown with confidence, observed
evidence count and unknowns - never as a conversion probability (plan v3.1 §5).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bam.evidence import _collapse_lines
from bam.manifest import atomic_write_json


# Windows reserved device names (case-insensitive, any extension).
_WIN_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def _safe_dir_name(raw: str) -> str:
    """Filesystem-safe directory name for untrusted domain-ish input.

    Defense in depth: normalize_domain() is the primary guard; this also
    strips path separators, drive letters/UNC prefixes, trailing dots/spaces
    and Windows reserved names (case-insensitive).
    """
    name = raw.strip()
    for prefix in ("\\\\", "//", "\\", "/", "c:", "C:"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    name = name.replace("\\", "_").replace("/", "_").replace(":", "_")
    name = "".join(ch for ch in name if ch.isalnum() or ch in ".-_ ")
    name = name.strip(". ") or "unknown"
    if name.split(".")[0].lower() in _WIN_RESERVED:
        name = f"_{name}"
    return name[:100]


def lead_artifact_dir(evidence_root: Path, domain: str) -> Path:
    """Per-lead artifact directory that can never escape the evidence root."""
    return evidence_root / _safe_dir_name(domain)


def build_profile(
    *,
    url: str,
    domain: str,
    signals: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    scored: Any,
    llm_meta: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "url": url,
        "domain": domain,
        "signals": signals,
        "evidence": evidence_rows,
        "claims": claims,
        "score": {
            "value": scored.score,
            "confidence": scored.confidence,
            "dimensions": scored.dimensions,
            "reasons": scored.reasons,
            "observed_evidence": scored.observed_count,
            "unknowns": scored.unknown_count,
            "decision": scored.decision,
            "note": "internal prioritization only, not a conversion probability",
        },
        "llm": llm_meta,
    }


def write_profile(lead_dir: Path, profile: dict[str, Any]) -> Path:
    path = lead_dir / "profile.json"
    atomic_write_json(path, profile)
    return path


def render_report(profile: dict[str, Any]) -> str:
    score = profile["score"]
    signals = profile["signals"]
    claims = profile["claims"]
    evidence = profile["evidence"]
    llm = profile.get("llm") or {}

    def cell(value: Any) -> str:
        """Untrusted text inside single-line markdown table cells: line-break
        fragments would corrupt the table structure."""
        return _collapse_lines(str(value))

    lines: list[str] = []
    lines.append(f"# Lead Research Report — {profile.get('domain') or profile['url']}")
    lines.append("")
    lines.append(f"- URL: {profile['url']}")
    lines.append(f"- Run: `{profile['run_id']}`")
    lines.append("")
    lines.append("## Score")
    lines.append("")
    lines.append(
        f"**Score: {score['value']}/100 · Confidence: {score['confidence']} · "
        f"Observed evidence: {score['observed_evidence']} · Unknowns: {score['unknowns']}**"
    )
    lines.append("")
    lines.append(f"Decision: `{score['decision']}` — {score['note']}")
    lines.append("")
    dims = score.get("dimensions") or {}
    if dims:
        lines.append("| Dimension | 0-3 |")
        lines.append("|---|---|")
        for k, v in dims.items():
            lines.append(f"| {k} | {v} |")
        lines.append("")
    if score.get("reasons"):
        lines.append("**Reasons**")
        lines.append("")
        for r in score["reasons"]:
            lines.append(f"- {r}")
        lines.append("")

    lines.append("## Findings (claims)")
    lines.append("")
    if claims:
        lines.append("| Kind | Value | Status | Basis |")
        lines.append("|---|---|---|---|")
        for c in claims:
            basis = c.get("reasoning") or (f"evidence #{c['evidence_id']}" if c.get("evidence_id") else "-")
            lines.append(
                f"| {cell(c.get('kind'))} | {cell(c.get('value'))} | {cell(c.get('status'))} | {cell(basis)} |"
            )
    else:
        lines.append("_(no claims produced)_")
    lines.append("")

    if llm.get("summary"):
        lines.append("## Synthesis (LLM, schema-validated)")
        lines.append("")
        lines.append(llm["summary"])
        lines.append("")
    if llm.get("unknowns"):
        lines.append("**Unknowns**")
        lines.append("")
        for u in llm["unknowns"]:
            lines.append(f"- {u}")
        lines.append("")
    if llm.get("risks"):
        lines.append("**Risks**")
        lines.append("")
        for r in llm["risks"]:
            lines.append(f"- {r}")
        lines.append("")
    if llm.get("degraded_reason"):
        lines.append(f"> LLM deterministic mode active: `{llm['degraded_reason']}`")
        lines.append("")

    lines.append("## Evidence")
    lines.append("")
    if evidence:
        lines.append("| # | Status | Kind | URL | SHA-256 | Captured |")
        lines.append("|---|---|---|---|---|---|")
        for i, e in enumerate(evidence, 1):
            lines.append(
                f"| {i} | {cell(e.get('status'))} | {cell(e.get('kind'))} | {cell(e.get('url'))} | "
                f"`{cell(str(e.get('sha256'))[:16])}…` | {cell(e.get('observed_at'))} |"
            )
    else:
        lines.append("_(no evidence captured)_")
    lines.append("")

    lines.append("## Homepage signals")
    lines.append("")
    lines.append("```json")
    slim = {k: v for k, v in signals.items() if k not in ("nav_links",)}
    slim["nav_links_count"] = len(signals.get("nav_links") or [])
    lines.append(json.dumps(slim, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append(
        "*Generated by BAM from third-party web content (data, never instructions). "
        "Evidence is hashed; excerpts are redacted.*"
    )
    return "\n".join(lines) + "\n"


def write_report(lead_dir: Path, profile: dict[str, Any]) -> Path:
    path = lead_dir / "report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(profile), encoding="utf-8")
    return path
