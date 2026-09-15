"""The 9-step lead research pipeline (plan v3.1 §3).

bam research <url>:
  1. validate URL (scheme allowlist, public host)
  2. denylist check -> BLOCKED_xxx on match
  3. download homepage (guarded fetch + robots.txt check)
  4. extract signals (deterministic)
  5. save evidence
  6. build profile (claims from closed enums)
  7. compute score (deterministic; evidence caps)
  8. generate report (profile.json + report.md)
  9. persist to SQLite (+ audit)

LLM is optional: disabled/unavailable/budget/schema failure => deterministic
mode, pipeline still completes with exit 0 (plan §7).
"""

from __future__ import annotations

import time
import urllib.robotparser as robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bam.config import Config, load_config
from bam.denylist import Denylist, DenyHit, normalize_domain
from bam.evidence import Evidence, page_evidence
from bam.extractors import extract_signals
from bam.fetcher import FetchError, Fetcher, validate_url
from bam.llm import synthesize
from bam.manifest import RunManifest, new_run_id
from bam.reporting import build_profile, write_profile, write_report
from bam.scorer import Scored, apply_observed_wins, score_opportunity
from bam.store import LeadState, Store


@dataclass
class ResearchResult:
    run_id: str
    url: str
    domain: str
    lead_id: int
    state: str
    score: float | None
    confidence: str | None
    decision: str
    profile_path: Path | None
    report_path: Path | None
    llm_degraded_reason: str | None
    notes: list[str] = field(default_factory=list)


class ResearchBlocked(RuntimeError):
    """Lead blocked by denylist or unreachable target (structured reason)."""


def _robots_allows(url: str, *, user_agent: str) -> bool | None:
    """True/False from robots.txt; None if robots.txt unavailable (fail-open,
    still capped by the same fetch guards)."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = robotparser.RobotFileParser()
    try:
        with Fetcher() as f:
            res = f.fetch(robots_url)
        if res.status != 200:
            return None
        rp.parse(res.content.decode("utf-8", errors="replace").splitlines())
    except FetchError:
        return None
    return rp.can_fetch(user_agent, url)


def research(
    url: str,
    *,
    store: Store,
    config: Config | None = None,
    company_name: str | None = None,
    industry: str | None = None,
) -> ResearchResult:
    cfg = config or load_config()
    run_id = new_run_id()
    manifest = RunManifest(run_id=run_id, kind="lead_research")
    notes: list[str] = []

    fetched_bytes: bytes | None = None
    final_url = url
    signals: dict[str, Any] = {}
    evidence_rows: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    llm_meta: dict[str, Any] = {}
    scored: Scored | None = None
    domain = normalize_domain(urlparse(url).netloc)
    res = None  # FetchResult

    def _fail(error: str, *, state: str = "blocked") -> ResearchResult:
        manifest.finish("failed", outcome={"reason": error})
        mpath = manifest.write(cfg.paths.runs_dir)
        store.save_run_manifest(run_id, kind="lead_research", status="failed",
                                manifest_path=str(mpath))
        cid = store.upsert_company(company_name or domain, domain, industry)
        lead_id2 = store.upsert_lead(cid, url, run_id, state=state)
        store.audit_standalone(actor="agent", action="lead.research_failed",
                               entity_type="lead", entity_id=lead_id2,
                               payload={"reason": error})
        return ResearchResult(run_id, url, domain, lead_id2, state, None, None,
                              "failed", None, None, error, notes)

    # -- 1. URL validation (syntactic; DNS happens inside the guarded fetcher) ------
    t0 = time.monotonic()
    try:
        final_url = validate_url(url)
    except FetchError as exc:
        return _fail(f"url_validation: {exc}")
    manifest.add_step("validate_url", elapsed_s=time.monotonic() - t0,
                      detail={"url": final_url})

    # -- 2. denylist --------------------------------------------------------------
    # Semantics per plan v3.1 §10: do_not_research / blocked_* BLOCK research;
    # do_not_contact does NOT (it may be researched, never contacted - that
    # gate is enforced at the CLI contact command).
    t0 = time.monotonic()
    deny = Denylist.load()
    hit: DenyHit | None = deny.check(domain=domain)
    if hit and hit.category == "do_not_contact":
        notes.append(f"denylist: {hit.reason} (research allowed, contact forbidden)")
        hit = None
    if hit:
        manifest.finish("blocked", outcome={"reason": hit.reason})
        mpath = manifest.write(cfg.paths.runs_dir)
        store.save_run_manifest(run_id, kind="lead_research", status="blocked",
                                manifest_path=str(mpath))
        cid = store.upsert_company(company_name or domain, domain, industry)
        blocked_id = store.upsert_lead(cid, url, run_id, state="blocked")
        store.audit_standalone(actor="agent", action="lead.blocked",
                               entity_type="lead", entity_id=blocked_id,
                               payload={"reason": hit.reason, "category": hit.category})
        return ResearchResult(run_id, url, domain, blocked_id, "blocked", None, None,
                              f"BLOCKED ({hit.category})", None, None, hit.reason, notes)
    manifest.add_step("denylist", elapsed_s=time.monotonic() - t0,
                      detail={"result": "clear" if not notes else "do_not_contact noted"})

    # -- 3. robots + fetch -----------------------------------------------------------
    t0 = time.monotonic()
    robots = _robots_allows(final_url, user_agent=cfg.fetch.user_agent)
    if robots is False:
        return _fail(f"robots.txt disallows fetching {final_url}")
    if robots is None:
        notes.append("robots.txt unavailable; fetch proceeded under hard caps")
    manifest.add_step("robots_check", elapsed_s=time.monotonic() - t0,
                      detail={"robots": robots})

    try:
        with Fetcher(cfg) as fetcher:
            t0 = time.monotonic()
            res = fetcher.fetch(final_url)
        fetched_bytes = res.content
        final_url = res.final_url
        if res.status != 200:
            return _fail(f"http_{res.status}: non-200 response for {final_url}")
    except FetchError as exc:
        return _fail(f"fetch: {exc}")
    manifest.add_input("homepage", res.sha256, url=final_url)
    manifest.add_step("fetch", elapsed_s=time.monotonic() - t0,
                      detail={"status": res.status, "bytes": len(fetched_bytes),
                              "hops": res.hops})

    # -- 4. extract (homepage) -------------------------------------------------------
    t0 = time.monotonic()
    html = fetched_bytes.decode("utf-8", errors="replace")
    signals_home = extract_signals(html, keep_emails=True)
    manifest.add_step("extract", elapsed_s=time.monotonic() - t0)

    # -- 4b. subpage research (about/services/team/contact; capped, robots-checked) ---
    # Same evidence discipline as the homepage: every kept page becomes OBSERVED
    # evidence; pages that fail, are disallowed, or merely mirror the homepage
    # (same SHA-256 soft-404 pattern) are skipped without inventing signals.
    from bam.contacts import extract_linkedin_urls

    max_subpages = int((cfg.raw.get("fetch") or {}).get("max_subpages", 4))
    subpages_fetched: list[str] = []
    sub_evidence: list[Evidence] = []
    team_links: list[str] = []
    page_pairs: list[tuple[str, dict[str, Any]]] = [(final_url, signals_home)]
    t0 = time.monotonic()
    if max_subpages > 0:
        seen_paths = {final_url.rstrip("/")}
        candidates: list[str] = []
        for href in signals_home.get("nav_links", []):
            if href.startswith(("mailto:", "#", "javascript:", "tel:")):
                continue
            abs_url = urljoin(final_url, href)
            if urlparse(abs_url).netloc != urlparse(final_url).netloc:
                continue  # same-host only
            low = abs_url.lower()
            if any(h in low for h in ("/about", "/services", "/team",
                                      "/contact", "/nosotros", "/servicios",
                                      "/equipo", "/contacto")):
                if abs_url.rstrip("/") not in seen_paths:
                    seen_paths.add(abs_url.rstrip("/"))
                    candidates.append(abs_url)
        for path in ("/about", "/services", "/team", "/contact"):
            guess = urljoin(final_url, path)
            if guess.rstrip("/") not in seen_paths:
                seen_paths.add(guess.rstrip("/"))
                candidates.append(guess)
        candidates = candidates[:max_subpages]

        if candidates:
            with Fetcher(cfg) as fetcher:
                for sub_url in candidates:
                    if _robots_allows(sub_url, user_agent=cfg.fetch.user_agent) is False:
                        continue
                    try:
                        sub_res = fetcher.fetch(sub_url)
                    except FetchError:
                        continue
                    if sub_res.status != 200:
                        continue
                    if sub_res.sha256 == res.sha256:
                        continue  # soft-404: identical body to the homepage
                    sub_html = sub_res.content.decode("utf-8", errors="replace")
                    sub_signals = extract_signals(sub_html, keep_emails=True)
                    page_pairs.append((sub_res.final_url, sub_signals))
                    subpages_fetched.append(sub_res.final_url)
                    team_links.extend(extract_linkedin_urls(sub_html))
                    sub_evidence.append(page_evidence(sub_res.final_url, sub_res.content))
    signals = _merge_signals(signals_home, page_pairs)
    signals["subpages_fetched"] = subpages_fetched
    signals["team_links"] = list(dict.fromkeys(team_links))[:10]
    manifest.add_step("subpages", elapsed_s=time.monotonic() - t0,
                      detail={"fetched": subpages_fetched,
                              "candidates": max_subpages})

    # -- 5. evidence --------------------------------------------------------------------
    ev: Evidence = page_evidence(final_url, fetched_bytes)
    evidence_rows = [ev.to_row()] + [sev.to_row() for sev in sub_evidence]
    manifest.add_step("evidence", detail={"sha256": ev.sha256, "status": ev.status,
                                          "pages": len(evidence_rows)})

    # -- 6. claims (closed enums, deterministic; per-page attribution) ----------------------
    known_service_values = {"data-cleaning", "pdf-extraction", "qa-automation", "web-dev"}
    for page_url, sig in page_pairs:
        for s in sig["keyword_services"]:
            if s in known_service_values:
                claims.append({"kind": "detected_service", "value": s, "status": "OBSERVED",
                               "evidence_id": None, "source_url": page_url,
                               "reasoning": f"keyword match on {page_url} (deterministic)"})
        for p in sig["keyword_problems"]:
            claims.append({"kind": "observable_problem", "value": p, "status": "OBSERVED",
                           "evidence_id": None, "source_url": page_url,
                           "reasoning": f"keyword match on {page_url} (deterministic)"})
    manifest.add_step("claims", detail={"count": len(claims)})

    # -- 6b. optional LLM synthesis (1 call max; degrade on any failure) ----------------------
    t0 = time.monotonic()
    llm_res = synthesize({"signals": signals}, cfg.llm)
    if llm_res.ok and llm_res.data:
        for s in llm_res.data["detected_services"]:
            if s not in [c["value"] for c in claims if c["kind"] == "detected_service"]:
                claims.append({"kind": "detected_service", "value": s, "status": "INFERRED",
                               "evidence_id": None,
                               "reasoning": "LLM synthesis (schema-validated)"})
        for p in llm_res.data["observable_problems"]:
            if p not in [c["value"] for c in claims if c["kind"] == "observable_problem"]:
                claims.append({"kind": "observable_problem", "value": p, "status": "INFERRED",
                               "evidence_id": None,
                               "reasoning": "LLM synthesis (schema-validated)"})
        llm_meta = {"summary": llm_res.data["reasoning_summary"],
                    "unknowns": llm_res.data["unknowns"],
                    "risks": llm_res.data["risks"],
                    "usage": llm_res.usage,
                    "degraded_reason": None}
    else:
        llm_meta = {"summary": None, "unknowns": [], "risks": [],
                    "usage": llm_res.usage,
                    "degraded_reason": llm_res.degraded_reason}
    manifest.add_step("llm_synthesis", elapsed_s=time.monotonic() - t0,
                      detail={"ok": llm_res.ok, "degraded": llm_res.degraded_reason})

    claims = apply_observed_wins(claims)

    scored = score_opportunity(
        {
            "signals": signals,
            "claims": claims,
            "evidence": evidence_rows,
            "unknowns": llm_meta.get("unknowns") or [],
            "registry": _registry_services(),
        }
    )
    manifest.add_step("score", detail={"score": scored.score, "decision": scored.decision})

    # -- 7-9. persist ---------------------------------------------------------------------------
    company_name_resolved = (
        company_name or (signals.get("organization") or {}).get("name") or domain
    )
    cid = store.upsert_company(company_name_resolved, domain, industry)
    existing = store.find_lead_by_domain(domain)
    lead_id = existing.id if existing else store.upsert_lead(
        cid, final_url, run_id, state=LeadState.DISCOVERED.value
    )
    # Re-research: a lead previously disqualified/blocked is reset to
    # `discovered` (AUDITED) so AUTOMATIC transitions stay the only path
    # back into the pipeline; research then re-transitions it below.
    store.reset_lead_for_research(lead_id, run_id=run_id)

    ev_id = store.add_evidence(
        lead_id, kind=ev.kind, url=ev.url, sha256=ev.sha256,
        observed_at=ev.observed_at, excerpt=ev.excerpt, status=ev.status,
    )
    ev_id_by_url = {final_url: ev_id}
    for sev in sub_evidence:
        ev_id_by_url[sev.url] = store.add_evidence(
            lead_id, kind=sev.kind, url=sev.url, sha256=sev.sha256,
            observed_at=sev.observed_at, excerpt=sev.excerpt, status=sev.status,
        )
    for c in claims:
        store.add_claim(lead_id, kind=c["kind"], value=c["value"], status=c["status"],
                        evidence_id=ev_id_by_url.get(c.get("source_url"), ev_id),
                        reasoning=c.get("reasoning"))

    if not scored.qualified:
        store.transition_lead(lead_id, "disqualified",
                              reason="empty evidence set: DO NOT QUALIFY",
                              run_id=run_id, score=scored.score,
                              confidence=scored.confidence)
        final_state = "disqualified"
    else:
        store.transition_lead(lead_id, "researched", run_id=run_id, score=scored.score,
                              confidence=scored.confidence)
        final_state = "researched"
        if scored.decision == "queued":
            store.transition_lead(lead_id, "qualified", run_id=run_id)
            matched = [c["value"] for c in claims
                       if c["kind"] == "detected_service"
                       and c["status"] == "OBSERVED"]
            store.transition_lead(lead_id, "approval_required", run_id=run_id,
                                  reason="score >= auto_queue threshold",
                                  recommended_service=_recommend(matched))
            final_state = "approval_required"
        elif scored.decision == "disqualified":
            store.transition_lead(lead_id, "disqualified", run_id=run_id,
                                  reason="; ".join(scored.reasons))
            final_state = "disqualified"

    # -- 8. artifacts ---------------------------------------------------------------------------
    from bam.reporting import lead_artifact_dir

    lead_dir = lead_artifact_dir(cfg.paths.evidence_dir, domain)
    profile = build_profile(
        url=final_url, domain=domain, signals=signals, evidence_rows=evidence_rows,
        claims=claims, scored=scored, llm_meta=llm_meta, run_id=run_id,
    )
    profile_path = write_profile(lead_dir, profile)
    report_path = write_report(lead_dir, profile)
    manifest.add_step("artifacts", detail={"profile": str(profile_path),
                                           "report": str(report_path)})
    manifest.finish("ok", outcome={"lead_id": lead_id, "state": final_state,
                                   "score": scored.score, "decision": scored.decision})
    mpath = manifest.write(cfg.paths.runs_dir)
    store.save_run_manifest(run_id, kind="lead_research", status="ok",
                            manifest_path=str(mpath))
    store.metric(name="lead_researched", value=1, unit="count", lead_id=lead_id)
    store.metric(name="automation_savings_minutes", value=15, unit="minutes",
                 lead_id=lead_id)

    notes.append(f"artifacts: {lead_dir}")
    return ResearchResult(
        run_id=run_id, url=final_url, domain=domain, lead_id=lead_id,
        state=final_state, score=scored.score, confidence=scored.confidence,
        decision=scored.decision, profile_path=profile_path, report_path=report_path,
        llm_degraded_reason=llm_meta.get("degraded_reason"), notes=notes,
    )


def _merge_signals(home: dict[str, Any],
                   page_pairs: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Merge per-page signals into one dict. Homepage fields win; additive
    fields (emails, tech, keywords, forms) union across pages."""
    merged = dict(home)
    for _url, sig in page_pairs[1:]:
        for key in ("emails", "technologies", "keyword_services", "keyword_problems"):
            merged[key] = list(dict.fromkeys([*merged.get(key, []), *sig.get(key, [])]))
        merged["has_contact_form"] = bool(
            merged.get("has_contact_form") or sig.get("has_contact_form"))
        merged["mailto_count"] = int(merged.get("mailto_count") or 0) + int(
            sig.get("mailto_count") or 0)
        if not merged.get("meta_description") and sig.get("meta_description"):
            merged["meta_description"] = sig["meta_description"]
        if not (merged.get("organization") or {}).get("name") and sig.get("organization"):
            merged["organization"] = sig["organization"]
        if not merged.get("contact_url") and sig.get("contact_url"):
            merged["contact_url"] = sig["contact_url"]
    return merged


def _registry_services() -> dict[str, dict[str, Any]]:
    """Enabled services from the registry (for service_match scoring)."""
    try:
        import yaml

        from bam.config import project_root

        data = yaml.safe_load(
            (project_root() / "config" / "service-registry.yaml").read_text(
                encoding="utf-8"
            )
        ) or {}
        return {
            sid: {"enabled": bool(spec.get("enabled", True))}
            for sid, spec in (data.get("services") or {}).items()
        }
    except Exception:
        return {"pdf-to-excel": {"enabled": True}, "excel-cleaner": {"enabled": True}}


_SERVICE_TO_SERVICE_ID = {
    "data-cleaning": "excel-cleaner",
    "pdf-extraction": "pdf-to-excel",
    "qa-automation": "qa-agent",
}


def _recommend(matched: list[str] | None = None) -> str | None:
    """Best enabled service for the detected keyword services (real match)."""
    reg = _registry_services()
    for service in matched or []:
        sid = _SERVICE_TO_SERVICE_ID.get(service)
        if sid and reg.get(sid, {}).get("enabled", False):
            return sid
    return None
