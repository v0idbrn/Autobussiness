"""Deterministic commercial signal engine (Discovery V2).

Pure functions over already-fetched page signals/text. No LLM, no network.

Purpose: turn raw page text into WEIGHTED commercial signals per BAM service
(pdf_to_excel, excel_cleaning, qa_automation), detect publishers/aggregators
that must never compete as company leads, and score contactability from
OBSERVED signals only. This is the deterministic layer that decides whether a
candidate is clearly good, clearly bad, or ambiguous enough to warrant the
optional Gemini opinion (bam/gemini.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

# -- service catalog -------------------------------------------------------------

SERVICE_IDS = ("pdf_to_excel", "excel_cleaning", "qa_automation")

# Query catalog: small, measurable, per service (Discovery V2 §2).
QUERY_CATALOG: dict[str, tuple[str, ...]] = {
    "pdf_to_excel": (
        "invoice processing services company",
        "document processing company",
        "accounts payable outsourcing company",
        "PDF data extraction services",
        "back office document processing company",
    ),
    "excel_cleaning": (
        "data entry services company",
        "spreadsheet data processing company",
        "data cleansing services company",
        "CSV data processing services",
        "data migration services company",
    ),
    "qa_automation": (
        "QA automation company",
        "software testing agency",
        "web application testing services",
        "software quality assurance company",
    ),
}

# Weighted patterns per service. strong = offering language (weight 3),
# medium = domain vocabulary (weight 1). Case-insensitive substring match on
# normalized text. Deliberately small: quality over coverage.
_PATTERNS: dict[str, dict[str, tuple[str, ...]]] = {
    "pdf_to_excel": {
        "strong": (
            "invoice processing", "document processing", "accounts payable",
            "pdf data extraction", "document data extraction",
            "data extraction services", "invoice automation",
            "document management services", "back office document",
            "accounts receivable automation",
        ),
        "medium": (
            "ocr", "invoice", "financial documents", "document scanning",
            "paperless", "ap automation",
        ),
    },
    "excel_cleaning": {
        "strong": (
            "data cleansing services", "data cleaning services",
            "excel data cleaning", "csv data processing",
            "data entry services", "data migration services",
            "spreadsheet data processing", "data processing services",
        ),
        "medium": (
            "data entry", "data cleansing", "data migration", "spreadsheet",
            "csv", "reporting services",
        ),
    },
    "qa_automation": {
        "strong": (
            "qa automation", "test automation", "automation testing",
            "software testing services", "regression testing",
            "quality assurance services", "web application testing",
        ),
        "medium": (
            "quality assurance", "software testing", "qa testing",
            "manual testing", "unit testing",
        ),
    },
}

STRONG_WEIGHT = 3
MEDIUM_WEIGHT = 1


def query_catalog() -> dict[str, list[str]]:
    """Copy of the per-service query catalog (small and measurable by design;
    see QUERY_CATALOG). Tests and callers get a defensive copy."""
    return {k: list(v) for k, v in QUERY_CATALOG.items()}

# Ambiguity band: below LOW a candidate is clearly bad (no Gemini, no lead);
# above HIGH it is clearly good (no Gemini needed); inside the band the
# optional Gemini opinion may be requested.
AMBIGUOUS_LOW = 2
AMBIGUOUS_HIGH = 6


# -- text normalization -----------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


# -- publisher / aggregator detection (§4) ----------------------------------------

# Hostname tokens that indicate a publisher/aggregator. Matched on hyphen/dot
# separated parts, not substrings ("times" must not match "timestamps").
_PUBLISHER_HOST_TOKENS = frozenset({
    "news", "times", "post", "press", "wire", "newswire", "daily", "journal",
    "magazine", "media", "tv", "radio", "herald", "tribune", "gazette",
    "observer", "insider", "digest", "chronicle", "express", "courier",
    "beat", "report", "reports",
})

# Known aggregator/marketplace/wire-service parts we actually hit in the first
# campaign plus the canonical press wires.
_AGGREGATOR_HOST_PARTS = frozenset({
    "forbes", "bloomberg", "reuters", "yahoo", "finance.yahoo", "marketwatch",
    "coursera", "udemy", "builtin", "builtinchicago", "clutch", "goodfirms",
    "designrush", "upwork", "fiverr", "thumbtack", "marketgrowthreports",
    "technology.org", "itbrief", "nasscom", "cioreview", "cio",
    "cpapracticeadvisor", "accountingtoday", "goingconcern", "wolterskluwer",
    "natlawreview", "theguardian", "dailymemphian", "calcalistech",
    "onlinemarketplaces", "qa-financial", "ft", "intuit", "businessinsider",
    "businesswire", "prnewswire", "globenewswire", "einpresswire",
    "accessnewswire", "prweb", "newswire",
    # app stores & affiliate networks: platforms, not companies that buy services
    "apps.apple", "play.google", "awin1", "clickbank", "shareasale",
})

# Generic title patterns (publisher mastheads / wire bylines).
_PUBLISHER_TITLE_PATTERNS = (
    re.compile(r"\b(wire|news|press release|magazine|gazette|herald|tribune)\b", re.I),
    re.compile(r"\b(market research|market size|industry forecast)\b", re.I),
)

_SOCIAL_HOST_PARTS = frozenset({
    "linkedin", "facebook", "instagram", "x", "twitter", "reddit", "quora",
    "medium", "substack", "youtube", "github", "stackoverflow", "wikipedia",
})

DIRECTORY_HOST_TOKENS = frozenset({"directory", "directories", "yellowpages", "yelp"})


@dataclass
class CandidateType:
    """Classification of a candidate source. `publisher`/`aggregator`/
    `directory`/`social` candidates never compete as commercial leads."""
    kind: str                      # company | publisher | aggregator | directory | social
    reason: str


def classify_candidate(hostname: str, *, title: str | None = None) -> CandidateType:
    """Deterministic publisher/aggregator detection (V2 §4).

    Compact: hostname token/parts + generic title patterns. NOT an infinite
    manual list; unknown domains default to kind='company' and are decided by
    evidence downstream.
    """
    host = (hostname or "").lower().removeprefix("www.")
    parts = {p for p in re.split(r"[.\-_]", host) if p}
    # multi-part names with dots (finance.yahoo)
    if host in _SOCIAL_HOST_PARTS or host.split(".")[0] in _SOCIAL_HOST_PARTS:
        return CandidateType("social", f"social/knowledge host: {host}")
    labels = host.split(".")
    pairs = {f"{labels[i]}.{labels[i + 1]}" for i in range(len(labels) - 1)}
    if any(p in _AGGREGATOR_HOST_PARTS for p in (host, *parts, *pairs)):
        return CandidateType("aggregator", f"known aggregator/publisher host: {host}")
    pub_tokens = parts & _PUBLISHER_HOST_TOKENS
    if pub_tokens:
        return CandidateType("publisher", f"publisher hostname token: {sorted(pub_tokens)}")
    if any(t in DIRECTORY_HOST_TOKENS for t in parts):
        return CandidateType("directory", f"directory host: {host}")
    if title:
        for pat in _PUBLISHER_TITLE_PATTERNS:
            m = pat.search(title)
            if m:
                return CandidateType("publisher", f"title pattern: {m.group(0).lower()}")
    return CandidateType("company", "no publisher/aggregator pattern")


# -- weighted commercial signals (§3) ----------------------------------------------

@dataclass
class PageSignals:
    """Weighted commercial signals found on ONE fetched page."""
    url: str
    is_homepage: bool
    matches: list[dict[str, str]] = field(default_factory=list)  # service,tier,phrase
    scores: dict[str, int] = field(default_factory=dict)

    @property
    def strong_hit(self) -> bool:
        return any(m["tier"] == "strong" for m in self.matches)


def extract_page_signals(html: str, *, url: str, is_homepage: bool) -> PageSignals:
    """Weighted phrase scan over one fetched page (pure, deterministic).

    Accepts raw HTML: visible text + title + meta description are extracted
    locally (no network). Scripts/styles are excluded by the shared parser.
    """
    from bam.extractors import parse_html

    parser = parse_html(html)
    parts = [parser.title or ""]
    parts.append(parser.meta.get("description") or parser.meta.get("og:description") or "")
    parts.extend(parser.text_parts)
    t = _norm(" ".join(parts))
    ps = PageSignals(url=url, is_homepage=is_homepage)
    for service, tiers in _PATTERNS.items():
        score = 0
        for tier, weight in (("strong", STRONG_WEIGHT), ("medium", MEDIUM_WEIGHT)):
            for phrase in tiers[tier]:
                if phrase in t:
                    score += weight
                    ps.matches.append({"service": service, "tier": tier,
                                       "phrase": phrase})
        ps.scores[service] = score
    return ps


@dataclass
class CommercialProfile:
    """Aggregated deterministic commercial view of one candidate (all pages)."""
    domain: str
    pages_scanned: int = 0
    homepage_strong: bool = False
    matches: list[dict[str, str]] = field(default_factory=list)
    service_scores: dict[str, int] = field(default_factory=dict)
    pages_matched: dict[str, int] = field(default_factory=dict)
    recommended_service: str | None = None
    base_score: int = 0          # evidence breadth: sum of raw page matches
    total_score: int = 0         # ranking score: base + homepage-strong bonus
    ambiguous: bool = False
    candidate_type: CandidateType | None = None
    contactability: dict[str, Any] = field(default_factory=dict)

    def as_signals_dict(self) -> dict[str, Any]:
        """Compact dict for profile.json / signals storage (no raw HTML)."""
        return {
            "service_scores": dict(self.service_scores),
            "pages_matched": dict(self.pages_matched),
            "recommended_service": self.recommended_service,
            "base_score": self.base_score,
            "total_score": self.total_score,
            "ambiguous": self.ambiguous,
            "homepage_strong": self.homepage_strong,
            "candidate_type": self.candidate_type.kind if self.candidate_type else None,
            "candidate_reason": self.candidate_type.reason if self.candidate_type else None,
            "phrases": [m["phrase"] for m in self.matches][:20],
            "contactability": self.contactability,
        }


def aggregate_commercial(
    domain: str,
    page_signals: list[PageSignals],
    *,
    contact: dict[str, Any] | None = None,
    title: str | None = None,
) -> CommercialProfile:
    """Fold per-page signals into a CommercialProfile (pure)."""
    cp = CommercialProfile(domain=domain)
    cp.pages_scanned = len(page_signals)
    cp.matches = [m for ps in page_signals for m in ps.matches]
    for service in SERVICE_IDS:
        cp.service_scores[service] = sum(ps.scores.get(service, 0) for ps in page_signals)
        cp.pages_matched[service] = sum(
            1 for ps in page_signals if ps.scores.get(service, 0) > 0)
    cp.homepage_strong = any(
        ps.is_homepage and ps.strong_hit for ps in page_signals)
    # homepage strong evidence doubles as the deciding weight (V2 §3: homepage
    # evidence outranks subpage-only evidence)
    if cp.homepage_strong:
        for ps in page_signals:
            if ps.is_homepage:
                for m in ps.matches:
                    if m["tier"] == "strong":
                        cp.service_scores[m["service"]] += STRONG_WEIGHT
    top = max(cp.service_scores, key=lambda s: cp.service_scores[s])
    # base_score = raw evidence sum BEFORE the homepage bonus (ambiguity must
    # reflect how much evidence exists, not where it sits)
    cp.base_score = sum(
        sum(ps.scores.get(s, 0) for ps in page_signals) for s in SERVICE_IDS)
    cp.total_score = cp.service_scores[top]
    cp.recommended_service = top if cp.total_score > 0 else None
    cp.ambiguous = AMBIGUOUS_LOW <= cp.base_score <= AMBIGUOUS_HIGH
    cp.candidate_type = classify_candidate(domain, title=title)
    cp.contactability = score_contactability(contact or {})
    return cp


# -- contactability (§5) -----------------------------------------------------------

_SALES_EMAIL_LOCALPARTS = ("info", "sales", "contact", "hello", "office", "admin")


def score_contactability(signals: dict[str, Any]) -> dict[str, Any]:
    """0-6 contactability from OBSERVED signals only. Never invents addresses."""
    score = 0
    emails = signals.get("emails") or []
    if emails:
        score += 2
        if any(e.split("@", 1)[0].lower() in _SALES_EMAIL_LOCALPARTS for e in emails):
            score += 1
    if signals.get("has_contact_form"):
        score += 1
    if signals.get("contact_url"):
        score += 1
    if (signals.get("mailto_count") or 0) > 0:
        score += 1
    level = "low" if score <= 1 else ("medium" if score <= 3 else "high")
    return {"score": score, "level": level,
            "has_email": bool(emails), "has_contact_page": bool(signals.get("contact_url")),
            "has_form": bool(signals.get("has_contact_form"))}


# -- ambiguity gate for the optional Gemini layer (§7) ------------------------------

def should_ask_gemini(cp: CommercialProfile) -> bool:
    """Only near-threshold, non-publisher, evidence-bearing candidates qualify.
    Clear wins, clear rejects, publishers and zero-signal pages never cost tokens."""
    if cp.candidate_type and cp.candidate_type.kind != "company":
        return False
    if cp.pages_scanned == 0 or cp.total_score == 0:
        return False
    return cp.ambiguous
