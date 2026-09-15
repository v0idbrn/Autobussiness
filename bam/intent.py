"""Commercial Intent Engine: find PUBLIC EXPRESSIONS OF NEED, not companies.

A company matching our industry is weak signal. A public post asking for
help with something we sell is a strong signal. This module is pure and
deterministic: no LLM, no network (sources feed it normalized items),
untrusted text is DATA only - phrases here are match patterns, never
instructions to execute.

Intent strength (§8) is a priority signal, not a conversion probability:
  100 explicit  - a public request for help with our service
  80  strong    - hiring a contractor / outsourcing our service
  60  medium    - hiring an in-house role that does what we automate
  40  weak      - company merely performs the activity
   0 none       - industry-only match
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# -- intent phrase catalog (§3-§5) ------------------------------------------------
# Each entry: (tier, regex). "explicit" needs a request-for-help verb PLUS a
# service noun phrase nearby; the combined patterns below enforce that context
# so a page merely *mentioning* Excel never scores as intent.

_SERVICE_NOUNS = {
    "pdf_to_excel": (
        r"pdf\s*(?:to|a|->|→)\s*(?:excel|xlsx|spreadsheet)",
        r"pdf\w*\s+(?:\w+\s+){0,2}(?:to|a|hacia)\s+(?:excel|xlsx|spreadsheet)",
        r"convert\w*\s+(?:\w+\s+){0,2}pdfs?\b",
        r"pdf\s+(?:data\s+)?(?:extraction|conversion|processing)",
        r"invoice\s+(?:processing|data\s+entry|extraction)",
        r"facturas?\s+(?:a\s+|en\s+)?(?:excel|datos)",
        r"procesamiento\s+de\s+(?:pdfs?|documentos|facturas)",
        r"extracci[oó]n\s+de\s+datos\s+de\s+pdf",
    ),
    "excel_cleaning": (
        r"(?:excel|csv|spreadsheet)s?\s+(?:clean\w*|cleanup|normaliz\w*|validat\w*|fix\w*|tidy)",
        r"(?:clean\w*|normaliz\w*|dedup\w*|tidy\w*|fix)\s+(?:\w+\s+){0,3}(?:excel|csv|spreadsheet)s?\b",
        r"data\s+clean\w*|cleansing|limpieza\s+de\s+datos",
        r"data\s+migration|migraci[oó]n\s+de\s+datos",
        r"(?:duplicates?|dedup\w*)\s+(?:in\s+)?(?:excel|csv|spreadsheet|data)",
        r"spreadsheet\s+(?:data\s+)?(?:processing|management|audit)",
        r"csv\s+(?:processing|conversion|cleanup)",
        r"limpiar\s+(?:planillas?|excel|csv)",
    ),
    "qa_automation": (
        r"(?:qa|quality\s+assurance)\s+(?:automation|engineer|tester|testing)",
        r"automation\s+testing|regression\s+testing|pruebas\s+automatizadas",
        r"(?:web|website|webapp|saas)\s+(?:app\s+)?testing",
        r"test\s+automation|automatizaci[oó]n\s+de\s+(?:pruebas|tests)",
        r"software\s+tester\b",
    ),
}

_REQUEST_VERBS = (
    r"looking\s+for(?:\s+(?:someone|help|a\s+freelancer|a\s+contractor|assistance))?",
    r"need\s+(?:help|assistance|someone)|need(?:s)?\s+a?\s*(?:freelancer|contractor)",
    r"seeking|seeking\s+help",
    r"(?:we\s+)?are\s+hiring|hire\s+(?:a|an|someone)|hiring\b",
    r"outsourc\w+|contractor\s+needed|freelancer\s+needed",
    r"request\s+for\s+proposal|\brfp\b",
    r"buscamos|buscando(?:\s+(?:freelancer|alguien|ayuda))?",
    r"necesitamos|necesito|necesita(?:mos)?\s+ayuda",
    r"contratar|contrataci[oó]n\s+de",
    r"busco\s+(?:alguien|proveedor|servicio)",
)

_INHOUSE_ROLES = (
    r"(?:data\s+entry|data\s+processing)\s+(?:specialist|clerk|operator|assistant)",
    r"(?:qa|test)\s+engineer",
    r"spreadsheet\s+specialist|excel\s+specialist|analista\s+de\s+datos",
    r"back\s+office\s+(?:assistant|specialist|clerk)",
    r"operations\s+assistant|asistente\s+administrativ\w+",
    r"encargad\w+\s+de\s+administraci[oó]n",
)

_WEAK_CONTEXT = (
    r"we\s+(?:perform|offer|provide|do)\s+(?:data\s+processing|document\s+processing)",
    r"(?:perform|offers?|provides?|do|does)\s+(?:data|document)\s+processing",
    r"(?:data|document)\s+processing\s+services",
    r"servicios\s+de\s+(?:procesamiento|procesos)\s+de\s+datos",
    r"(?:monthly|weekly|daily)\s+(?:excel|spreadsheet|report\w*)\s+(?:processing|reports?)",
)


@dataclass(frozen=True)
class IntentMatch:
    tier: str            # explicit | strong | medium | weak | none
    score: int           # 100 / 80 / 60 / 40 / 0
    service: str | None  # recommended service id, if determinable
    phrases: tuple[str, ...]


_TIER_SCORE = {"explicit": 100, "strong": 80, "medium": 60, "weak": 40, "none": 0}


def _compile(patterns: tuple[str, ...]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.I) for p in patterns]


def score_intent(text: str) -> IntentMatch:
    """Deterministic intent scoring over untrusted text (§8).

    Tier logic, first match wins:
      explicit - a request-for-help verb within ±120 chars of a service noun
      strong   - request verb AND service noun both present anywhere
      medium   - in-house role hire (the role does what we automate)
      weak     - company merely performs the activity
    """
    if not text or not text.strip():
        return IntentMatch("none", 0, None, ())

    # untrusted text is data: cap the scanned window to keep regex work bounded
    text = text[:20000]

    request = _compile(_REQUEST_VERBS)
    has_request = any(r.search(text) for r in request)
    best: IntentMatch | None = None
    for service, nouns in _SERVICE_NOUNS.items():
        noun_hit = None
        explicit_hit = None
        for n in _compile(nouns):
            for nm in n.finditer(text):
                if noun_hit is None:
                    noun_hit = nm.group(0)[:80]
                if explicit_hit is None:
                    window = text[max(0, nm.start() - 120):nm.end() + 120]
                    if has_request and any(r.search(window) for r in request):
                        explicit_hit = nm.group(0)[:80]
        if explicit_hit:
            return IntentMatch("explicit", 100, service, (explicit_hit,))
        if noun_hit and has_request and best is None:
            best = IntentMatch("strong", 80, service, (noun_hit,))
    if best:
        return best
    # medium: in-house role hires
    role_pats = _compile(_INHOUSE_ROLES)
    for r in role_pats:
        m = r.search(text)
        if m:
            service = _service_from_role(m.group(0))
            return IntentMatch("medium", 60, service, (m.group(0)[:80],))
    # weak: the company merely performs the activity
    for w in _compile(_WEAK_CONTEXT):
        m = w.search(text)
        if m:
            return IntentMatch("weak", 40, None, (m.group(0)[:80],))
    return IntentMatch("none", 0, None, ())


def _service_from_role(role: str) -> str | None:
    rl = role.lower()
    if "qa" in rl or "test" in rl:
        return "qa_automation"
    if "spreadsheet" in rl or "excel" in rl or "datos" in rl:
        return "excel_cleaning"
    if "data entry" in rl or "data processing" in rl:
        return "pdf_to_excel"
    return None


# -- freshness (§11, §18) ----------------------------------------------------------

def freshness(age_days: int | None) -> str:
    """Freshness bucket from a CONFIRMED publication age. Unknown stays unknown."""
    if age_days is None:
        return "unknown"
    if age_days <= 3:
        return "very_high"
    if age_days <= 7:
        return "high"
    if age_days <= 30:
        return "medium"
    if age_days <= 90:
        return "low"
    return "very_low"


_FRESHNESS_RANK = {"very_high": 0, "high": 1, "medium": 2, "low": 3,
                   "very_low": 4, "unknown": 5}


def age_days_from(published_at: str | None, *, now: datetime | None = None) -> int | None:
    """Age in whole days from an ISO timestamp. None if absent/unparseable."""
    if not published_at:
        return None
    try:
        ts = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0, (now - ts).days)


def is_expired(published_at: str | None, *, max_age_days: int = 90) -> bool:
    """Only when a reliable date exists (§18). Undated items never auto-expire."""
    age = age_days_from(published_at)
    return age is not None and age > max_age_days


# -- normalized opportunity (§9, §24) ----------------------------------------------

@dataclass
class OpportunityCandidate:
    company: str | None
    company_url: str | None
    source_url: str
    source_type: str            # rss | job_board | directory | manual | ...
    published_at: str | None
    title: str
    snippet: str
    intent: IntentMatch
    evidence: list[dict[str, Any]] = field(default_factory=list)

    @property
    def dedup_key(self) -> tuple[str | None, str, str]:
        """(company, normalized intent text, source host) - §19."""
        from urllib.parse import urlparse
        host = urlparse(self.source_url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        norm = re.sub(r"[^a-z0-9]+", " ", (self.title or self.snippet).lower()).strip()
        return (self.company, norm[:140], host)


def deduplicate_candidates(cands: list[OpportunityCandidate]) -> list[OpportunityCandidate]:
    """Same need may appear on many boards; keep first occurrence, never
    destroy evidence - later duplicates only ADD their source to the kept one."""
    kept: dict[tuple, OpportunityCandidate] = {}
    order: list[tuple] = []
    for c in cands:
        k = c.dedup_key
        if k in kept:
            prev = kept[k]
            if c.source_url not in [e.get("url") for e in prev.evidence]:
                prev.evidence.append({
                    "kind": "duplicate_source", "status": "OBSERVED",
                    "url": c.source_url, "excerpt": c.title[:160],
                })
        else:
            kept[k] = c
            order.append(k)
    return [kept[k] for k in order]
