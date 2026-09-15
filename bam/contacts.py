"""Contact discovery: extract contacts from company web pages.

Extracts emails, contact pages, team pages, and form data from:
- Homepage (already done by extractors)
- /contact pages
- /about pages
- /team pages

All extraction is deterministic. No inference of person names/roles from
non-structured data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from urllib.parse import urlparse

from bam.evidence import redact
from bam.extractors import parse_html
from bam.fetcher import FetchError, Fetcher

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LINKEDIN_RE = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_-]{3,}")


def extract_linkedin_urls(html: str) -> list[str]:
    """Extract LinkedIn profile URLs from HTML. Never visits the profiles."""
    urls = list(dict.fromkeys(
        m.group(0).rstrip(".,);\"'>") for m in _LINKEDIN_RE.finditer(html)))
    return urls[:10]


@dataclass
class DiscoveredContact:
    name: str | None
    role: str | None
    email: str | None
    phone: str | None
    source: str
    evidence: str
    confidence: str  # observed | inferred | unknown


def extract_contacts_from_html(html: str, url: str) -> list[DiscoveredContact]:
    """Extract contacts from a page's HTML deterministically."""
    parser = parse_html(html)
    contacts: list[DiscoveredContact] = []

    # Emails from page
    emails = list(dict.fromkeys(re.findall(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", html)))[:5]

    # Phone numbers (international formats)
    phones = list(dict.fromkeys(re.findall(
        r"(?:\+\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}", html)))[:3]

    # JSON-LD Organization contacts
    from bam.extractors import extract_jsonld_organization
    org = extract_jsonld_organization(parser)
    if org.get("email") and org["email"] not in emails:
        emails.insert(0, org["email"])
    if org.get("telephone") and org["telephone"] not in phones:
        phones.insert(0, org["telephone"])

    # Create contacts from extracted data
    for email in emails[:3]:
        contacts.append(DiscoveredContact(
            name=None,
            role=None,
            email=redact(email, keep_emails=True),
            phone=None,
            source=url,
            evidence=f"email found on page",
            confidence="observed",
        ))

    for phone in phones[:2]:
        contacts.append(DiscoveredContact(
            name=None,
            role=None,
            email=None,
            phone=phone,
            source=url,
            evidence=f"phone found on page",
            confidence="observed",
        ))

    # LinkedIn profile URLs: observed on the page, never visited (auth-walled).
    for li_url in extract_linkedin_urls(html)[:3]:
        contacts.append(DiscoveredContact(
            name=None,
            role=None,
            email=None,
            phone=None,
            source=url,
            evidence=f"LinkedIn profile found on team page: {li_url}",
            confidence="observed",
        ))

    return contacts


def discover_contacts_from_page(
    url: str,
    *,
    fetcher: Fetcher | None = None,
    timeout_s: float = 30,
) -> list[DiscoveredContact]:
    """Fetch a page and extract contacts from it."""
    should_close = False
    if fetcher is None:
        fetcher = Fetcher()
        should_close = True

    try:
        res = fetcher.fetch(url)
        if res.status != 200:
            return []
        html = res.content.decode("utf-8", errors="replace")
        return extract_contacts_from_html(html, url)
    except (FetchError, Exception):
        return []
    finally:
        if should_close:
            fetcher.close()


def discover_contacts_for_lead(
    base_url: str,
    *,
    fetcher: Fetcher | None = None,
) -> list[DiscoveredContact]:
    """Discover contacts from multiple pages of a company website.

    Tries:
    1. Homepage (base URL)
    2. /contact
    3. /about
    4. /team
    5. /about-us
    """
    from urllib.parse import urljoin

    pages_to_try = [
        base_url,
        urljoin(base_url, "/contact"),
        urljoin(base_url, "/contacto"),
        urljoin(base_url, "/about"),
        urljoin(base_url, "/about-us"),
        urljoin(base_url, "/team"),
    ]

    all_contacts: list[DiscoveredContact] = []
    seen_emails: set[str] = set()
    seen_phones: set[str] = set()

    for page_url in pages_to_try:
        contacts = discover_contacts_from_page(page_url, fetcher=fetcher)
        for c in contacts:
            if c.email and c.email not in seen_emails:
                seen_emails.add(c.email)
                all_contacts.append(c)
            elif c.phone and c.phone not in seen_phones:
                seen_phones.add(c.phone)
                all_contacts.append(c)
            elif not c.email and not c.phone:
                # LinkedIn URLs and other evidence-only contacts: dedupe by evidence.
                if not any(existing.evidence == c.evidence for existing in all_contacts):
                    all_contacts.append(c)

    # WHOIS fallback: only when web pages yielded no email (1 request max).
    if not seen_emails:
        domain = urlparse(base_url).netloc
        if domain:
            all_contacts.extend(discover_contacts_from_whois(domain, fetcher=fetcher))

    return all_contacts


def discover_contacts_from_whois(
    domain: str,
    *,
    fetcher: Fetcher | None = None,
) -> list[DiscoveredContact]:
    """Registrant email from whois.com's web view (1 request; skip on failure).

    Deterministic: only emails found inside the Registrant section are taken.
    """
    should_close = False
    if fetcher is None:
        fetcher = Fetcher()
        should_close = True
    whois_url = f"https://www.whois.com/whois/{domain}"
    try:
        try:
            res = fetcher.fetch(whois_url)
        except Exception:
            return []
        if res.status != 200:
            return []
        html = res.content.decode("utf-8", errors="replace")
        text = re.sub(r"<[^>]+>", " ", html)
        match = re.search(r"Registrant", text, flags=re.IGNORECASE)
        if not match:
            return []
        window = text[match.start():match.start() + 3000]
        emails = list(dict.fromkeys(_EMAIL_RE.findall(window)))
        if not emails:
            return []
        return [DiscoveredContact(
            name=None,
            role=None,
            email=redact(emails[0], keep_emails=True),
            phone=None,
            source=whois_url,
            evidence="registrant email from whois.com",
            confidence="observed",
        )]
    finally:
        if should_close:
            fetcher.close()
