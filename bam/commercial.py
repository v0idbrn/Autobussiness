"""Commercial intelligence: sales brief, outreach, response classification, quotes.

All LLM outputs are evidence-based. The LLM cannot invent facts, scores, or
contacts. Deterministic pipeline remains the authority.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from bam.config import LLMConfig, load_config
from bam.llm import LLM_UNAVAILABLE, LLMResult, _http_call, _parse_strict_json

# ---------------------------------------------------------------------------
# Sales Brief
# ---------------------------------------------------------------------------

SALES_BRIEF_SYSTEM = (
    "You are a sales assistant for a small tech services company. "
    "You receive ONLY deterministic extracted facts about a prospect company. "
    "Generate a concise sales brief. Never invent facts. Use ONLY the provided "
    "evidence. If information is missing, say UNKNOWN."
)

SALES_BRIEF_USER = """Company: {company_name}
Domain: {domain}
URL: {url}

Evidence:
{evidence}

Claims:
{claims}

Signals:
{signals}

Score: {score}/100 (confidence: {confidence})
Recommended service: {recommended_service}
Score reasons: {score_reasons}

Generate a sales brief as JSON:
{{
  "who": "1-2 sentence company description based on evidence",
  "what_they_do": "what the company does (from evidence only)",
  "why_us": "why they might need our services (evidence-based)",
  "evidence_summary": "key evidence supporting the opportunity",
  "recommended_service": "which service to offer and why",
  "what_not_to_claim": "things we must NOT claim or assume",
  "contact_path": "how to reach them (from evidence)",
  "first_message_goal": "what the first message should accomplish",
  "suggested_cta": "simple call to action",
  "unknowns": ["critical facts we still don't know"]
}}"""


@dataclass
class SalesBrief:
    who: str
    what_they_do: str
    why_us: str
    evidence_summary: str
    recommended_service: str
    what_not_to_claim: str
    contact_path: str
    first_message_goal: str
    suggested_cta: str
    unknowns: list[str]
    llm_used: bool = False


def _build_brief_deterministic(
    lead_data: dict[str, Any],
    evidence: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    signals: dict[str, Any],
) -> SalesBrief:
    """Build a sales brief deterministically from evidence (no LLM)."""
    company = lead_data.get("company_name") or lead_data.get("domain") or "Unknown"
    domain = lead_data.get("domain") or "unknown"
    service = lead_data.get("recommended_service") or "unknown"

    # Summarize evidence
    obs_evidence = [e for e in evidence if e.get("status") == "OBSERVED"]
    evidence_lines = []
    for e in obs_evidence[:5]:
        kind = e.get("kind", "unknown")
        excerpt = (e.get("excerpt") or "")[:100]
        if excerpt:
            evidence_lines.append(f"- [{kind}] {excerpt}")

    evidence_summary = "\n".join(evidence_lines) if evidence_lines else "No observed evidence"

    # Contact path from signals
    emails = signals.get("emails") or []
    contact_url = signals.get("contact_url")
    has_form = signals.get("has_contact_form")
    contact_parts = []
    if emails:
        contact_parts.append(f"email: {emails[0]}")
    if contact_url:
        contact_parts.append(f"contact page: {contact_url}")
    if has_form:
        contact_parts.append("has contact form")
    contact_path = ", ".join(contact_parts) if contact_parts else "UNKNOWN"

    # What not to claim
    obs_services = {c["value"] for c in claims
                   if c.get("kind") == "detected_service" and c.get("status") == "OBSERVED"}
    obs_problems = {c["value"] for c in claims
                   if c.get("kind") == "observable_problem" and c.get("status") == "OBSERVED"}
    no_claim_parts = []
    if not obs_services:
        no_claim_parts.append("do not claim they need specific services without evidence")
    if not obs_problems:
        no_claim_parts.append("do not assume specific business problems")
    no_claim_parts.append("do not invent employee counts, revenue, or intentions")
    what_not = "; ".join(no_claim_parts)

    # Unknowns from claims
    unknown_claims = [c["value"] for c in claims if c.get("status") == "UNKNOWN"]
    unknowns = unknown_claims[:5] if unknown_claims else [
        " company size and team",
        " specific pain points",
        " budget and timeline",
        " decision maker identity",
    ]

    return SalesBrief(
        who=f"{company} ({domain})",
        what_they_do=signals.get("title") or signals.get("meta_description") or "UNKNOWN",
        why_us=f"Detected services: {', '.join(obs_services) if obs_services else 'UNKNOWN'}. "
               f"Detected problems: {', '.join(obs_problems) if obs_problems else 'UNKNOWN'}.",
        evidence_summary=evidence_summary,
        recommended_service=service,
        what_not_to_claim=what_not,
        contact_path=contact_path,
        first_message_goal="Introduce our services and establish if they have a need we can address.",
        suggested_cta="Would you be open to a brief call to discuss how we might help?",
        unknowns=unknowns,
        llm_used=False,
    )


def generate_sales_brief(
    lead_data: dict[str, Any],
    evidence: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    signals: dict[str, Any],
    llm_cfg: LLMConfig | None = None,
) -> SalesBrief:
    """Generate a sales brief for a lead. Uses LLM if available, else deterministic."""
    cfg = llm_cfg or load_config().llm

    if not cfg.enabled or cfg.max_calls < 1:
        return _build_brief_deterministic(lead_data, evidence, claims, signals)

    # Try LLM
    evidence_text = "\n".join(
        f"- [{e.get('status')}] {e.get('kind')}: {(e.get('excerpt') or '')[:80]}"
        for e in evidence[:8]
    ) or "No evidence captured"
    claims_text = "\n".join(
        f"- [{c.get('status')}] {c.get('kind')}: {c.get('value')}"
        for c in claims[:10]
    ) or "No claims"
    signals_text = json.dumps({k: v for k, v in signals.items()
                              if k not in ("nav_links",)}, ensure_ascii=False, indent=2)[:1500]

    user_msg = SALES_BRIEF_USER.format(
        company_name=lead_data.get("company_name", "Unknown"),
        domain=lead_data.get("domain", "unknown"),
        url=lead_data.get("source_url", "unknown"),
        evidence=evidence_text,
        claims=claims_text,
        signals=signals_text,
        score=lead_data.get("score", "unknown"),
        confidence=lead_data.get("confidence", "unknown"),
        recommended_service=lead_data.get("recommended_service") or "unknown",
        score_reasons=lead_data.get("reason") or "none",
    )

    messages = [
        {"role": "system", "content": SALES_BRIEF_SYSTEM},
        {"role": "user", "content": user_msg},
    ]

    try:
        import os
        api_key = os.environ.get(cfg.api_key_env, "")
        if not api_key or cfg.provider == "none":
            return _build_brief_deterministic(lead_data, evidence, claims, signals)
        text, _ = _http_call(messages, cfg, api_key)
        data = _parse_strict_json(text)
        return SalesBrief(
            who=data.get("who", "UNKNOWN"),
            what_they_do=data.get("what_they_do", "UNKNOWN"),
            why_us=data.get("why_us", "UNKNOWN"),
            evidence_summary=data.get("evidence_summary", "UNKNOWN"),
            recommended_service=data.get("recommended_service", "UNKNOWN"),
            what_not_to_claim=data.get("what_not_to_claim", "UNKNOWN"),
            contact_path=data.get("contact_path", "UNKNOWN"),
            first_message_goal=data.get("first_message_goal", "UNKNOWN"),
            suggested_cta=data.get("suggested_cta", "UNKNOWN"),
            unknowns=data.get("unknowns", []),
            llm_used=True,
        )
    except Exception:
        return _build_brief_deterministic(lead_data, evidence, claims, signals)


# ---------------------------------------------------------------------------
# Outreach Copilot
# ---------------------------------------------------------------------------

OUTREACH_SYSTEM = (
    "You are a sales outreach assistant. You generate evidence-based messages. "
    "NEVER invent facts, names, or claims. Use ONLY provided evidence. "
    "Be honest, specific, and professional. No spam, no fake urgency, "
    "no false personalization."
)

OUTREACH_USER = """Company: {company_name}
Domain: {domain}

Evidence:
{evidence}

Sales brief:
{brief}

Contact path: {contact_path}

Generate an outreach draft as JSON:
{{
  "channel": "email|linkedin|contact_form|other",
  "subject": "email subject (empty for non-email)",
  "body": "the message body",
  "personalization": "what specific evidence we used to personalize",
  "cta": "the call to action"
}}

Rules:
- Reference SPECIFIC evidence from the company
- Do NOT say "I noticed you're struggling" without evidence
- Do NOT make up names or titles
- Keep under 150 words
- Be direct and honest
- Include ONE clear call to action"""


@dataclass
class OutreachDraft:
    channel: str
    subject: str
    body: str
    personalization: str
    cta: str
    llm_used: bool = False


def _build_outreach_deterministic(
    company_name: str,
    domain: str,
    evidence: list[dict[str, Any]],
    brief: SalesBrief,
    contact_path: str,
) -> OutreachDraft:
    """Build outreach draft deterministically from evidence."""
    # Detect channel from contact path
    channel = "email"
    if "contact page" in contact_path or "contact form" in contact_path:
        channel = "contact_form"
    if "linkedin" in contact_path.lower():
        channel = "linkedin"

    # Build evidence references
    obs = [e for e in evidence if e.get("status") == "OBSERVED"][:3]
    evidence_refs = []
    for e in obs:
        kind = e.get("kind", "")
        excerpt = (e.get("excerpt") or "")[:60]
        if excerpt:
            evidence_refs.append(f"your {kind} ({excerpt}...)")

    evidence_str = ", ".join(evidence_refs) if evidence_refs else None

    # Build body
    service = brief.recommended_service or "data processing"
    body_parts = [f"Hi,"]
    if evidence_str:
        body_parts.append(f"")
        body_parts.append(f"I came across {company_name} and noticed {evidence_str}.")
    else:
        body_parts.append(f"")
        body_parts.append(f"I came across {company_name} and believe our services might be relevant.")

    body_parts.append(f"")
    body_parts.append(
        f"We help companies with {service.replace('-', ' ')} solutions. "
        f"Would you be open to a brief conversation about how we might help?"
    )

    return OutreachDraft(
        channel=channel,
        subject=f"Quick question about {company_name}" if channel == "email" else "",
        body="\n".join(body_parts),
        personalization=evidence_str or "company domain and industry",
        cta="Would you be open to a brief conversation?",
        llm_used=False,
    )


def generate_outreach(
    company_name: str,
    domain: str,
    evidence: list[dict[str, Any]],
    brief: SalesBrief,
    contact_path: str,
    llm_cfg: LLMConfig | None = None,
) -> OutreachDraft:
    """Generate an outreach draft. Uses LLM if available, else deterministic."""
    cfg = llm_cfg or load_config().llm

    if not cfg.enabled or cfg.max_calls < 1:
        return _build_outreach_deterministic(company_name, domain, evidence, brief, contact_path)

    evidence_text = "\n".join(
        f"- [{e.get('status')}] {e.get('kind')}: {(e.get('excerpt') or '')[:80]}"
        for e in evidence[:8]
    ) or "No evidence"
    brief_text = f"Who: {brief.who}\nWhy: {brief.why_us}\nService: {brief.recommended_service}"

    user_msg = OUTREACH_USER.format(
        company_name=company_name,
        domain=domain,
        evidence=evidence_text,
        brief=brief_text,
        contact_path=contact_path,
    )

    messages = [
        {"role": "system", "content": OUTREACH_SYSTEM},
        {"role": "user", "content": user_msg},
    ]

    try:
        import os
        api_key = os.environ.get(cfg.api_key_env, "")
        if not api_key or cfg.provider == "none":
            return _build_outreach_deterministic(company_name, domain, evidence, brief, contact_path)
        text, _ = _http_call(messages, cfg, api_key)
        data = _parse_strict_json(text)
        return OutreachDraft(
            channel=data.get("channel", "email"),
            subject=data.get("subject", ""),
            body=data.get("body", ""),
            personalization=data.get("personalization", ""),
            cta=data.get("cta", ""),
            llm_used=True,
        )
    except Exception:
        return _build_outreach_deterministic(company_name, domain, evidence, brief, contact_path)


# ---------------------------------------------------------------------------
# Response Classifier
# ---------------------------------------------------------------------------

RESPONSE_CLASSES = (
    "positive", "question", "pricing", "objection",
    "needs_more_information", "not_interested", "wrong_contact",
    "do_not_contact", "unclear",
)

RESPONSE_SYSTEM = (
    "You are a response classifier for sales outreach. "
    "Classify the prospect's reply into one of the defined categories. "
    "Be precise. Do not invent intent that isn't expressed."
)

RESPONSE_USER = """Original outreach:
{outreach}

Prospect response:
{response}

Classify as ONE of: {classes}

Return JSON:
{{
  "classification": "<one of the classes>",
  "confidence": "high|medium|low",
  "summary": "1 sentence summary of what they said",
  "next_action": "recommended next step",
  "needs_response": true/false
}}"""


@dataclass
class ResponseClassification:
    classification: str
    confidence: str
    summary: str
    next_action: str
    needs_response: bool


def classify_response(
    response_text: str,
    outreach_body: str = "",
    llm_cfg: LLMConfig | None = None,
) -> ResponseClassification:
    """Classify a prospect's response. Uses LLM if available, else keyword-based."""
    cfg = llm_cfg or load_config().llm

    if not cfg.enabled or cfg.max_calls < 1:
        return _classify_response_keywords(response_text)

    user_msg = RESPONSE_USER.format(
        outreach=outreach_body[:500] or "(not recorded)",
        response=response_text[:1000],
        classes=", ".join(RESPONSE_CLASSES),
    )

    messages = [
        {"role": "system", "content": RESPONSE_SYSTEM},
        {"role": "user", "content": user_msg},
    ]

    try:
        import os
        api_key = os.environ.get(cfg.api_key_env, "")
        if not api_key or cfg.provider == "none":
            return _classify_response_keywords(response_text)
        text, _ = _http_call(messages, cfg, api_key)
        data = _parse_strict_json(text)
        cls = data.get("classification", "unclear")
        if cls not in RESPONSE_CLASSES:
            cls = "unclear"
        return ResponseClassification(
            classification=cls,
            confidence=data.get("confidence", "low"),
            summary=data.get("summary", ""),
            next_action=data.get("next_action", "Review manually"),
            needs_response=data.get("needs_response", True),
        )
    except Exception:
        return _classify_response_keywords(response_text)


def _classify_response_keywords(text: str) -> ResponseClassification:
    """Keyword-based response classification (deterministic fallback)."""
    lower = text.lower()
    if any(w in lower for w in ("not interested", "no thank", "no thanks", "unsubscribe", "stop")):
        return ResponseClassification("not_interested", "medium", "Explicitly declined", "Do not contact again", False)
    if any(w in lower for w in ("price", "cost", "how much", "quote", "budget")):
        return ResponseClassification("pricing", "medium", "Asked about pricing", "Send quote", True)
    if any(w in lower for w in ("what", "how", "can you", "tell me", "explain")):
        return ResponseClassification("question", "medium", "Asked a question", "Answer their question", True)
    if any(w in lower for w in ("wrong person", "not the right", "wrong contact")):
        return ResponseClassification("wrong_contact", "medium", "Wrong contact", "Find correct contact", True)
    if any(w in lower for w in ("interested", "yes", "let's", "sounds good", "tell me more")):
        return ResponseClassification("positive", "medium", "Expressed interest", "Move to quote/proposal", True)
    if any(w in lower for w in ("later", "not now", "busy", "follow up")):
        return ResponseClassification("needs_more_information", "medium", "Deferred", "Schedule follow-up", True)
    if len(text.strip()) < 20:
        return ResponseClassification("unclear", "low", "Very short response", "Ask for clarification", True)
    return ResponseClassification("unclear", "low", "Could not determine intent", "Review manually", True)


# ---------------------------------------------------------------------------
# Quote Assistant
# ---------------------------------------------------------------------------

QUOTE_SYSTEM = (
    "You are a pricing assistant for a small tech services company. "
    "Generate a quote based on the provided scope and evidence. "
    "Be conservative and honest. Do not overpromise."
)

QUOTE_USER = """Service: {service_id}
Company: {company_name}

Scope: {scope}
Estimated files: {estimated_files}
Estimated pages: {estimated_pages}
Estimated rows: {estimated_rows}
Complexity: {complexity}

Evidence:
{evidence}

Suggested price range for this service: {price_range}

Generate a quote as JSON:
{{
  "suggested_price": <number>,
  "currency": "EUR",
  "delivery_estimate": "estimated delivery time",
  "assumptions": "what this quote assumes",
  "exclusions": "what is NOT included",
  "notes": "any additional notes"
}}"""


@dataclass
class QuoteSuggestion:
    suggested_price: float
    currency: str
    delivery_estimate: str
    assumptions: str
    exclusions: str
    notes: str
    llm_used: bool = False


# Price ranges per service (deterministic defaults)
_SERVICE_PRICE_RANGES = {
    "pdf-to-excel": {"min": 50, "max": 300, "per": "file"},
    "excel-cleaner": {"min": 30, "max": 200, "per": "file"},
    "qa-agent": {"min": 100, "max": 500, "per": "session"},
}


def _build_quote_deterministic(
    service_id: str,
    scope: str | None,
    estimated_files: int | None,
    estimated_pages: int | None,
    estimated_rows: int | None,
    complexity: str | None,
) -> QuoteSuggestion:
    """Build quote deterministically from scope parameters."""
    price_info = _SERVICE_PRICE_RANGES.get(service_id, {"min": 50, "max": 300, "per": "item"})
    base = price_info["min"]
    per = price_info["per"]

    # Adjust by complexity
    multiplier = {"simple": 1.0, "moderate": 1.5, "complex": 2.5}.get(complexity or "moderate", 1.5)

    # Adjust by volume
    volume = estimated_files or estimated_pages or 1
    if volume > 10:
        multiplier *= 0.85  # volume discount
    elif volume > 5:
        multiplier *= 0.9

    price = round(base * multiplier * max(1, volume), 2)
    price = max(price_info["min"], min(price, price_info["max"] * 3))

    assumptions_parts = []
    if estimated_files:
        assumptions_parts.append(f"{estimated_files} {per}(s)")
    if estimated_pages:
        assumptions_parts.append(f"~{estimated_pages} pages")
    if estimated_rows:
        assumptions_parts.append(f"~{estimated_rows} rows")
    if complexity:
        assumptions_parts.append(f"complexity: {complexity}")
    assumptions = "; ".join(assumptions_parts) if assumptions_parts else "standard scope"

    return QuoteSuggestion(
        suggested_price=price,
        currency="EUR",
        delivery_estimate="3-5 business days",
        assumptions=assumptions,
        exclusions="Complex formatting, urgent delivery (<24h), source data correction",
        notes="Final price confirmed after file review",
        llm_used=False,
    )


def generate_quote(
    service_id: str,
    company_name: str,
    scope: str | None = None,
    estimated_files: int | None = None,
    estimated_pages: int | None = None,
    estimated_rows: int | None = None,
    complexity: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
    llm_cfg: LLMConfig | None = None,
) -> QuoteSuggestion:
    """Generate a quote suggestion. Uses LLM if available, else deterministic."""
    cfg = llm_cfg or load_config().llm

    if not cfg.enabled or cfg.max_calls < 1:
        return _build_quote_deterministic(service_id, scope, estimated_files,
                                          estimated_pages, estimated_rows, complexity)

    price_range = _SERVICE_PRICE_RANGES.get(service_id, {"min": 50, "max": 300})
    evidence_text = "\n".join(
        f"- {e.get('kind')}: {(e.get('excerpt') or '')[:60]}"
        for e in (evidence or [])[:5]
    ) or "No evidence"

    user_msg = QUOTE_USER.format(
        service_id=service_id,
        company_name=company_name,
        scope=scope or "standard",
        estimated_files=estimated_files or "unknown",
        estimated_pages=estimated_pages or "unknown",
        estimated_rows=estimated_rows or "unknown",
        complexity=complexity or "moderate",
        evidence=evidence_text,
        price_range=f"EUR {price_range['min']}-{price_range['max']}",
    )

    messages = [
        {"role": "system", "content": QUOTE_SYSTEM},
        {"role": "user", "content": user_msg},
    ]

    try:
        import os
        api_key = os.environ.get(cfg.api_key_env, "")
        if not api_key or cfg.provider == "none":
            return _build_quote_deterministic(service_id, scope, estimated_files,
                                              estimated_pages, estimated_rows, complexity)
        text, _ = _http_call(messages, cfg, api_key)
        data = _parse_strict_json(text)
        return QuoteSuggestion(
            suggested_price=float(data.get("suggested_price", 100)),
            currency=data.get("currency", "EUR"),
            delivery_estimate=data.get("delivery_estimate", "3-5 business days"),
            assumptions=data.get("assumptions", "standard scope"),
            exclusions=data.get("exclusions", "Complex formatting, urgent delivery"),
            notes=data.get("notes", ""),
            llm_used=True,
        )
    except Exception:
        return _build_quote_deterministic(service_id, scope, estimated_files,
                                          estimated_pages, estimated_rows, complexity)
