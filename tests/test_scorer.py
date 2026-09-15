"""Scorer tests: caps, thresholds, OBSERVED WINS, determinism."""

from __future__ import annotations

from typing import Any

from bam.config import load_weights
from bam.scorer import apply_observed_wins, score_opportunity


def _profile(n_observed: int, *, services: list[str] | None = None,
             problems: list[str] | None = None, emails: bool = True,
             unknowns: int = 0) -> dict[str, Any]:
    return {
        "signals": {
            "technologies": ["wordpress"],
            "emails": ["x@y.test"] if emails else [],
            "has_contact_form": emails,
            "contact_url": "/contact" if emails else None,
            "pricing_url": "/pricing" if emails else None,
        },
        "claims": (
            [{"kind": "detected_service", "value": s, "status": "OBSERVED"}
             for s in (services or [])]
            + [{"kind": "observable_problem", "value": p, "status": "OBSERVED"}
               for p in (problems or [])]
        ),
        "evidence": [{"status": "OBSERVED"} for _ in range(n_observed)],
        "unknowns": [f"u{i}" for i in range(unknowns)],
        "registry": {"pdf-to-excel": {"enabled": True},
                     "excel-cleaner": {"enabled": True}},
    }


def test_strong_profile_scores_and_queues() -> None:
    r = score_opportunity(_profile(4, services=["data-cleaning", "pdf-extraction"],
                                   problems=["manual-excel-work", "slow-reporting"]))
    assert r.score > 50
    assert r.confidence in ("medium", "high")
    assert r.qualified is True


def test_few_observed_caps_score_at_40() -> None:
    w = load_weights()
    r = score_opportunity(_profile(1, services=["data-cleaning"],
                                   problems=["manual-excel-work"]))
    assert r.score <= w.capped_score
    assert any("capped" in x for x in r.reasons)


def test_empty_evidence_never_qualifies() -> None:
    r = score_opportunity(_profile(0, services=["data-cleaning"],
                                   problems=["manual-excel-work"]))
    assert r.qualified is False
    assert r.decision == "do_not_qualify"


def test_low_score_disqualifies() -> None:
    # no services, no problems, no contact channels, 1 evidence
    p = _profile(1, emails=False)
    r = score_opportunity(p)
    assert r.decision == "disqualified"


def test_identical_inputs_identical_score() -> None:
    p = _profile(3, services=["data-cleaning"], problems=["manual-excel-work"])
    assert score_opportunity(p).score == score_opportunity(p).score


def test_observed_wins_downgrades_inferred() -> None:
    claims = [
        {"kind": "detected_service", "value": "web-dev", "status": "INFERRED"},
        {"kind": "detected_service", "value": "data-cleaning", "status": "OBSERVED"},
    ]
    out = apply_observed_wins(claims)
    web = [c for c in out if c["value"] == "web-dev"][0]
    assert web["status"] == "UNKNOWN"
    assert "OBSERVED WINS" in web["reasoning"]


def test_confidence_grows_with_observed_evidence() -> None:
    w = load_weights()
    low = score_opportunity(_profile(1))
    mid = score_opportunity(_profile(w.medium_min_observed))
    high = score_opportunity(_profile(w.high_min_observed))
    assert (low.confidence, mid.confidence, high.confidence) == ("low", "medium", "high")


def test_unknowns_counted() -> None:
    r = score_opportunity(_profile(3, unknowns=3))
    assert r.unknown_count == 3
