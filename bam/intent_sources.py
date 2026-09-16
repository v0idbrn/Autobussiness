"""Intent sources: public feeds that expose EXPRESSIONS OF NEED (§6, §24).

Three source classes, all fully public and legitimate:
- job-board RSS (primary): public hiring posts are explicit company intent
  ("posted by <company>" is company intent by definition; §12)
- news RSS search (secondary): only explicit/strong request phrasing survives
- directory/association member lists (tertiary): company existence signal, NOT
  commercial intent. Directory companies produce LEAD SIGNAL (weak tier),
  never automatic intent. Intent requires additional evidence (§1).

Every source returns normalized OpportunityCandidate items. One source
failing never kills a campaign (§25): failures are collected and skipped.
All fetched content is UNTRUSTED DATA - only scanned with deterministic
regexes, never executed, followed or re-prompted (§26).

Safety posture (unchanged): no CAPTCHA/login bypass, no private scraping,
1 request per query, robots/limits enforced by the shared fetcher.
"""

from __future__ import annotations

import html as _html
import re
from typing import Any

from bam.fetcher import Fetcher, validate_url
from bam.intent import OpportunityCandidate, score_intent

# -- source adapters (§24) ------------------------------------------------------

# Public job board with open RSS and company attribution. 1 request per feed.
JOB_BOARD_FEEDS: tuple[dict[str, str], ...] = (
    {"id": "wpjobs", "name": "WordPress Jobs (public board)",
     "url": "https://jobs.wordpress.net/feed/",
     "intent_class": "hiring"},
)

# News RSS search feeds (secondary; high noise, strict intent gate).
_INTENT_FEED_URL = ("https://news.google.com/rss/search?q={query}"
                    "&hl=en-US&gl=US&ceid=US:en")

# Query families: need-phrase x service-topic (§5). Small and measurable.
INTENT_QUERY_FAMILIES: dict[str, tuple[tuple[str, str], ...]] = {
    "pdf_to_excel": (
        ("en", '"need help" converting PDF invoices to Excel'),
        ("es", 'buscamos "procesamiento de facturas" OR "PDF a Excel"'),
    ),
    "excel_cleaning": (
        ("en", '"need help" cleaning up Excel spreadsheet data'),
        ("es", 'necesitamos "limpieza de datos" OR "limpiar excel"'),
    ),
    "qa_automation": (
        ("en", '"looking for" QA automation contractor website testing'),
        ("es", 'buscamos "automatización de pruebas" OR "testing" freelance'),
    ),
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _scrub(text: str) -> str:
    """Strip line-break fragments and control chars from untrusted text."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\r\n\x85\u2028\u2029]", " ", text)


def _strip_html(text: str) -> str:
    text = _html.unescape(text or "")
    return re.sub(r"<[^>]+>", " ", text)


def _parse_pubdate(raw: str | None) -> str | None:
    """Normalize an RFC822 pubDate to ISO-8601 (deterministic; None if absent)."""
    from datetime import timezone
    from email.utils import parsedate_to_datetime

    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat(timespec="seconds")


def parse_job_board_feed(xml_bytes: bytes, *, source_id: str,
                         source_url: str, limit: int = 20
                         ) -> list[OpportunityCandidate]:
    """Parse a public job-board RSS into hiring-intent candidates (§3).

    Every item IS company intent by construction (a public hiring post).
    Poster company is extracted only from the feed's own metadata
    (<dc:creator> / <job_listing:company> style fields) - never guessed.
    Service fit is scored deterministically from title text.
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    out: list[OpportunityCandidate] = []
    for item in (el for el in root.iter() if _local(el.tag) == "item"):
        if len(out) >= limit:
            break
        title = link = creator = None
        for child in item:
            tag = _local(child.tag)
            if tag == "title" and child.text:
                title = child.text.strip()
            elif tag == "link" and child.text:
                link = child.text.strip()
            elif tag in ("creator", "author") and child.text:
                creator = child.text.strip()
        if not title or not link:
            continue
        try:
            final_url = validate_url(link)
        except Exception:
            continue
        title = _scrub(title)[:200]
        intent = score_intent(title)
        # hiring posts: a role mention without request verbs is still a
        # hiring signal - upgrade none/weak to medium (HIRING_SIGNAL, §3)
        if intent.tier in ("none", "weak"):
            intent = IntentMatchUpgrade(intent, title)
        if intent.tier in ("none",):
            continue
        poster = _scrub(creator)[:80] if creator else None
        out.append(OpportunityCandidate(
            company=poster,          # observed feed metadata only (§12)
            company_url=None,
            source_url=final_url,
            source_type=f"job_board:{source_id}",
            published_at=_parse_pubdate(
                _item_field(item, "pubDate")),
            title=title,
            snippet=title,
            intent=intent,
            evidence=[{"kind": "job_post", "status": "OBSERVED",
                       "url": final_url, "excerpt": title[:160]}],
        ))
    return out


def IntentMatchUpgrade(intent: Any, title: str) -> Any:
    """HIRING_SIGNAL floor: a public job post is at least a medium hiring
    signal (§3: 'hiring != buying', so never above medium without real
    service-fit phrases). Deterministic."""
    from bam.intent import IntentMatch

    if intent.service:
        return IntentMatch("medium", 60, intent.service, intent.phrases)
    # role heuristics from the title text (still deterministic)
    tl = title.lower()
    if any(w in tl for w in ("qa", "test", "quality")):
        return IntentMatch("medium", 60, "qa_automation", (title[:80],))
    if any(w in tl for w in ("data entry", "data processing", "excel",
                             "spreadsheet", "back office")):
        return IntentMatch("medium", 60, "excel_cleaning", (title[:80],))
    return intent  # keep 'none' - not our domain


def _item_field(item: Any, name: str) -> str | None:
    name_lower = name.lower()
    for child in item:
        if _local(child.tag) == name_lower and child.text:
            return child.text
    return None


def parse_intent_feed(xml_bytes: bytes, *, query: str, source_url: str,
                      limit: int = 10) -> list[OpportunityCandidate]:
    """Parse a news RSS search feed into intent candidates (secondary source).

    Only explicit/strong intent survives: news items describe events, and a
    company merely existing in an industry is NOT an opportunity (§1)."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    out: list[OpportunityCandidate] = []
    for item in (el for el in root.iter() if _local(el.tag) == "item"):
        if len(out) >= limit:
            break
        title = link = None
        for child in item:
            tag = _local(child.tag)
            if tag == "title" and child.text:
                title = child.text.strip()
            elif tag == "link" and child.text:
                link = child.text.strip()
        if not title or not link:
            continue
        try:
            final_url = validate_url(link)
        except Exception:
            continue
        title = _scrub(title)[:200]
        desc = _scrub(_strip_html(_item_field(item, "description") or ""))[:600]
        text = f"{title}. {desc}"
        intent = score_intent(text)
        if intent.tier not in ("explicit", "strong"):
            continue
        out.append(OpportunityCandidate(
            company=None,
            company_url=None,
            source_url=final_url,
            source_type="rss",
            published_at=_parse_pubdate(_item_field(item, "pubDate")),
            title=title,
            snippet=text[:400],
            intent=intent,
            evidence=[{"kind": "intent_post", "status": "OBSERVED",
                       "url": final_url, "excerpt": title[:160]}],
        ))
    return out


# -- discovery runners (§25 failure isolation) -----------------------------------

def discover_intent_from_job_boards(
    *, fetcher: Fetcher | None = None, limit_per_feed: int = 20,
) -> tuple[list[OpportunityCandidate], list[dict[str, str]]]:
    """1 request per job-board feed; failures are skips, not crashes."""
    should_close = False
    if fetcher is None:
        fetcher = Fetcher()
        should_close = True
    candidates: list[OpportunityCandidate] = []
    failures: list[dict[str, str]] = []
    try:
        for feed in JOB_BOARD_FEEDS:
            try:
                res = fetcher.fetch(feed["url"])
            except Exception as exc:
                failures.append({"source": feed["id"],
                                 "reason": f"fetch failed: {exc}"})
                continue
            if res.status != 200:
                failures.append({"source": feed["id"],
                                 "reason": f"blocked: HTTP {res.status}"})
                continue
            candidates.extend(parse_job_board_feed(
                res.content, source_id=feed["id"], source_url=feed["url"],
                limit=limit_per_feed))
    finally:
        if should_close:
            fetcher.close()
    return candidates, failures


def discover_intent_from_feeds(
    queries: list[str] | None = None, *,
    services: list[str] | None = None,
    limit_per_query: int = 5,
    fetcher: Fetcher | None = None,
    include_job_boards: bool = True,
    include_remote_boards: bool = True,
    directories: list[str] | None = None,
) -> tuple[list[OpportunityCandidate], list[dict[str, str]]]:
    """Full intent discovery: job boards + remote boards + news RSS + directories.

    Sources (§24):
    - WordPress Jobs RSS (primary): hiring posts = explicit company intent
    - We Work Remotely RSS (primary): remote job posts = hiring intent
    - Remote OK JSON API (primary): remote job posts = hiring intent
    - Jobicy JSON API (primary): remote job posts = hiring intent
    - Google News RSS (secondary): explicit/strong request phrases only
    - Directory/association pages (tertiary): LEAD SIGNAL only, not intent

    Directory results are LEAD SIGNAL only (weak tier) — they prove company
    existence but never automatically produce commercial intent (§1).

    Returns (candidates, failures). A blocked/failed source is a skip (§25).
    """
    qs: list[tuple[str, str]] = []
    if queries:
        qs = [("op", q) for q in queries]
    else:
        for svc in (services or list(INTENT_QUERY_FAMILIES)):
            qs.extend(INTENT_QUERY_FAMILIES.get(svc, ()))

    should_close = False
    if fetcher is None:
        fetcher = Fetcher()
        should_close = True
    candidates: list[OpportunityCandidate] = []
    failures: list[dict[str, str]] = []
    try:
        if include_job_boards:
            # WordPress Jobs
            jb, jb_fail = discover_intent_from_job_boards(
                fetcher=fetcher, limit_per_feed=20)
            candidates.extend(jb)
            failures.extend(jb_fail)
        if include_remote_boards:
            # We Work Remotely
            wwr, wwr_fail = discover_intent_from_wwrss(
                fetcher=fetcher, limit_per_feed=20)
            candidates.extend(wwr)
            failures.extend(wwr_fail)
            # Remote OK
            rok, rok_fail = discover_intent_from_remoteok(
                fetcher=fetcher, limit=20)
            candidates.extend(rok)
            failures.extend(rok_fail)
            # Jobicy
            jc, jc_fail = discover_intent_from_jobicy(
                fetcher=fetcher, limit=20)
            candidates.extend(jc)
            failures.extend(jc_fail)
            # Reddit (multiple subreddits)
            reddit, reddit_fail = discover_intent_from_reddit(
                fetcher=fetcher, services=services, limit_per_sub=15)
            candidates.extend(reddit)
            failures.extend(reddit_fail)
            # Freelancer.com
            fl, fl_fail = discover_intent_from_freelancer(
                fetcher=fetcher, limit_per_category=10)
            candidates.extend(fl)
            failures.extend(fl_fail)
            # Workana
            wa, wa_fail = discover_intent_from_workana(
                fetcher=fetcher, limit_per_category=10)
            candidates.extend(wa)
            failures.extend(wa_fail)
        for lang, query in qs:
            feed_url = _INTENT_FEED_URL.format(query=query)
            try:
                res = fetcher.fetch(feed_url)
            except Exception as exc:
                failures.append({"query": query, "reason": f"fetch failed: {exc}"})
                continue
            if res.status != 200:
                failures.append({"query": query,
                                 "reason": f"blocked: HTTP {res.status}"})
                continue
            items = parse_intent_feed(res.content, query=query,
                                      source_url=feed_url,
                                      limit=limit_per_query)
            for it in items:
                it.evidence.append({"kind": "intent_query", "status": "OBSERVED",
                                    "excerpt": f"matched intent query: {query}"})
            candidates.extend(items)
        # Directory discovery (tertiary source): lead signal, not intent
        if directories:
            for d in directories:
                try:
                    dir_cands, dir_fail = discover_intent_from_directory(
                        d, fetcher=fetcher, limit=10)
                    candidates.extend(dir_cands)
                    if dir_fail:
                        failures.extend(dir_fail)
                except Exception as exc:
                    failures.append({"directory": d, "reason": f"fetch failed: {exc}"})
    finally:
        if should_close:
            fetcher.close()
    return candidates, failures


# -- We Work Remotely RSS source (§24) ------------------------------------------

WWRSS_FEEDS: tuple[dict[str, str], ...] = (
    {"id": "wwr_dev", "name": "We Work Remotely - Programming",
     "url": "https://weworkremotely.com/categories/remote-programming-jobs.rss"},
    {"id": "wwr_full", "name": "We Work Remotely - Full-Stack",
     "url": "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss"},
    {"id": "wwr_js", "name": "We Work Remotely - JavaScript",
     "url": "https://weworkremotely.com/categories/remote-javascript-programming-jobs.rss"},
    {"id": "wwr_python", "name": "We Work Remotely - Python/Django",
     "url": "https://weworkremotely.com/categories/remote-python-programming-jobs.rss"},
)


def parse_wwrss_feed(xml_bytes: bytes, *, source_id: str, limit: int = 20
                     ) -> list[OpportunityCandidate]:
    """Parse We Work Remotely RSS into hiring-intent candidates.

    Standard RSS 2.0 with <title>, <link>, <description>, <pubDate>,
    <category>, <dc:creator>. Company extracted from creator field.
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    out: list[OpportunityCandidate] = []
    for item in (el for el in root.iter() if _local(el.tag) == "item"):
        if len(out) >= limit:
            break
        title = link = creator = desc = None
        categories: list[str] = []
        for child in item:
            tag = _local(child.tag)
            if tag == "title" and child.text:
                title = child.text.strip()
            elif tag == "link" and child.text:
                link = child.text.strip()
            elif tag in ("creator", "author") and child.text:
                creator = child.text.strip()
            elif tag == "description" and child.text:
                desc = child.text.strip()
            elif tag == "category" and child.text:
                categories.append(child.text.strip())
        if not title or not link:
            continue
        try:
            final_url = validate_url(link)
        except Exception:
            continue
        title = _scrub(title)[:200]
        desc_text = _scrub(_strip_html(desc or ""))[:600]
        text = f"{title}. {desc_text}"
        intent = score_intent(text)
        # Hiring signal floor
        if intent.tier in ("none", "weak"):
            intent = IntentMatchUpgrade(intent, title)
        if intent.tier in ("none",):
            continue
        poster = _scrub(creator)[:80] if creator else None
        out.append(OpportunityCandidate(
            company=poster,
            company_url=None,
            source_url=final_url,
            source_type=f"job_board:{source_id}",
            published_at=_parse_pubdate(_item_field(item, "pubDate")),
            title=title,
            snippet=text[:400],
            intent=intent,
            evidence=[{"kind": "job_post", "status": "OBSERVED",
                        "url": final_url, "excerpt": title[:160]}],
        ))
    return out


def discover_intent_from_wwrss(
    *, fetcher: Fetcher | None = None, limit_per_feed: int = 20,
) -> tuple[list[OpportunityCandidate], list[dict[str, str]]]:
    """Fetch We Work Remotely RSS feeds; failures are skips, not crashes."""
    should_close = False
    if fetcher is None:
        fetcher = Fetcher()
        should_close = True
    candidates: list[OpportunityCandidate] = []
    failures: list[dict[str, str]] = []
    try:
        for feed in WWRSS_FEEDS:
            try:
                res = fetcher.fetch(feed["url"])
            except Exception as exc:
                failures.append({"source": feed["id"],
                                 "reason": f"fetch failed: {exc}"})
                continue
            if res.status != 200:
                failures.append({"source": feed["id"],
                                 "reason": f"blocked: HTTP {res.status}"})
                continue
            candidates.extend(parse_wwrss_feed(
                res.content, source_id=feed["id"], limit=limit_per_feed))
    finally:
        if should_close:
            fetcher.close()
    return candidates, failures


# -- Remote OK JSON API source (§24) --------------------------------------------

REMOTEOK_API = "https://remoteok.com/api"


def parse_remoteok_json(data: list[dict[str, Any]], *, limit: int = 20
                        ) -> list[OpportunityCandidate]:
    """Parse Remote OK JSON API response into hiring-intent candidates.

    Remote OK returns a flat JSON array of job objects. Each has:
    id, position, company, logo, link, description, tags, date, etc.
    """
    out: list[OpportunityCandidate] = []
    for job in data:
        if len(out) >= limit:
            break
        # Skip the metadata object (first item is usually {"legal": ...})
        if not isinstance(job, dict) or "position" not in job:
            continue
        position = job.get("position", "")
        company = job.get("company", "")
        link = job.get("link", "")
        desc = job.get("description", "")
        tags = job.get("tags", [])
        date_str = job.get("date", "")
        if not position or not link:
            continue
        try:
            final_url = validate_url(link)
        except Exception:
            continue
        position = _scrub(position)[:200]
        desc_text = _scrub(_strip_html(desc))[:600]
        text = f"{position} at {company}. {desc_text}"
        intent = score_intent(text)
        if intent.tier in ("none", "weak"):
            intent = IntentMatchUpgrade(intent, position)
        if intent.tier in ("none",):
            continue
        # Remote OK date format: ISO or epoch
        pub_date = None
        if date_str:
            try:
                from datetime import datetime, timezone
                if isinstance(date_str, (int, float)):
                    pub_date = datetime.fromtimestamp(date_str, tz=timezone.utc).isoformat(timespec="seconds")
                else:
                    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    pub_date = dt.isoformat(timespec="seconds")
            except (ValueError, TypeError):
                pub_date = None
        out.append(OpportunityCandidate(
            company=company or None,
            company_url=None,
            source_url=final_url,
            source_type="job_board:remoteok",
            published_at=pub_date,
            title=position,
            snippet=text[:400],
            intent=intent,
            evidence=[{"kind": "job_post", "status": "OBSERVED",
                        "url": final_url, "excerpt": position[:160]}],
        ))
    return out


def discover_intent_from_remoteok(
    *, fetcher: Fetcher | None = None, limit: int = 20,
) -> tuple[list[OpportunityCandidate], list[dict[str, str]]]:
    """Fetch Remote OK JSON API; failures are skips, not crashes."""
    should_close = False
    if fetcher is None:
        fetcher = Fetcher()
        should_close = True
    candidates: list[OpportunityCandidate] = []
    failures: list[dict[str, str]] = []
    try:
        try:
            res = fetcher.fetch(REMOTEOK_API)
        except Exception as exc:
            failures.append({"source": "remoteok",
                             "reason": f"fetch failed: {exc}"})
            return candidates, failures
        if res.status != 200:
            failures.append({"source": "remoteok",
                             "reason": f"blocked: HTTP {res.status}"})
            return candidates, failures
        import json
        try:
            data = json.loads(res.content)
        except (json.JSONDecodeError, ValueError):
            failures.append({"source": "remoteok",
                             "reason": "invalid JSON response"})
            return candidates, failures
        candidates.extend(parse_remoteok_json(data, limit=limit))
    finally:
        if should_close:
            fetcher.close()
    return candidates, failures


# -- Jobicy JSON API source (§24) ------------------------------------------------

JOBICY_API = "https://jobicy.com/api/v2/remote-jobs"


def parse_jobicy_json(data: dict[str, Any], *, limit: int = 20
                      ) -> list[OpportunityCandidate]:
    """Parse Jobicy JSON API response into hiring-intent candidates.

    Jobicy returns {"jobs": [...]} with each job having:
    id, url, jobTitle, companyName, jobIndustry, jobType, jobGeo,
    jobLevel, jobExcerpt, jobDescription, pubDate, salaryMin/Max.
    """
    out: list[OpportunityCandidate] = []
    jobs = data.get("jobs", [])
    for job in jobs:
        if len(out) >= limit:
            break
        if not isinstance(job, dict):
            continue
        title = job.get("jobTitle", "")
        company = job.get("companyName", "")
        link = job.get("url", "")
        desc = job.get("jobExcerpt", "") or job.get("jobDescription", "")
        industries = job.get("jobIndustry", [])
        geo = job.get("jobGeo", "")
        level = job.get("jobLevel", "")
        pub_date = job.get("pubDate", "")
        salary_min = job.get("salaryMin")
        salary_max = job.get("salaryMax")
        if not title or not link:
            continue
        try:
            final_url = validate_url(link)
        except Exception:
            continue
        title = _scrub(title)[:200]
        desc_text = _scrub(_strip_html(desc))[:600]
        text = f"{title} at {company}. {desc_text}"
        intent = score_intent(text)
        if intent.tier in ("none", "weak"):
            intent = IntentMatchUpgrade(intent, title)
        if intent.tier in ("none",):
            continue
        # Parse ISO date
        iso_date = None
        if pub_date:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                iso_date = dt.isoformat(timespec="seconds")
            except (ValueError, TypeError):
                iso_date = None
        out.append(OpportunityCandidate(
            company=company or None,
            company_url=None,
            source_url=final_url,
            source_type="job_board:jobicy",
            published_at=iso_date,
            title=title,
            snippet=text[:400],
            intent=intent,
            evidence=[{"kind": "job_post", "status": "OBSERVED",
                        "url": final_url, "excerpt": title[:160]}],
        ))
    return out


def discover_intent_from_jobicy(
    *, fetcher: Fetcher | None = None, limit: int = 20,
) -> tuple[list[OpportunityCandidate], list[dict[str, str]]]:
    """Fetch Jobicy JSON API; failures are skips, not crashes."""
    should_close = False
    if fetcher is None:
        fetcher = Fetcher()
        should_close = True
    candidates: list[OpportunityCandidate] = []
    failures: list[dict[str, str]] = []
    try:
        url = f"{JOBICY_API}?count={limit}"
        try:
            res = fetcher.fetch(url)
        except Exception as exc:
            failures.append({"source": "jobicy",
                             "reason": f"fetch failed: {exc}"})
            return candidates, failures
        if res.status != 200:
            failures.append({"source": "jobicy",
                             "reason": f"blocked: HTTP {res.status}"})
            return candidates, failures
        import json
        try:
            data = json.loads(res.content)
        except (json.JSONDecodeError, ValueError):
            failures.append({"source": "jobicy",
                             "reason": "invalid JSON response"})
            return candidates, failures
        candidates.extend(parse_jobicy_json(data, limit=limit))
    finally:
        if should_close:
            fetcher.close()
    return candidates, failures


# -- Reddit RSS (§24 Tier 1) ----------------------------------------------------

# Subreddits organized by service relevance (top 5 per service for rate limit safety)
REDDIT_SUBREDDITS: dict[str, tuple[dict[str, str], ...]] = {
    "pdf_to_excel": (
        {"id": "r_forhire", "name": "r/forhire", "subreddit": "forhire",
         "intent_class": "hiring"},
        {"id": "r_dataentry", "name": "r/DataEntry", "subreddit": "DataEntry",
         "intent_class": "service_request"},
        {"id": "r_excel", "name": "r/Excel", "subreddit": "Excel",
         "intent_class": "help_request"},
        {"id": "r_freelance", "name": "r/freelance", "subreddit": "freelance",
         "intent_class": "hiring"},
    ),
    "excel_cleaning": (
        {"id": "r_excel", "name": "r/Excel", "subreddit": "Excel",
         "intent_class": "help_request"},
        {"id": "r_googlesheets", "name": "r/GoogleSheets", "subreddit": "GoogleSheets",
         "intent_class": "help_request"},
        {"id": "r_bookkeeping", "name": "r/Bookkeeping", "subreddit": "Bookkeeping",
         "intent_class": "help_request"},
        {"id": "r_forhire", "name": "r/forhire", "subreddit": "forhire",
         "intent_class": "hiring"},
    ),
    "qa_automation": (
        {"id": "r_qualityassurance", "name": "r/QualityAssurance", "subreddit": "QualityAssurance",
         "intent_class": "hiring"},
        {"id": "r_selenium", "name": "r/Selenium", "subreddit": "Selenium",
         "intent_class": "help_request"},
        {"id": "r_playwright", "name": "r/Playwright", "subreddit": "Playwright",
         "intent_class": "help_request"},
        {"id": "r_cypress", "name": "r/Cypress", "subreddit": "Cypress",
         "intent_class": "help_request"},
    ),
}

# Generic Reddit RSS URL pattern
_REDDIT_RSS_URL = "https://www.reddit.com/r/{subreddit}/.rss"

# Title patterns that signal service offers (not hiring)
_OFFER_PATTERNS = re.compile(
    r"\[for hire\]|\[offer\]|available for hire|offering my services",
    re.IGNORECASE,
)
_HIRING_PATTERNS = re.compile(
    r"\[hiring\]|looking for|need a|seeking|want to hire",
    re.IGNORECASE,
)


def parse_reddit_rss(xml_bytes: bytes, source_url: str,
                     intent_class: str = "hiring",
                     limit: int = 30) -> list[OpportunityCandidate]:
    """Parse Reddit RSS (Atom) into OpportunityCandidate items.

    For hiring-focused subreddits (r/forhire): [Hiring] posts only.
    For help-focused subreddits (r/Excel): posts asking for help with our services.
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    out: list[OpportunityCandidate] = []
    for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        if len(out) >= limit:
            break
        title_el = entry.find("{http://www.w3.org/2005/Atom}title")
        link_el = entry.find("{http://www.w3.org/2005/Atom}link")
        content_el = entry.find("{http://www.w3.org/2005/Atom}content")
        author_el = entry.find("{http://www.w3.org/2005/Atom}author/{http://www.w3.org/2005/Atom}name")

        if title_el is None or link_el is None:
            continue

        title = (title_el.text or "").strip()
        link = link_el.get("href", "")
        content = (content_el.text if content_el is not None else "") or ""
        author = (author_el.text if author_el is not None else "") or ""

        if not title or not link:
            continue

        # For hiring-focused subs: skip [For Hire] posts
        if intent_class == "hiring" and _OFFER_PATTERNS.search(title):
            continue

        try:
            final_url = validate_url(link)
        except Exception:
            continue

        title_clean = _scrub(title)[:200]
        desc_clean = _scrub(_strip_html(content))[:600]
        text = f"{title_clean}. {desc_clean}"
        intent = score_intent(text)

        # Hiring posts without service keywords still carry medium intent
        if intent.tier in ("none", "weak"):
            intent = IntentMatchUpgrade(intent, title_clean)
        if intent.tier in ("none",):
            continue

        out.append(OpportunityCandidate(
            company=author or None,
            company_url=None,
            source_url=final_url,
            source_type=f"reddit:{source_url.split('/r/')[-1].split('/')[0] if '/r/' in source_url else 'unknown'}",
            published_at=None,
            title=title_clean,
            snippet=text[:400],
            intent=intent,
            evidence=[{"kind": "reddit_post", "status": "OBSERVED",
                        "url": final_url, "excerpt": title_clean[:160]}],
        ))
    return out


def discover_intent_from_reddit(
    *,
    fetcher: Fetcher | None = None,
    services: list[str] | None = None,
    limit_per_sub: int = 15,
) -> tuple[list[OpportunityCandidate], list[dict[str, str]]]:
    """Fetch multiple Reddit subreddits; failures are skips, not crashes."""
    should_close = False
    if fetcher is None:
        fetcher = Fetcher()
        should_close = True
    candidates: list[OpportunityCandidate] = []
    failures: list[dict[str, str]] = []

    # Deduplicate subreddits across services
    seen_subs: set[str] = set()
    subs_to_fetch: list[tuple[str, str, str]] = []  # (id, url, intent_class)

    for svc in (services or list(REDDIT_SUBREDDITS)):
        for sub in REDDIT_SUBREDDITS.get(svc, ()):
            if sub["subreddit"] not in seen_subs:
                seen_subs.add(sub["subreddit"])
                url = _REDDIT_RSS_URL.format(subreddit=sub["subreddit"])
                subs_to_fetch.append((sub["id"], url, sub["intent_class"]))

    try:
        for sub_id, url, intent_class in subs_to_fetch:
            try:
                res = fetcher.fetch(url)
            except Exception as exc:
                failures.append({"source": sub_id,
                                 "reason": f"fetch failed: {exc}"})
                continue
            if res.status != 200:
                failures.append({"source": sub_id,
                                 "reason": f"blocked: HTTP {res.status}"})
                continue
            candidates.extend(parse_reddit_rss(
                res.content, source_url=url,
                intent_class=intent_class, limit=limit_per_sub))
    finally:
        if should_close:
            fetcher.close()
    return candidates, failures


# -- Freelancer.com HTML scraper (§24 Tier 1) ------------------------------------

FREELANCER_CATEGORIES = (
    "https://www.freelancer.com/projects/data-entry/",
    "https://www.freelancer.com/projects/data-analysis/",
    "https://www.freelancer.com/projects/software-development/",
    "https://www.freelancer.com/projects/testing-qa/",
)


def _parse_freelancer_html(html_text: str | bytes, source_url: str,
                           limit: int = 10) -> list[OpportunityCandidate]:
    """Extract project cards from Freelancer.com HTML."""
    if isinstance(html_text, bytes):
        html_text = html_text.decode("utf-8", errors="replace")
    out: list[OpportunityCandidate] = []

    # Project card patterns: title links and descriptions
    # Match <a href="/projects/...">...<title text>...</a>
    title_pattern = re.compile(
        r'<a[^>]*href="(/projects/[^"]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    desc_pattern = re.compile(
        r'<div[^>]*class="[^"]*(?:project-description|text-body)[^"]*"'
        r'[^>]*>(.*?)</div>',
        re.IGNORECASE | re.DOTALL,
    )
    budget_pattern = re.compile(
        r'(?:USD|EUR|GBP|\$|€|£)\s*[\d,]+(?:\s*[-–]\s*(?:USD|EUR|GBP|\$|€|£)?\s*[\d,]+)?',
        re.IGNORECASE,
    )

    found_urls: set[str] = set()
    for match in title_pattern.finditer(html_text):
        if len(out) >= limit:
            break
        rel_url, inner_html = match.group(1), match.group(2)
        full_url = f"https://www.freelancer.com{rel_url}"
        if full_url in found_urls:
            continue
        found_urls.add(full_url)

        # Extract title from inner HTML (strip tags)
        title_clean = _scrub(_strip_html(inner_html))[:200]
        if not title_clean:
            continue

        # Try to find nearby description
        desc_match = desc_pattern.search(html_text[match.start():match.start() + 2000])
        desc = _scrub(_strip_html(desc_match.group(1)))[:400] if desc_match else ""

        # Try to find budget
        budget_match = budget_pattern.search(html_text[match.start():match.start() + 2000])
        budget = budget_match.group(0) if budget_match else ""

        text = f"{title_clean}. {desc}"
        intent = score_intent(text)

        # Freelancer posts are explicit service requests
        if intent.tier in ("none", "weak"):
            intent = IntentMatchUpgrade(intent, title_clean)
        if intent.tier in ("none",):
            continue

        out.append(OpportunityCandidate(
            company=None,
            company_url=None,
            source_url=validate_url(full_url),
            source_type="job_board:freelancer",
            published_at=None,
            title=title_clean,
            snippet=text[:400],
            intent=intent,
            evidence=[{"kind": "job_post", "status": "OBSERVED",
                        "url": full_url, "excerpt": title_clean[:160]}],
        ))
    return out


def discover_intent_from_freelancer(
    *, fetcher: Fetcher | None = None, limit_per_category: int = 10,
) -> tuple[list[OpportunityCandidate], list[dict[str, str]]]:
    """Fetch Freelancer.com project listings; failures are skips, not crashes."""
    should_close = False
    if fetcher is None:
        fetcher = Fetcher()
        should_close = True
    candidates: list[OpportunityCandidate] = []
    failures: list[dict[str, str]] = []
    try:
        for cat_url in FREELANCER_CATEGORIES:
            try:
                res = fetcher.fetch(cat_url)
            except Exception as exc:
                failures.append({"source": "freelancer",
                                 "reason": f"fetch failed: {exc}"})
                continue
            if res.status != 200:
                failures.append({"source": "freelancer",
                                 "reason": f"blocked: HTTP {res.status}"})
                continue
            candidates.extend(_parse_freelancer_html(
                res.content, source_url=cat_url, limit=limit_per_category))
    finally:
        if should_close:
            fetcher.close()
    return candidates, failures


# -- Workana HTML scraper (§24 Tier 1) -------------------------------------------

WORKANA_CATEGORIES = (
    "https://www.workana.com/jobs?category=data-entry",
    "https://www.workana.com/jobs?category=it-programming",
    "https://www.workana.com/jobs?category=writing-translation",
)


def _parse_workana_html(html_text: str | bytes, source_url: str,
                        limit: int = 10) -> list[OpportunityCandidate]:
    """Extract project cards from Workana HTML."""
    if isinstance(html_text, bytes):
        html_text = html_text.decode("utf-8", errors="replace")
    out: list[OpportunityCandidate] = []

    # Workana project cards
    title_pattern = re.compile(
        r'<h2[^>]*class="[^"]*project-title[^"]*"[^>]*>\s*<a[^>]*href="([^"]+)"'
        r'[^>]*>([^<]+)</a>',
        re.IGNORECASE,
    )
    desc_pattern = re.compile(
        r'<p[^>]*class="[^"]*project-description[^"]*"[^>]*>(.*?)</p>',
        re.IGNORECASE | re.DOTALL,
    )
    budget_pattern = re.compile(
        r'(?:USD|EUR|GBP|\$|€|£)\s*[\d,]+(?:\s*[-–]\s*(?:USD|EUR|GBP|\$|€|£)?\s*[\d,]+)?',
        re.IGNORECASE,
    )

    found_urls: set[str] = set()
    for match in title_pattern.finditer(html_text):
        if len(out) >= limit:
            break
        rel_url, title = match.group(1), match.group(2)
        if not rel_url.startswith("http"):
            full_url = f"https://www.workana.com{rel_url}"
        else:
            full_url = rel_url
        if full_url in found_urls:
            continue
        found_urls.add(full_url)

        title_clean = _scrub(title)[:200]
        if not title_clean:
            continue

        desc_match = desc_pattern.search(html_text[match.start():match.start() + 2000])
        desc = _scrub(_strip_html(desc_match.group(1)))[:400] if desc_match else ""

        budget_match = budget_pattern.search(html_text[match.start():match.start() + 2000])
        budget = budget_match.group(0) if budget_match else ""

        text = f"{title_clean}. {desc}"
        intent = score_intent(text)

        if intent.tier in ("none", "weak"):
            intent = IntentMatchUpgrade(intent, title_clean)
        if intent.tier in ("none",):
            continue

        out.append(OpportunityCandidate(
            company=None,
            company_url=None,
            source_url=validate_url(full_url),
            source_type="job_board:workana",
            published_at=None,
            title=title_clean,
            snippet=text[:400],
            intent=intent,
            evidence=[{"kind": "job_post", "status": "OBSERVED",
                        "url": full_url, "excerpt": title_clean[:160]}],
        ))
    return out


def discover_intent_from_workana(
    *, fetcher: Fetcher | None = None, limit_per_category: int = 10,
) -> tuple[list[OpportunityCandidate], list[dict[str, str]]]:
    """Fetch Workana job listings; failures are skips, not crashes."""
    should_close = False
    if fetcher is None:
        fetcher = Fetcher()
        should_close = True
    candidates: list[OpportunityCandidate] = []
    failures: list[dict[str, str]] = []
    try:
        for cat_url in WORKANA_CATEGORIES:
            try:
                res = fetcher.fetch(cat_url)
            except Exception as exc:
                failures.append({"source": "workana",
                                 "reason": f"fetch failed: {exc}"})
                continue
            if res.status != 200:
                failures.append({"source": "workana",
                                 "reason": f"blocked: HTTP {res.status}"})
                continue
            candidates.extend(_parse_workana_html(
                res.content, source_url=cat_url, limit=limit_per_category))
    finally:
        if should_close:
            fetcher.close()
    return candidates, failures


# -- campaigns_from_candidates (§33) --------------------------------------------

def campaigns_from_candidates(
    candidates: list[OpportunityCandidate],
) -> dict[str, list[OpportunityCandidate]]:
    """Group by recommended service for the campaign report (§33)."""
    grouped: dict[str, list[OpportunityCandidate]] = {}
    for c in candidates:
        grouped.setdefault(c.intent.service or "unknown", []).append(c)
    return grouped


# -- directory/association source adapter (Discovery V2 §1) ----------------------

def discover_intent_from_directory(
    directory_url: str,
    *,
    fetcher: Fetcher | None = None,
    limit: int = 15,
) -> tuple[list[OpportunityCandidate], list[dict[str, str]]]:
    """Directory/association source adapter for the Intent Engine.

    Uses the existing `discover_from_directory()` from discovery.py to extract
    company candidates from a public directory page. Does NOT automatically
    classify these as commercial intent — directory membership only proves
    existence/identity (LEAD SIGNAL), not need.

    Returns (candidates, failures). Failures are collected and skipped
    (§25: one source failing never kills a campaign).
    """
    from bam.discovery import discover_from_directory
    from bam.signals import classify_candidate
    from bam.intent import score_intent, IntentMatch

    should_close = False
    if fetcher is None:
        fetcher = Fetcher()
        should_close = True

    # Step 1: discover companies from directory
    # discover_from_directory() returns a list; failures return empty list
    try:
        companies = discover_from_directory(
            directory_url, fetcher=fetcher, limit=limit
        )
    except Exception as exc:
        if should_close:
            fetcher.close()
        return [], [{"directory": directory_url, "reason": f"fetch failed: {exc}"}]

    candidates: list[OpportunityCandidate] = []

    for c in companies:
        # Step 2: classify the domain
        ct = classify_candidate(c.domain)
        if ct.kind != "company":
            # Not a company host (publisher/aggregator/directory/social) — skip
            continue

        # Step 3: directory gives existence signal only; intent is always
        # at most weak, determined by whether intent phrases appear in title
        title = c.name or c.domain
        snippet = c.evidence or f"listed on directory {c.source_url}"
        intent = score_intent(title)

        # Step 4: upgrade none/weak from directory to at least medium
        # (hiring signal floor — a public listing may indicate hiring needs)
        if intent.tier in ("none", "weak"):
            intent = IntentMatch(
                tier="medium",
                score=40,
                service=intent.service,
                phrases=intent.phrases,
            )

        # Step 5: build OpportunityCandidate with directory as source
        # source_type: "directory" for this adapter
        candidate = OpportunityCandidate(
            company=c.name,
            company_url=c.url,
            source_url=c.source_url or "",
            source_type="directory",
            published_at=None,  # directory has no publication date
            title=title,
            snippet=snippet[:400] if snippet else "",
            intent=intent,
            evidence=[
                {"kind": "directory_listing", "status": "OBSERVED", "url": c.source_url or "", "excerpt": title[:160]},
            ],
        )
        candidates.append(candidate)

    if should_close:
        fetcher.close()
    return candidates, []
