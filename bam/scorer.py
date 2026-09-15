"""Deterministic opportunity scoring (plan v3.1 §5-§6).

The score is internal prioritization, never a conversion probability. Rules:
- 8 dimensions x 0-3, YAML weights summing to 1.0 -> 0-100.
- < 2 OBSERVED evidences  => score capped at 40.
- empty evidence set      => DO NOT QUALIFY (never qualified).
- OBSERVED WINS: contradictory OBSERVED claims downgrade INFERRED to UNKNOWN
  (applied by the pipeline before scoring via apply_observed_wins()).
- the LLM never touches any number here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bam.config import Weights, load_weights


@dataclass(frozen=True)
class Scored:
    score: float
    confidence: str                     # low | medium | high
    dimensions: dict[str, int]
    reasons: list[str] = field(default_factory=list)
    observed_count: int = 0
    unknown_count: int = 0
    qualified: bool = False
    decision: str = "review"            # queued | review | disqualified | do_not_qualify
    notes: list[str] = field(default_factory=list)


def _confidence(n_observed: int, w: Weights) -> str:
    if n_observed >= w.high_min_observed:
        return "high"
    if n_observed >= w.medium_min_observed:
        return "medium"
    return "low"


def apply_observed_wins(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OBSERVED WINS rule (plan v3.1 §6).

    If an OBSERVED claim of a kind contradicts an INFERRED claim of the same
    kind (for our closed enums: a different value), the INFERRED claim is
    downgraded to UNKNOWN with a reason. The LLM can never reverse this.
    """
    observed_by_kind: dict[str, set[str]] = {}
    for c in claims:
        if c.get("status") == "OBSERVED":
            observed_by_kind.setdefault(c.get("kind", ""), {c.get("value", "")}).add(
                c.get("value", "")
            )
    out: list[dict[str, Any]] = []
    for c in claims:
        if c.get("status") == "INFERRED":
            observed_vals = observed_by_kind.get(c.get("kind", ""), set())
            if observed_vals and c.get("value") not in observed_vals:
                downgraded = dict(c)
                downgraded["status"] = "UNKNOWN"
                downgraded["reasoning"] = (
                    f"downgraded by OBSERVED WINS: observed {sorted(observed_vals)}"
                )
                out.append(downgraded)
                continue
        out.append(c)
    return out


def score_opportunity(
    profile: dict[str, Any],
    weights: Weights | None = None,
) -> Scored:
    """Score a lead profile deterministically. Pure function of its inputs."""
    w = weights or load_weights()

    evidence_rows = profile.get("evidence") or []
    claims = profile.get("claims") or []
    signals = profile.get("signals") or {}
    unknowns = profile.get("unknowns") or []

    observed = [e for e in evidence_rows if e.get("status") == "OBSERVED"]
    n_observed = len(observed)
    n_unknown = len(unknowns)

    services = {
        c["value"]
        for c in claims
        if c.get("kind") == "detected_service" and c.get("status") == "OBSERVED"
    }
    problems = {
        c["value"]
        for c in claims
        if c.get("kind") == "observable_problem" and c.get("status") == "OBSERVED"
    }
    techs = set(signals.get("technologies") or [])
    has_contact = bool(signals.get("emails")) or bool(signals.get("contact_url")) or bool(
        signals.get("has_contact_form")
    )

    registry = profile.get("registry") or {}
    known_services = {s for s, caps in registry.items() if caps.get("enabled", True)}
    matches = sorted(services & known_services)

    # -- dimensions, each 0-3, deterministic ---------------------------------
    fit = 0
    if services:
        fit = 3 if len(services) >= 2 else 2
    elif signals.get("keyword_services"):
        fit = 1

    evidence_dim = min(3, n_observed)

    impact = 0
    if problems:
        impact = 3 if len(problems) >= 2 else 2

    service_match = min(3, len(matches))

    delivery = 0
    if "data-cleaning" in services:
        delivery = max(delivery, 3)
    if "pdf-extraction" in services:
        delivery = max(delivery, 2)
    if services:
        delivery = max(delivery, 1)

    urgency = 0
    if {"manual-excel-work", "slow-reporting"} & problems:
        urgency = 2
    elif problems:
        urgency = 1

    buying = 0
    if signals.get("pricing_url"):
        buying += 1
    if techs & {"hubspot", "mailchimp", "google-analytics", "intercom"}:
        buying += 1
    buying = min(3, buying)

    contactability = 0
    if signals.get("emails"):
        contactability += 2
    if signals.get("has_contact_form") or signals.get("contact_url"):
        contactability += 1
    contactability = min(3, contactability)

    dims = {
        "fit": fit,
        "evidence": evidence_dim,
        "business_impact": impact,
        "service_match": service_match,
        "delivery_feasibility": delivery,
        "urgency": urgency,
        "buying_signal": buying,
        "contactability": contactability,
    }

    total = sum(dims[k] * w.dimensions[k] for k in dims) / 3.0 * 100.0

    reasons: list[str] = []
    if matches:
        reasons.append(f"service match: {', '.join(matches)}")
    if problems:
        reasons.append(f"observed problems: {', '.join(sorted(problems))}")
    if not has_contact:
        reasons.append("no obvious contact channel found")
    if not signals.get("keyword_services"):
        reasons.append("no service keywords on homepage")

    qualified = True
    decision = "review"
    if n_observed == 0:
        qualified = False
        decision = "do_not_qualify"
        reasons.append("empty evidence set: DO NOT QUALIFY")
    else:
        if n_observed < w.min_observed_for_full_score:
            total = min(total, float(w.capped_score))
            reasons.append(
                f"score capped at {w.capped_score}: only {n_observed} OBSERVED evidence(s)"
            )
        if total >= w.auto_queue_min:
            decision = "queued"
        elif total < w.review_min:
            decision = "disqualified"

    return Scored(
        score=round(total, 1),
        confidence=_confidence(n_observed, w),
        dimensions=dims,
        reasons=reasons,
        observed_count=n_observed,
        unknown_count=n_unknown,
        qualified=qualified,
        decision=decision,
    )
