"""Discovery: find companies from various sources.

Supports:
- Manual URL lists
- CSV files with company data
- Keyword-based web searches (via fetcher, no aggressive scraping)

All discovery is rate-limited, evidence-only, and respects robots.txt.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

from bam.config import load_config
from bam.denylist import Denylist
from bam.fetcher import Fetcher, validate_url
from bam.manifest import new_run_id


@dataclass
class DiscoveredCompany:
    name: str
    domain: str
    url: str
    source: str
    source_url: str | None
    industry: str | None
    evidence: str


def discover_from_urls(urls: list[str], *, source: str = "manual") -> list[DiscoveredCompany]:
    """Discover companies from a list of URLs."""
    results = []
    for url in urls:
        url = url.strip()
        if not url or url.startswith("#"):
            continue
        try:
            final_url = validate_url(url)
            domain = urlparse(final_url).netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
            results.append(DiscoveredCompany(
                name=domain.split(".")[0].title(),
                domain=domain,
                url=final_url,
                source=source,
                source_url=final_url,
                industry=None,
                evidence=f"URL provided by operator: {final_url}",
            ))
        except Exception:
            continue
    return results


def discover_from_csv(csv_path: Path, *, name_col: str | None = None,
                       url_col: str | None = None,
                       domain_col: str | None = None,
                       industry_col: str | None = None,
                       source: str = "csv") -> list[DiscoveredCompany]:
    """Discover companies from a CSV file.

    Auto-detects columns if not specified. Looks for common column names.
    """
    results = []
    if not csv_path.exists():
        return results

    with open(csv_path, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return results

        headers = [h.lower().strip() for h in reader.fieldnames]

        # Auto-detect columns
        if name_col is None:
            for h in headers:
                if h in ("name", "company", "company_name", "empresa"):
                    name_col = h
                    break
        if url_col is None:
            for h in headers:
                if h in ("url", "website", "site", "homepage", "web"):
                    url_col = h
                    break
        if domain_col is None:
            for h in headers:
                if h in ("domain", "dominio"):
                    domain_col = h
                    break
        if industry_col is None:
            for h in headers:
                if h in ("industry", "industria", "sector"):
                    industry_col = h
                    break

        for row in reader:
            name = row.get(name_col) if name_col else None
            url = row.get(url_col) if url_col else None
            domain = row.get(domain_col) if domain_col else None
            industry = row.get(industry_col) if industry_col else None

            if url:
                try:
                    final_url = validate_url(url)
                    parsed_domain = urlparse(final_url).netloc.lower()
                    if parsed_domain.startswith("www."):
                        parsed_domain = parsed_domain[4:]
                    results.append(DiscoveredCompany(
                        name=name or parsed_domain.split(".")[0].title(),
                        domain=parsed_domain,
                        url=final_url,
                        source=source,
                        source_url=str(csv_path),
                        industry=industry,
                        evidence=f"CSV row from {csv_path.name}",
                    ))
                except Exception:
                    continue
            elif domain:
                clean_domain = domain.strip().lower()
                if clean_domain.startswith("www."):
                    clean_domain = clean_domain[4:]
                url_from_domain = f"https://{clean_domain}"
                results.append(DiscoveredCompany(
                    name=name or clean_domain.split(".")[0].title(),
                    domain=clean_domain,
                    url=url_from_domain,
                    source=source,
                    source_url=str(csv_path),
                    industry=industry,
                    evidence=f"CSV row from {csv_path.name}",
                ))

    return results


def discover_from_text(text: str, *, source: str = "text") -> list[DiscoveredCompany]:
    """Discover companies from free-form text containing URLs or domains."""
    results = []
    # Extract URLs
    urls = re.findall(r'https?://[^\s<>"\']+', text)
    for url in urls:
        try:
            final_url = validate_url(url)
            domain = urlparse(final_url).netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
            results.append(DiscoveredCompany(
                name=domain.split(".")[0].title(),
                domain=domain,
                url=final_url,
                source=source,
                source_url=None,
                industry=None,
                evidence=f"URL found in text: {final_url}",
            ))
        except Exception:
            continue

    # Extract bare domains (e.g., "example.com")
    for match in re.finditer(r'\b([a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,})\b', text):
        domain = match.group(1).lower()
        if domain.startswith("www."):
            domain = domain[4:]
        # Skip common non-company domains
        skip = {"example.com", "localhost", "test.com", "google.com",
                "github.com", "stackoverflow.com", "wikipedia.org"}
        if domain in skip:
            continue
        url = f"https://{domain}"
        # Dedup by domain
        if not any(d.domain == domain for d in results):
            results.append(DiscoveredCompany(
                name=domain.split(".")[0].title(),
                domain=domain,
                url=url,
                source=source,
                source_url=None,
                industry=None,
                evidence=f"Domain found in text: {domain}",
            ))

    return results


def deduplicate(companies: list[DiscoveredCompany]) -> list[DiscoveredCompany]:
    """Remove duplicate companies by domain."""
    seen: set[str] = set()
    result = []
    for c in companies:
        if c.domain not in seen:
            seen.add(c.domain)
            result.append(c)
    return result


def filter_denied(companies: list[DiscoveredCompany]) -> tuple[list[DiscoveredCompany], list[DiscoveredCompany]]:
    """Filter out denylisted companies. Returns (allowed, denied)."""
    deny = Denylist.load()
    allowed = []
    denied = []
    for c in companies:
        hit = deny.check(domain=c.domain, company=c.name)
        if hit:
            denied.append(c)
        else:
            allowed.append(c)
    return allowed, denied


# -- RSS discovery ---------------------------------------------------------------

_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:EN"
_DOMAIN_SKIP = {
    "news.google.com", "google.com", "www.google.com", "youtube.com",
    "linkedin.com", "facebook.com", "x.com", "twitter.com", "reddit.com",
}


def _local(tag: str) -> str:
    """Local name of an XML tag (namespace-agnostic)."""
    return tag.rsplit("}", 1)[-1]


def _company_name_from_title(title: str) -> str:
    # Google News titles are "Headline - Publisher"; the headline is the signal.
    return title.split(" - ")[0].strip()[:120] or title.strip()[:120]


def parse_rss_feed(xml_bytes: bytes, *, query: str, source_url: str | None,
                   limit: int = 10) -> list[DiscoveredCompany]:
    """Parse an RSS search feed into candidate companies.

    Google News puts the PUBLISHER url in <source url="...">; <link> is a
    news.google.com redirect, so it is only a last-resort fallback and
    news.google.com domains themselves are never company candidates.
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    results: list[DiscoveredCompany] = []
    seen_domains: set[str] = set()
    for item in (el for el in root.iter() if _local(el.tag) == "item"):
        if len(results) >= limit:
            break
        title = link = source_attr = None
        for child in item:
            local = _local(child.tag)
            if local == "title" and child.text:
                title = child.text.strip()
            elif local == "link" and child.text:
                link = child.text.strip()
            elif local == "source" and child.get("url"):
                source_attr = child.get("url")
        if not title:
            continue
        candidate_url = source_attr or link
        if not candidate_url:
            continue
        try:
            final_url = validate_url(candidate_url)
        except Exception:
            continue
        domain = urlparse(final_url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        if not domain or domain in _DOMAIN_SKIP or domain in seen_domains:
            continue
        seen_domains.add(domain)
        evidence = f"found via RSS search: {query} ({title[:100]})"
        results.append(DiscoveredCompany(
            name=_company_name_from_title(title),
            domain=domain,
            url=final_url,
            source="rss",
            source_url=source_url,
            industry=None,
            evidence=evidence,
        ))
    return results


def discover_from_rss(query: str, *, limit: int = 10,
                      fetcher: Fetcher | None = None) -> list[DiscoveredCompany]:
    """Discover companies from a Google News RSS search (1 request per query)."""
    if not query.strip():
        return []
    feed_url = _RSS_URL.format(query=quote_plus(query.strip()))
    should_close = False
    if fetcher is None:
        fetcher = Fetcher()
        should_close = True
    try:
        try:
            res = fetcher.fetch(feed_url)
        except Exception:
            return []
        if res.status != 200:
            return []
        return parse_rss_feed(res.content, query=query, source_url=feed_url,
                              limit=limit)
    finally:
        if should_close:
            fetcher.close()
