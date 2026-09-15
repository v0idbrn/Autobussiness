"""Deterministic homepage signal extraction (plan v3.1 §3 step 4).

No LLM, no network: pure functions over fetched HTML. Output is a signals
dict consumed by the profile builder and the scorer. Claims produced here are
OBSERVED (directly seen in the fetched page).

Closed enumerations (anti-hallucination per plan §6):
- services: data-cleaning | pdf-extraction | qa-automation | web-dev | other
- problems: manual-excel-work | paper-forms | no-tests | slow-reporting | unknown
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any

from bam.evidence import redact

SERVICE_ENUM = ("data-cleaning", "pdf-extraction", "qa-automation", "web-dev", "other")
PROBLEM_ENUM = ("manual-excel-work", "paper-forms", "no-tests", "slow-reporting", "unknown")

_TECH_PATTERNS = {
    "wordpress": ("wp-content", "wp-includes"),
    "shopify": ("cdn.shopify.com", "shopify"),
    "wix": ("wixstatic", "wix.com"),
    "squarespace": ("squarespace",),
    "react": ("react", "__NEXT_DATA__"),
    "next.js": ("__NEXT_DATA__", "/_next/"),
    "vue": ("vue",),
    "hubspot": ("hs-scripts", "hubspot"),
    "google-analytics": ("googletagmanager", "google-analytics"),
    "mailchimp": ("mailchimp", "mc.us"),
    "intercom": ("intercom",),
    "webflow": ("webflow",),
}

# Keyword scans (case-insensitive) mapped to closed-enum findings.
_SERVICE_KEYWORDS = {
    "data-cleaning": ("spreadsheet", "excel", "csv", "hoja de cálculo", "hoja de calculo", "planilla"),
    "pdf-extraction": ("pdf", "facturas", "invoices", "extractos", "bank statement"),
    "qa-automation": ("testing", "qa", "quality assurance", "playwright", "automated tests"),
    "web-dev": ("web app", "development", "software house", "agencia", "desarrollo web"),
}
_PROBLEM_KEYWORDS = {
    "manual-excel-work": ("manual data entry", "spreadsheet hell", "copy paste", "repetitive"),
    "paper-forms": ("paper forms", "printed forms", "formularios", "escanear"),
    "no-tests": ("without tests", "sin pruebas", "legacy code", "deuda técnica", "deuda tecnica"),
    "slow-reporting": ("monthly report", "reporting takes", "informes mensuales"),
}
_CONTACT_WORDS = ("contact", "contacto", "kontakt", "contact us", "hablemos")

_NAV_HINTS = (
    "pricing", "precios", "about", "nosotros", "contact", "contacto",
    "blog", "careers", "empleo", "services", "servicios",
)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.jsonld_raw: list[str] = []
        self._in_jsonld = False
        self._jsonld_buf: list[str] = []
        self.links: list[tuple[str, str]] = []          # (href, text)
        self._href: str | None = None
        self._link_text: list[str] = []
        self.mailto: list[str] = []
        self.form_count = 0
        self.text_parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = a.get("name") or a.get("property") or a.get("itemprop")
            if key and a.get("content"):
                self.meta[key.lower()] = a["content"]
        elif tag == "script" and a.get("type") == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_buf = []
        elif tag == "a":
            self._href = a.get("href")
            self._link_text = []
        elif tag == "form":
            self.form_count += 1
        elif tag in ("script", "style", "noscript"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            self.jsonld_raw.append("".join(self._jsonld_buf))
        elif tag == "a":
            if self._href is not None:
                text = " ".join("".join(self._link_text).split())
                self.links.append((self._href, text))
            self._href = None
            self._link_text = []
        elif tag in ("script", "style", "noscript") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = (self.title or "") + data
        if self._in_jsonld:
            self._jsonld_buf.append(data)
        if self._href is not None:
            self._link_text.append(data)
        if self._skip_depth == 0:
            self.text_parts.append(data)


def parse_html(html: str) -> _PageParser:
    parser = _PageParser()
    try:
        parser.feed(html)
    except Exception:
        # Malformed HTML: keep whatever was parsed (evidence is still captured).
        pass
    return parser


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def extract_jsonld_organization(parser: _PageParser) -> dict[str, Any]:
    """First Organization-like JSON-LD node (tolerant: malformed blocks skipped)."""
    for raw in parser.jsonld_raw:
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for node in candidates:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph")
            if isinstance(graph, list):
                candidates.extend(g for g in graph if isinstance(g, dict))
            t = node.get("@type", "")
            types = t if isinstance(t, list) else [t]
            if any(str(x).lower() in ("organization", "localbusiness", "professional service",
                                      "professionalservice", "corporation", "person")
                   for x in types):
                return {
                    "name": node.get("name"),
                    "email": node.get("email"),
                    "telephone": node.get("telephone"),
                    "same_as": node.get("sameAs") if isinstance(node.get("sameAs"), list)
                    else ([node["sameAs"]] if node.get("sameAs") else []),
                    "description": node.get("description"),
                }
    return {}


def extract_tech(parser: _PageParser) -> list[str]:
    """Technology hints from generator meta + raw HTML markers (OBSERVED)."""
    found: list[str] = []
    generator = parser.meta.get("generator", "").lower()
    if generator:
        found.append(generator.split()[0])
    raw = " ".join(parser.text_parts) + " " + " ".join(h for h, _ in parser.links)
    # rescan original html markers cheaply: parse() drops scripts, so also check meta
    for tech, markers in _TECH_PATTERNS.items():
        if any(m in generator for m in markers):
            if tech not in found:
                found.append(tech)
    return found


def extract_links(parser: _PageParser) -> dict[str, Any]:
    nav = []
    contact_url = None
    pricing_url = None
    for href, text in parser.links:
        low = (href + " " + text).lower()
        if any(h in low for h in _NAV_HINTS):
            nav.append(href)
        if contact_url is None and ("contact" in low or "contacto" in low):
            contact_url = href
        if pricing_url is None and ("pricing" in low or "precios" in low):
            pricing_url = href
    return {"nav_links": nav[:20], "contact_url": contact_url, "pricing_url": pricing_url}


def keyword_signals(parser: _PageParser) -> dict[str, list[str]]:
    text = _norm(" ".join(parser.text_parts))
    services = [s for s, kws in _SERVICE_KEYWORDS.items() if any(k in text for k in kws)]
    problems = [p for p, kws in _PROBLEM_KEYWORDS.items() if any(k in text for k in kws)]
    return {"services": services, "problems": problems}


def extract_signals(html: str, *, keep_emails: bool = True) -> dict[str, Any]:
    """Full deterministic extraction from one homepage.

    keep_emails=True: contact emails are the signal we are looking for, so they
    survive inside structured fields; they are still redacted in evidence excerpts.
    """
    parser = parse_html(html)
    title = (parser.title or "").strip() or None
    description = parser.meta.get("description") or parser.meta.get("og:description")
    org = extract_jsonld_organization(parser)
    emails = list(dict.fromkeys(re.findall(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", html)))[:5]
    if not keep_emails:
        emails = []
    tech = extract_tech(parser)
    html_lower = html.lower()
    for t, markers in _TECH_PATTERNS.items():
        if t not in tech and any(m in html_lower for m in markers):
            tech.append(t)
    links = extract_links(parser)
    kw = keyword_signals(parser)
    return {
        "title": redact(title) if title else None,
        "meta_description": redact(description) if description else None,
        "organization": org,
        "emails": [redact(e, keep_emails=True) for e in emails],
        "technologies": list(dict.fromkeys(tech))[:12],
        "has_contact_form": parser.form_count > 0,
        "mailto_count": sum(1 for h, _ in parser.links if h.lower().startswith("mailto:")),
        **links,
        "keyword_services": kw["services"],
        "keyword_problems": kw["problems"],
    }
