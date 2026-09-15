"""Sales Copilot: tier classification, WHY explanations, daily sales queue,
follow-up digests and objection playbook.

Deterministic only. Every recommendation is derived from OBSERVED evidence,
commercial signals (bam/signals.py) and store state. The LLM may polish
wording elsewhere, but the decision layer here never calls a model.

Principles:
- Score is PRIORITY, never a conversion probability.
- Every A/B lead gets a WHY grounded in evidence with OBSERVED/UNKNOWN split.
- Hypotheses are labeled HYPOTHESIS: "If this is part of your workflow…".
- Human approval before any external action; this module never sends anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bam.signals import CONTACT_PRIORITY_ROLES, OFFERS

# -- tiers (§3) --------------------------------------------------------------------

TIERS = ("A", "B", "C", "D")

_TIER_ACTION = {
    "A": "CONTACT TODAY",
    "B": "RESEARCH MORE",
    "C": "LOW PRIORITY",
    "D": "DO NOT PURSUE",
}


def classify_tier(
    *,
    score: float | None,
    commercial: dict[str, Any],
    contactability: dict[str, Any],
    recommended_service: str | None,
    lead_state: str,
) -> str:
    """A/B/C/D from deterministic inputs. Publisher-like candidates are
    always D; no contact at all caps at B (research-more), never A."""
    ctype = (commercial.get("candidate_type") or "company")
    if ctype != "company":
        return "D"
    base = commercial.get("base_score") or 0
    contact = contactability.get("score", 0) if contactability else 0
    s = score or 0
    has_service = bool(recommended_service)
    if lead_state in ("disqualified", "do_not_contact", "blocked"):
        return "D"
    # A: strong evidence + real service fit + reachable
    if has_service and base >= 6 and contact >= 3 and s >= 60:
        return "A"
    # B: plausible fit but missing evidence or contactability
    if has_service and (base >= 2 or s >= 45):
        return "B"
    # C: weak/potential fit, thin evidence
    if base >= 1 or s >= 30:
        return "C"
    return "D"


def tier_action(tier: str) -> str:
    return _TIER_ACTION.get(tier, "REVIEW")


# -- WHY THIS LEAD (§10) ------------------------------------------------------------

@dataclass
class WhyExplanation:
    headline: str
    bullets: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    offer: dict[str, str] | None = None
    next_action: str = "RESEARCH MORE"
    hypothesis: bool = False


_SERVICE_EVIDENCE_HINTS = {
    "pdf_to_excel": "document-processing evidence (invoices/reports workflows)",
    "excel_cleaning": "recurring spreadsheet/CSV evidence",
    "qa_automation": "web product / testing evidence",
}


def explain_why(
    lead: dict[str, Any],
    evidence: list[dict[str, Any]],
    commercial: dict[str, Any],
    contactability: dict[str, Any],
) -> WhyExplanation:
    """Human explanation: WHY pursue, grounded in evidence. Deterministic."""
    service = lead.get("recommended_service")
    offer = OFFERS.get(service) if service else None
    obs = [e for e in evidence if e.get("status") == "OBSERVED"]
    unknowns: list[str] = []
    bullets: list[str] = []
    hypothesis = False

    ctype = commercial.get("candidate_type") or "company"
    if ctype != "company":
        return WhyExplanation(
            headline="Not a company: this is a publisher/aggregator/directory — never pursue.",
            unknowns=[], offer=None, next_action="DO NOT PURSUE")

    if commercial.get("recommended_service"):
        bullets.append(
            f"Commercial signals point to {commercial['recommended_service']} work "
            f"(weighted phrase score {commercial.get('total_score', 0)}, "
            f"{commercial.get('pages_matched', {}).get(commercial['recommended_service'], 0)} page(s)).")
    else:
        unknowns.append("no observed commercial-signal phrases on their pages")
        hypothesis = True

    if service:
        bullets.append(
            f"{OFFERS[service]['name']} fits {OFFERS[service]['evidence_phrase']}.")
    else:
        unknowns.append("service fit undetermined")

    if obs:
        bullets.append(f"{len(obs)} OBSERVED evidence item(s): "
                       + "; ".join((e.get("kind") or "?") for e in obs[:4]) + ".")
    else:
        unknowns.append("no OBSERVED evidence captured yet")
        hypothesis = True

    level = contactability.get("level")
    if level == "high":
        bullets.append("Contact path is strong (email/form observed on site).")
    elif level == "medium":
        bullets.append("Contact path exists (form or contact page) — no direct email yet.")
    else:
        unknowns.append("no observed contact channel yet")

    tier = classify_tier(score=lead.get("score"), commercial=commercial,
                         contactability=contactability,
                         recommended_service=service, lead_state=lead.get("state", ""))
    action = tier_action(tier)
    headline = f"{tier} — {action}" if tier in ("A", "B") else f"{tier} — {action}"
    return WhyExplanation(headline=headline, bullets=bullets, unknowns=unknowns,
                          offer=offer, next_action=action, hypothesis=hypothesis)


# -- suggested offer / contact roles (§11-§13) ---------------------------------------

def suggested_contact_roles(service_id: str | None) -> tuple[str, ...]:
    return CONTACT_PRIORITY_ROLES.get(service_id or "", ("founder", "owner"))


# -- outreach hypothesis framing (§15) ------------------------------------------------

_HYPOTHESIS_PAT = re.compile(r"\b(if|maybe|perhaps|possibly)\b", re.I)


def enforce_hypothesis_honesty(body: str, *, hypothesis: bool) -> str:
    """Ensure hypothesized pain is phrased conditionally. Deterministic check:
    when the WHY was hypothetical, the draft must use conditional phrasing."""
    if not hypothesis:
        return body
    if _HYPOTHESIS_PAT.search(body):
        return body
    # minimal honest edit: prepend a conditional opener to the first sentence
    return body.replace("Hi,", "Hi,", 1).replace(
        "\n\n", "\n\nIf this is part of your workflow: ", 1) if "\n\n" in body else \
        "If this is part of your workflow: " + body


# -- response objection playbook (§20) ----------------------------------------------
# Keys mirror bam.commercial.RESPONSE_CLASSES so `bam response` can map every
# classification straight to guidance. No invented claims anywhere.

OBJECTION_PLAYBOOK: dict[str, dict[str, Any]] = {
    "positive": {
        "means": "Buying signal — they are interested.",
        "say": ("Confirm scope concretely (what they send, what they get back) "
                "and propose the smallest real first batch."),
        "next": "Prepare quote; human approves before anything is sent",
        "quote_needed": True,
    },
    "pricing": {
        "means": "Direct pricing request (or 'too expensive' in disguise).",
        "say": ("Quote from real scope (files/pages/rows) with assumptions and "
                "exclusions explicit; if price was the blocker, offer a small "
                "paid pilot batch before any bigger commitment."),
        "next": "Run quote flow; state assumptions, never haggle vaguely",
        "quote_needed": True,
    },
    "objection": {
        "means": "A real blocker was named (incumbent, trust, timing, proof).",
        "say": ("Address ONLY the blocker they named: incumbent → 'easy "
                "comparison test with one overflow batch'; proof → redacted "
                "before/after sample; timing → 'offer stands for the next "
                "reporting cycle'."),
        "next": "Respond to the named blocker; park as not-now if timing",
        "quote_needed": False,
    },
    "question": {
        "means": "They are evaluating feasibility/mechanics.",
        "say": ("Answer concretely from real capabilities: 'you send documents; "
                "processing is local and audited; you get a structured "
                "spreadsheet plus an audit report'. Offer a small paid sample."),
        "next": "Answer the exact question; do not improvise features",
        "quote_needed": False,
    },
    "needs_more_information": {
        "means": "Timing/deferral — not a rejection of the offer itself.",
        "say": ("Acknowledge the timing; pin the next concrete moment (their "
                "next monthly close, next quarter) for one short follow-up."),
        "next": "Schedule follow-up for a specific date, then reassess",
        "quote_needed": False,
    },
    "not_interested": {
        "means": "Explicit decline. Do not push.",
        "say": ("Thank them and close politely; leave the door open without "
                "re-pitching."),
        "next": "Mark lost; no further outreach",
        "quote_needed": False,
    },
    "wrong_contact": {
        "means": "Someone else owns this decision.",
        "say": ("Ask who handles document/data workflows — do not pitch the "
                "wrong person."),
        "next": "Find the right contact; restart research on the contact path",
        "quote_needed": False,
    },
    "do_not_contact": {
        "means": "They asked to stop contact.",
        "say": ("Confirm politely that they will not be contacted again. "
                "No pitch, no follow-up."),
        "next": "do_not_contact state; denylist-worthy if repeated",
        "quote_needed": False,
    },
    "unclear": {
        "means": "Intent not clear from the reply.",
        "say": ("Ask ONE concrete question about their current document/"
                "spreadsheet process rather than pushing the offer again."),
        "next": "One clarifying question, then reassess",
        "quote_needed": False,
    },
}


def playbook_for(classification: str) -> dict[str, str] | None:
    """Objection playbook entry for a response classification (None if none)."""
    return OBJECTION_PLAYBOOK.get(classification)


# -- daily sales queue (§17) -----------------------------------------------------------

@dataclass
class QueueEntry:
    lead_id: int
    company: str
    domain: str | None
    score: float | None
    tier: str
    service: str | None
    offer_name: str | None
    why: WhyExplanation
    contact: str | None
    action: str
    state: str


def build_daily_queue(
    candidates: list[dict[str, Any]],
    *,
    limit: int = 5,
    contacted_this_week: int = 0,
    max_daily_outreach: int = 5,
) -> tuple[list[QueueEntry], dict[str, int]]:
    """Rank candidates into today's queue. Anti-spam: the queue never
    recommends more than max_daily_outreach contact-actions per day, and
    caps by what's already been sent this week. Deterministic ordering:
    tier, then score desc, then id.

    Each candidate dict needs: id, company_name, domain, score, state,
    recommended_service, evidence (list), commercial (dict), contactability (dict),
    contact (str|None).
    """
    entries: list[QueueEntry] = []
    for c in candidates:
        comm = c.get("commercial") or {}
        cont = c.get("contactability") or {}
        tier = classify_tier(score=c.get("score"), commercial=comm,
                             contactability=cont,
                             recommended_service=c.get("recommended_service"),
                             lead_state=c.get("state", ""))
        why = explain_why(c, c.get("evidence") or [], comm, cont)
        contact = c.get("contact")
        action = tier_action(tier)
        if tier == "A" and not contact:
            action = "REVIEW CONTACT PATH"
        entries.append(QueueEntry(
            lead_id=c["id"], company=c.get("company_name") or c.get("domain") or "?",
            domain=c.get("domain"), score=c.get("score"), tier=tier,
            service=c.get("recommended_service"),
            offer_name=(OFFERS.get(c.get("recommended_service")) or {}).get("name"),
            why=why, contact=contact, action=action, state=c.get("state", "")))

    order = {t: i for i, t in enumerate(TIERS)}
    entries.sort(key=lambda e: (order.get(e.tier, 9),
                                -(e.score or 0), e.lead_id))
    stats = {
        "A": sum(1 for e in entries if e.tier == "A"),
        "B": sum(1 for e in entries if e.tier == "B"),
        "C": sum(1 for e in entries if e.tier == "C"),
        "D": sum(1 for e in entries if e.tier == "D"),
    }
    # anti-spam cap: contact-actions limited by daily budget minus week usage
    budget = max(0, max_daily_outreach - contacted_this_week)
    shown = 0
    limited: list[QueueEntry] = []
    for e in entries:
        if e.tier in ("A", "B") and e.action.startswith("CONTACT"):
            if shown >= limit or shown >= budget:
                e.action = "QUEUED FOR TOMORROW (daily cap)"
            else:
                shown += 1
        limited.append(e)
    return limited, stats
