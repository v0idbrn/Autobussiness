"""BAM CLI (plan v3.1 §24): research, leads, approve, contact, deliver, digest.

Exit codes: 0 ok · 1 error · 2 usage/config (mirrors service convention).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path


def _header_safe(value: str) -> str:
    """Collapse every line-break variant (CR/LF/VT/FF/NEL/U+2028/U+2029/NUL)
    so untrusted names (e.g. JSON-LD organization names) cannot inject
    headers into generated .eml drafts, even with lenient MIME parsers."""
    return re.sub(r"[\r\n\x0b\x0c\x85\u2028\u2029\x00]+", " ", value).strip()


def _nonneg_int(text: str) -> int:
    """argparse type: non-negative integer only."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not an integer")
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value

from bam.config import load_config
from bam.store import _utcnow


def _store():
    from bam.store import Store

    return Store(load_config())


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bam",
        description="Business Automation Machine - local-first prospect-to-delivery OS",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # research
    r = sub.add_parser("research", help="9-step lead research pipeline")
    r.add_argument("url", help="company website URL")
    r.add_argument("--company", default=None, help="company name override")
    r.add_argument("--industry", default=None)

    # leads
    leads = sub.add_parser("leads", help="list leads with scores and states")
    leads.add_argument("--state", default=None, help="filter by state")

    # lead (single)
    lead = sub.add_parser("lead", help="view a single lead with full details")
    lead.add_argument("lead_id", type=int)

    # approve (HUMAN transition)
    ap = sub.add_parser("approve", help="record a human approval (HUMAN transition)")
    ap.add_argument("lead_id", type=int)
    ap.add_argument("--reason", default=None)
    ap.add_argument("--decided-by", default="operator")
    ap.add_argument("-y", "--yes", action="store_true", help="skip confirmation")

    # contact outcome (records manual outreach result; never sends anything)
    c = sub.add_parser("contact", help="record a manual contact outcome")
    c.add_argument("lead_id", type=int)
    c.add_argument("outcome", choices=["contacted", "replied", "lost"])
    c.add_argument("--reason", default=None)
    c.add_argument("--decided-by", default="operator")
    c.add_argument("-y", "--yes", action="store_true", help=argparse.SUPPRESS)

    # deliver
    d = sub.add_parser("deliver", help="route a job to a service (subprocess adapter)")
    d.add_argument("input", help="file or directory to process")
    d.add_argument("--service", default=None, help="force service id (else classify)")
    d.add_argument("--client", default=None, help="client name for the job record")
    d.add_argument("--quoted", type=float, default=None)
    d.add_argument("--agreed", type=float, default=None)
    d.add_argument("--currency", default="EUR")
    d.add_argument("--yes", action="store_true", help="skip confirmation")

    # jobs / payment
    jobs = sub.add_parser("jobs", help="list jobs")
    pay = sub.add_parser("pay", help="record a payment for a job (record only)")
    pay.add_argument("job_id", type=int)
    pay.add_argument("--amount", type=float, required=True)
    pay.add_argument("--currency", default="EUR")
    pay.add_argument("--date", default=None)

    # digest
    sub.add_parser("digest", help="business metrics (conversion, revenue, hours)")

    # backup / restore
    bk = sub.add_parser("backup", help="create a consistent database backup")
    bk.add_argument("--dir", default=None, help="backup directory (default: ../backups)")
    rs = sub.add_parser("restore", help="restore database from backup")
    rs.add_argument("backup_path", help="path to backup .db file")

    # services / doctor
    sub.add_parser("services", help="service registry + preflight status")
    sub.add_parser("doctor", help="config + environment check")

    # --- NEW COMMERCIAL COMMANDS ---

    # discover
    disc = sub.add_parser("discover", help="discover companies from URLs, CSV, RSS, or text")
    disc.add_argument("--urls", nargs="*", help="list of URLs to research")
    disc.add_argument("--csv", default=None, help="CSV file with company data")
    disc.add_argument("--text", default=None, help="free-form text containing URLs")
    disc.add_argument("--source", choices=["urls", "csv", "text", "rss"], default=None,
                      help="discovery source; 'rss' requires --query")
    disc.add_argument("--query", default=None, help="search query for --source rss")
    disc.add_argument("--limit", type=int, default=10, help="max companies to discover")
    disc.add_argument("--campaign", nargs="*", metavar="SERVICE",
                      help="commercial-intent campaign for services "
                           "(pdf_to_excel | excel_cleaning | qa_automation); "
                           "web search primary, RSS secondary, publishers filtered")
    disc.add_argument("--per-query", type=int, default=5,
                      help="max candidates per catalog query (campaign mode)")
    disc.add_argument("--directory", action="append", default=[], metavar="URL",
                      help="curated directory/association/member-list page to mine "
                           "(campaign mode; repeatable)")

    # ingest
    ing = sub.add_parser("ingest", help="ingest discovered companies into the pipeline")
    ing.add_argument("source", help="URL, CSV path, or text file path")
    ing.add_argument("--type", choices=["url", "csv", "text", "auto"], default="auto")

    # qualify
    ql = sub.add_parser("qualify", help="manually qualify a lead")
    ql.add_argument("lead_id", type=int)
    ql.add_argument("--reason", default=None)

    # sales-brief
    sb = sub.add_parser("sales-brief", help="generate a sales brief for a lead")
    sb.add_argument("lead_id", type=int)

    # draft-outreach
    do = sub.add_parser("draft-outreach", help="generate an outreach draft for a lead")
    do.add_argument("lead_id", type=int)
    do.add_argument("--channel", choices=["email", "linkedin", "contact_form"],
                    default=None, help="force channel")
    do.add_argument("--mailto", action="store_true",
                    help="open the default email client pre-filled with the draft")
    do.add_argument("--file", action="store_true",
                    help="write the draft as a .eml file under data/drafts/")

    # approve-contact (alias for approve with outreach context)
    ac = sub.add_parser("approve-contact", help="approve outreach to a lead")
    ac.add_argument("lead_id", type=int)
    ac.add_argument("--reason", default=None)
    ac.add_argument("-y", "--yes", action="store_true", help="skip confirmation")

    # response
    rp = sub.add_parser("response", help="record and classify a prospect response")
    rp.add_argument("lead_id", type=int)
    rp.add_argument("response_text", help="the response text from the prospect")
    rp.add_argument("--channel", default="email")
    rp.add_argument("--contact-id", type=int, default=None)

    # classify-response
    cr = sub.add_parser("classify-response", help="classify a response without recording")
    cr.add_argument("response_text", help="the response text to classify")
    cr.add_argument("--outreach", default="", help="the original outreach message")

    # follow-ups
    fu = sub.add_parser("followups", help="show pending follow-ups")
    fu.add_argument("--overdue", action="store_true", help="show only overdue")

    # quote
    qt = sub.add_parser("quote", help="generate a quote for a lead")
    qt.add_argument("lead_id", type=int)
    qt.add_argument("--service", default=None, help="service id (auto-detect from lead)")
    qt.add_argument("--scope", default=None, help="scope description")
    qt.add_argument("--files", type=_nonneg_int, default=None, help="estimated files")
    qt.add_argument("--pages", type=_nonneg_int, default=None, help="estimated pages")
    qt.add_argument("--rows", type=_nonneg_int, default=None, help="estimated rows")
    qt.add_argument("--complexity", choices=["simple", "moderate", "complex"], default=None)

    # approve-quote
    aq = sub.add_parser("approve-quote", help="approve a draft quote")
    aq.add_argument("quote_id", type=int)
    aq.add_argument("-y", "--yes", action="store_true", help="skip confirmation")

    # next
    sub.add_parser("next", help="show the next recommended action")

    # clients
    sub.add_parser("clients", help="list clients")

    return p


def _print_table(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("(none)")
        return
    widths = {c: max(len(c), *(len(str(r.get(c) or "")) for r in rows)) for c in columns}
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    print(header)
    print("-" * len(header))
    for r in rows:
        print("  ".join(str(r.get(c) or "").ljust(widths[c]) for c in columns))


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252; service output may contain any bytes.
    # (Minimal pattern copied from PDF Engine's cli - never imported.)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - best effort, never fatal
                pass
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "research":
            from bam.pipeline import research

            print(f"Researching: {args.url}")
            print()
            with _store() as store:
                res = research(args.url, store=store, company_name=args.company,
                               industry=args.industry)
            print(f"RESULT")
            print(f"  Lead:     #{res.lead_id}")
            print(f"  State:    {res.state}")
            print(f"  Score:    {res.score if res.score is not None else '-'}")
            print(f"  Confidence: {res.confidence or '-'}")
            print(f"  Decision: {res.decision}")
            if res.llm_degraded_reason:
                print(f"  LLM:      deterministic mode ({res.llm_degraded_reason})")
            for n in res.notes:
                print(f"  Note:     {n}")
            if res.report_path:
                print(f"  Report:   {res.report_path}")
            return 0

        if args.command == "leads":
            rows = [
                {"id": l.id, "state": l.state, "score": l.score, "conf": l.confidence,
                 "company": l.company_name, "domain": l.domain}
                for l in _store().list_leads()
            ]
            if args.state:
                rows = [r for r in rows if r["state"] == args.state]
            _print_table(rows, ["id", "state", "score", "conf", "company", "domain"])
            return 0

        if args.command == "approve":
            from bam.store import TransitionError

            if not args.yes:
                answer = input(f"Approve lead {args.lead_id} for contact? [y/N] ")
                if answer.strip().lower() not in ("y", "yes"):
                    print("aborted")
                    return 2
            store = _store()
            lead = store.get_lead(args.lead_id)
            if not lead:
                print(f"lead {args.lead_id} not found", file=sys.stderr)
                return 2
            try:
                store.record_approval(
                    subject_type="lead", subject_id=args.lead_id, kind="commercial",
                    to_state="approved", decided_by=args.decided_by, reason=args.reason,
                )
            except TransitionError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(f"lead {args.lead_id}: {lead.state} -> approved "
                  f"(transaction recorded)")
            return 0

        if args.command == "contact":
            from bam.store import TransitionError

            store = _store()
            lead = store.get_lead(args.lead_id)
            if not lead:
                print(f"lead {args.lead_id} not found", file=sys.stderr)
                return 2
            if lead.state != "approved" and args.outcome == "contacted":
                print("error: lead must be approved before recording contact",
                      file=sys.stderr)
                return 2
            if args.outcome == "contacted":
                from bam.denylist import Denylist

                hit = Denylist.load().check(domain=lead.domain,
                                            company=lead.company_name)
                if hit:
                    print(f"error: denylist forbids contacting this lead "
                          f"({hit.reason})", file=sys.stderr)
                    return 2
            try:
                if args.outcome == "contacted":
                    store.record_approval(
                        subject_type="lead", subject_id=args.lead_id,
                        kind="external_action", to_state="contacted",
                        decided_by=args.decided_by, reason=args.reason,
                    )
                else:
                    store.transition_lead(
                        args.lead_id, args.outcome, actor="human",
                        reason=f"contact outcome: {args.outcome} (recorded by "
                               f"{args.decided_by})",
                    )
                store.metric(name="contact_recorded", value=1, unit="count",
                             lead_id=args.lead_id)
            except TransitionError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(f"lead {args.lead_id}: outcome '{args.outcome}' recorded")
            return 0

        if args.command == "deliver":
            from bam.adapters import AdapterError, run_service
            from bam.router import ClassificationError, classify_job, load_registry

            input_path = Path(args.input)
            if not input_path.exists():
                print(f"input not found: {input_path}", file=sys.stderr)
                return 2
            registry = load_registry()
            try:
                sid = args.service or classify_job(input_path)
            except ClassificationError as exc:
                print(f"classification error: {exc}", file=sys.stderr)
                return 2
            spec = registry.get(sid)
            if not spec:
                print(f"unknown service: {sid}", file=sys.stderr)
                return 2
            if not args.yes:
                answer = input(
                    f"Run {sid}@{spec.contract_version} on {input_path.name}? [y/N] "
                )
                if answer.strip().lower() not in ("y", "yes"):
                    print("aborted")
                    return 2
            store = _store()
            client_id = store.create_client(args.client) if args.client else None
            job_id = store.create_job(
                service_id=sid, title=input_path.name, input_path=str(input_path),
                client_id=client_id, quoted_amount=args.quoted,
                agreed_amount=args.agreed, currency=args.currency,
            )
            store.update_job(job_id, state="classified")
            store.update_job(job_id, state="assigned")
            result = run_service(spec, input_path, store=store, job_id=job_id)
            if not result.ok:
                print(f"\nDELIVERY FAILED")
                print(f"  Service: {sid}@{spec.contract_version}")
                print(f"  Error: {result.error}")
                return 1
            print(f"\nDELIVERY SUCCESS")
            print(f"  Service:  {sid}@{spec.contract_version}")
            print(f"  Input:    {input_path.name}")
            print(f"  Client:   {args.client or '(none)'}")
            if args.agreed:
                print(f"  Agreed:   {args.currency} {args.agreed}")
            print(f"  Job:      #{job_id}")
            print(f"  Artifacts:")
            for a in result.artifacts:
                print(f"    - {a['path']} ({a['sha256'][:12]}\u2026)")
            print(f"  Status:   DELIVERED")
            return 0

        if args.command == "jobs":
            rows = [
                {"id": j.id, "service": j.service_id, "state": j.state,
                 "title": j.title, "agreed": j.agreed_amount, "paid": j.paid_amount}
                for j in _store().list_jobs()
            ]
            _print_table(rows, ["id", "service", "state", "title", "agreed", "paid"])
            return 0

        if args.command == "pay":
            store = _store()
            try:
                store.record_payment(args.job_id, paid_amount=args.amount,
                                     currency=args.currency, payment_date=args.date)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            print(f"job {args.job_id}: payment recorded ({args.amount} {args.currency})")
            return 0

        if args.command == "digest":
            import json

            print(json.dumps(_store().digest(), indent=2, ensure_ascii=False))
            return 0

        if args.command == "backup":
            store = _store()
            backup_dir = Path(args.dir) if args.dir else None
            result = store.backup(backup_dir)
            print(f"backup created : {result['path']}")
            print(f"size           : {result['size']:,} bytes")
            print(f"sha256         : {result['sha256'][:32]}...")
            print(f"verified       : integrity ok, "
                  f"{result['tables']} tables / {result['rows']:,} rows match live DB, "
                  "restore drill passed")
            pruned = result.get("pruned", [])
            if pruned:
                print(f"pruned         : {len(pruned)} old backup(s) "
                      f"(keep={result['kept']})")
                for p in pruned:
                    print(f"                 {p}")
            return 0

        if args.command == "restore":
            store = _store()
            backup_path = Path(args.backup_path)
            store.restore(backup_path)
            print(f"restored from  : {backup_path}")
            print("safety copy created for pre-restore state")
            return 0

        if args.command == "services":
            from bam.adapters import preflight
            from bam.router import load_registry

            registry = load_registry()
            rows = []
            for sid, spec in registry.items():
                ok, detail = preflight(spec) if spec.enabled else (False, "disabled")
                rows.append({
                    "id": sid, "ver": spec.contract_version, "enabled": spec.enabled,
                    "risk": spec.risk, "preflight": "ok" if ok else "FAIL",
                    "detail": detail[:40],
                })
            _print_table(rows, ["id", "ver", "enabled", "risk", "preflight", "detail"])
            return 0

        if args.command == "doctor":
            cfg = load_config()
            print("config          : ok")
            print(f"database        : {cfg.paths.database}")
            print(f"runtime deps    : httpx, pyyaml (only)")
            from bam.adapters import preflight
            from bam.router import load_registry

            for sid, spec in load_registry().items():
                ok, detail = preflight(spec) if spec.enabled else (False, "disabled")
                print(f"service {sid:<14}: {'ok' if ok else 'FAIL'} - {detail[:60]}")
            return 0

        # --- NEW COMMERCIAL COMMANDS ---

        if args.command == "discover":
            from bam.discovery import (
                commercial_candidates, deduplicate, discover_from_csv,
                discover_from_rss, discover_from_text, discover_from_urls,
                filter_denied, filter_publishers,
            )

            if getattr(args, "campaign", None):
                # Commercial-intent campaign (Discovery V2 + §30-§31): the run
                # is RECORDED so source/query quality accumulates over time.
                cfg = load_config()
                dlim = (cfg.raw.get("discovery") or {})
                max_candidates = int(dlim.get("max_candidates", 50))
                store = _store()
                campaign_id = store.start_campaign(
                    services=list(args.campaign),
                    sources=("directory" if args.directory else []) + ["web_search", "rss"],
                    markets=list(dlim.get("markets", [])) or None,
                    limits={"per_query": args.per_query,
                            "max_candidates": max_candidates})
                allowed = commercial_candidates(
                    args.campaign, per_query=max(1, args.per_query),
                    directories=list(args.directory or []))
                allowed = deduplicate(allowed)[:max_candidates]
                allowed, denied = filter_denied(allowed)
                companies_out, publishers = filter_publishers(allowed)
                # per-source funnel stats for the feedback loop
                per_source: dict[str, dict[str, int]] = {}
                for c in companies_out:
                    s = per_source.setdefault(c.source, {"candidates": 0})
                    s["candidates"] += 1
                store.finish_campaign(
                    campaign_id,
                    results={"candidates": len(companies_out),
                             "publishers_filtered": len(publishers),
                             "denied": len(denied)},
                    lead_ids=[],
                    query_stats=[
                        {"query": "(campaign)", "source": src,
                         "service": args.campaign[0], **counts}
                        for src, counts in per_source.items()] or None)
                print(f"CAMPAIGN #{campaign_id}: {len(args.campaign)} service(s), "
                      f"per-query cap {args.per_query}")
                print(f"CANDIDATES: {len(companies_out) + len(publishers)} "
                      f"-> companies {len(companies_out)}, "
                      f"publishers filtered {len(publishers)}")
                for c, why in publishers:
                    print(f"  [publisher] {c.domain}: {why}")
                for c in companies_out:
                    print(f"  {c.domain:35s} via {c.industry or c.source:16s} "
                          f"{c.evidence[:60]}")
                if denied:
                    print(f"DENIED: {len(denied)} (denylist)")
                print("\nResearch the interesting ones with:")
                print("  uv run bam research https://<domain>")
                return 0

            companies = []
            if args.source == "rss":
                if not args.query:
                    print("error: --source rss requires --query", file=sys.stderr)
                    return 2
                companies.extend(discover_from_rss(args.query, limit=args.limit))
            if args.urls:
                companies.extend(discover_from_urls(args.urls))
            if args.csv:
                companies.extend(discover_from_csv(Path(args.csv)))
            if args.text:
                companies.extend(discover_from_text(args.text))

            if not companies:
                print("No companies discovered. Provide --urls, --csv, --text, "
                      "or --source rss --query <q>.")
                return 2

            companies = deduplicate(companies)[:args.limit]
            allowed, denied = filter_denied(companies)

            print(f"DISCOVERED: {len(allowed)} companies")
            print()
            for c in allowed:
                print(f"  {c.name}")
                print(f"    domain:   {c.domain}")
                print(f"    url:      {c.url}")
                print(f"    source:   {c.source}")
                print(f"    evidence: {c.evidence[:80]}")
                print()
            if denied:
                print(f"DENIED: {len(denied)} companies (denylist)")
            return 0

        if args.command == "ingest":
            from bam.discovery import (
                deduplicate, discover_from_csv, discover_from_text,
                discover_from_urls, filter_denied,
            )
            from bam.manifest import new_run_id
            from bam.pipeline import research

            source_path = Path(args.source)
            companies = []

            if args.type == "auto":
                if source_path.exists() and source_path.suffix.lower() == ".csv":
                    args.type = "csv"
                elif source_path.exists():
                    args.type = "text"
                elif args.source.startswith("http"):
                    args.type = "url"
                else:
                    args.type = "text"

            if args.type == "url":
                companies = discover_from_urls([args.source])
            elif args.type == "csv":
                companies = discover_from_csv(source_path)
            elif args.type == "text":
                if source_path.exists():
                    content = source_path.read_text(encoding="utf-8", errors="replace")
                    companies = discover_from_text(content)
                else:
                    companies = discover_from_text(args.source)

            companies = deduplicate(companies)
            allowed, denied = filter_denied(companies)

            print(f"Ingesting {len(allowed)} companies...")
            store = _store()
            results = []
            for c in allowed:
                try:
                    res = research(c.url, store=store, company_name=c.name)
                    results.append(res)
                    print(f"  #{res.lead_id} {c.name}: {res.state} (score={res.score})")
                except Exception as exc:
                    print(f"  {c.name}: FAILED ({exc})")

            print(f"\nDone: {len(results)} researched, {len(denied)} denied")
            return 0

        if args.command == "lead":
            store = _store()
            lead = store.get_lead(args.lead_id)
            if not lead:
                print(f"lead {args.lead_id} not found", file=sys.stderr)
                return 2

            evidence = store.evidence_for_lead(lead.id)
            claims = store.claims_for_lead(lead.id)
            contacts = store.list_contacts(lead.id)
            interactions = store.list_interactions(lead.id)
            quotes = store.list_quotes(lead.id)

            print(f"LEAD #{lead.id}")
            print(f"  Company:   {lead.company_name}")
            print(f"  Domain:    {lead.domain or '-'}")
            print(f"  State:     {lead.state}")
            print(f"  Score:     {lead.score if lead.score is not None else '-'}")
            print(f"  Confidence: {lead.confidence or '-'}")
            print(f"  Service:   {lead.recommended_service or '-'}")
            print(f"  Reason:    {lead.reason or '-'}")
            print()
            print(f"  EVIDENCE ({len(evidence)} items):")
            for e in evidence[:5]:
                print(f"    [{e['status']}] {e['kind']}: {(e.get('excerpt') or '')[:60]}")
            print()
            print(f"  CLAIMS ({len(claims)} items):")
            for c in claims[:5]:
                print(f"    [{c['status']}] {c['kind']}: {c['value']}")
            print()
            if contacts:
                print(f"  CONTACTS ({len(contacts)}):")
                for ct in contacts:
                    parts = []
                    if ct.name:
                        parts.append(ct.name)
                    if ct.role:
                        parts.append(f"({ct.role})")
                    if ct.email:
                        parts.append(ct.email)
                    if ct.phone:
                        parts.append(ct.phone)
                    print(f"    {' '.join(parts) or '(no details)'} [{ct.source}]")
                print()
            if interactions:
                print(f"  INTERACTIONS ({len(interactions)}):")
                for i in interactions[-3:]:
                    print(f"    [{i.direction}] {i.kind}: {i.outcome or '-'} ({i.timestamp})")
                print()
            if quotes:
                print(f"  QUOTES ({len(quotes)}):")
                for q in quotes:
                    print(f"    {q.service_id}: EUR {q.suggested_price or '-'} [{q.state}]")
            return 0

        if args.command == "qualify":
            from bam.store import TransitionError

            store = _store()
            lead = store.get_lead(args.lead_id)
            if not lead:
                print(f"lead {args.lead_id} not found", file=sys.stderr)
                return 2
            if lead.state not in ("discovered", "researched", "disqualified"):
                print(f"error: lead must be in 'discovered', 'researched', or 'disqualified' "
                      f"state, got '{lead.state}'", file=sys.stderr)
                return 2
            try:
                if lead.state == "discovered":
                    store.transition_lead(lead.id, "researched", run_id="manual_qualify",
                                         reason=args.reason or "manual qualification")
                elif lead.state == "disqualified":
                    store.reset_lead_for_research(lead.id, run_id="manual_qualify")
                    store.transition_lead(lead.id, "researched", run_id="manual_qualify",
                                         reason=args.reason or "manual qualification")
                store.transition_lead(lead.id, "qualified", run_id="manual_qualify",
                                     reason=args.reason or "manual qualification")
                # Complete the automatic chain to the human gate: qualified ->
                # approval_required is AUTOMATIC; without this the lead strands.
                store.transition_lead(lead.id, "approval_required", run_id="manual_qualify",
                                     reason="queued for human approval")
                print(f"lead {lead.id}: {lead.state} -> approval_required")
            except TransitionError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            return 0

        if args.command == "sales-brief":
            from bam.commercial import generate_sales_brief
            from bam.reporting import build_profile

            store = _store()
            lead = store.get_lead(args.lead_id)
            if not lead:
                print(f"lead {args.lead_id} not found", file=sys.stderr)
                return 2

            evidence = store.evidence_for_lead(lead.id)
            claims = store.claims_for_lead(lead.id)

            # Load signals from profile if exists
            cfg = load_config()
            profile_path = cfg.paths.evidence_dir / (lead.domain or "unknown") / "profile.json"
            signals = {}
            if profile_path.exists():
                try:
                    profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
                    signals = profile_data.get("signals", {})
                except Exception:
                    pass

            brief = generate_sales_brief(
                lead_data={
                    "company_name": lead.company_name,
                    "domain": lead.domain,
                    "source_url": "",
                    "score": lead.score,
                    "confidence": lead.confidence,
                    "recommended_service": lead.recommended_service,
                    "reason": lead.reason,
                },
                evidence=evidence,
                claims=claims,
                signals=signals,
            )

            # WHY THIS LEAD + tier + productized offer (sales machine §3/§10-§12)
            from bam.copilot import explain_why, suggested_contact_roles
            from bam.signals import recommend_offer

            commercial = (signals.get("commercial") or {})
            contactability = commercial.get("contactability") or {}
            why = explain_why(
                {"id": lead.id, "state": lead.state, "score": lead.score,
                 "recommended_service": lead.recommended_service,
                 "company_name": lead.company_name, "domain": lead.domain},
                evidence, commercial, contactability)
            offer = recommend_offer(lead.recommended_service)

            print(f"SALES BRIEF — {lead.company_name}")
            print(f"{'=' * 50}")
            print(f"Who:              {brief.who}")
            print(f"What they do:     {brief.what_they_do}")
            print(f"Tier:             {why.headline}")
            print("WHY PURSUE:")
            for b in why.bullets:
                print(f"  - {b}")
            for u in why.unknowns:
                print(f"  - UNKNOWN: {u}")
            if offer:
                print(f"WHAT TO SELL:     {offer['name']}")
                print(f"  offer:            {offer['pitch']}")
            print(f"Service:          {brief.recommended_service}")
            print(f"Evidence:         {brief.evidence_summary}")
            print(f"Contact:          {brief.contact_path}")
            if lead.recommended_service:
                print(f"Contact roles:    {', '.join(suggested_contact_roles(lead.recommended_service))}")
            print(f"Message goal:     {brief.first_message_goal}")
            print(f"CTA:              {brief.suggested_cta}")
            print(f"Do NOT claim:     {brief.what_not_to_claim}")
            print(f"Unknowns:         {', '.join(brief.unknowns)}")
            print(f"LLM used:         {brief.llm_used}")
            return 0

        if args.command == "draft-outreach":
            from bam.commercial import generate_outreach, generate_sales_brief

            store = _store()
            lead = store.get_lead(args.lead_id)
            if not lead:
                print(f"lead {args.lead_id} not found", file=sys.stderr)
                return 2

            evidence = store.evidence_for_lead(lead.id)
            claims = store.claims_for_lead(lead.id)

            cfg = load_config()
            profile_path = cfg.paths.evidence_dir / (lead.domain or "unknown") / "profile.json"
            signals = {}
            if profile_path.exists():
                try:
                    profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
                    signals = profile_data.get("signals", {})
                except Exception:
                    pass

            brief = generate_sales_brief(
                lead_data={
                    "company_name": lead.company_name,
                    "domain": lead.domain,
                    "source_url": "",
                    "score": lead.score,
                    "confidence": lead.confidence,
                    "recommended_service": lead.recommended_service,
                    "reason": lead.reason,
                },
                evidence=evidence,
                claims=claims,
                signals=signals,
            )

            draft = generate_outreach(
                company_name=lead.company_name,
                domain=lead.domain or "",
                evidence=evidence,
                brief=brief,
                contact_path=brief.contact_path,
            )

            if args.channel:
                draft.channel = args.channel

            # -- delivery flags (draft is NEVER sent automatically) -------------
            if args.mailto:
                import webbrowser
                from urllib.parse import quote
                mailto = f"mailto:?subject={quote(draft.subject or '')}" \
                         f"&body={quote(draft.body)}"
                webbrowser.open(mailto)
                print("opened email client with the draft pre-filled "
                      "(nothing is sent automatically)")
            if args.file:
                drafts_dir = load_config().paths.jobs_dir.parent / "drafts"
                drafts_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                eml_path = drafts_dir / f"{args.lead_id}_{stamp}.eml"
                eml_path.write_text(
                    "From: operator@local\r\n"
                    f"To: {_header_safe(lead.company_name or '')} <{draft.channel}>\r\n"
                    f"Subject: {_header_safe(draft.subject or '')}\r\n"
                    "X-Unsent: 1\r\n"
                    "MIME-Version: 1.0\r\n"
                    "Content-Type: text/plain; charset=utf-8\r\n"
                    "\r\n"
                    f"{draft.body}\r\n",
                    encoding="utf-8",
                    newline="",  # keep explicit CRLF; no platform translation
                )
                print(f"draft saved: {eml_path}")

            print(f"OUTREACH DRAFT — {lead.company_name}")
            print(f"{'=' * 50}")
            print(f"Channel:           {draft.channel}")
            if draft.subject:
                print(f"Subject:           {draft.subject}")
            print(f"Personalization:   {draft.personalization}")
            print()
            print(f"MESSAGE:")
            print(draft.body)
            print()
            print(f"CTA: {draft.cta}")
            print(f"LLM used: {draft.llm_used}")
            return 0

        if args.command == "approve-contact":
            from bam.store import TransitionError

            if not args.yes:
                answer = input(f"Approve contact for lead {args.lead_id}? [y/N] ")
                if answer.strip().lower() not in ("y", "yes"):
                    print("aborted")
                    return 2
            store = _store()
            lead = store.get_lead(args.lead_id)
            if not lead:
                print(f"lead {args.lead_id} not found", file=sys.stderr)
                return 2
            try:
                store.record_approval(
                    subject_type="lead", subject_id=args.lead_id,
                    kind="external_action", to_state="approved",
                    decided_by="operator", reason=args.reason or "outreach approved",
                )
            except TransitionError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(f"lead {args.lead_id}: {lead.state} -> approved (contact approved)")
            return 0

        if args.command == "response":
            from bam.commercial import classify_response

            store = _store()
            lead = store.get_lead(args.lead_id)
            if not lead:
                print(f"lead {args.lead_id} not found", file=sys.stderr)
                return 2

            # Classify the response
            classification = classify_response(args.response_text)

            # Record the interaction
            store.add_interaction(
                lead.id,
                contact_id=args.contact_id,
                channel=args.channel,
                direction="inbound",
                kind="response",
                subject=None,
                body=args.response_text,
                outcome=classification.classification,
                next_action=classification.next_action,
            )

            # Transition lead based on classification
            if classification.classification in ("positive", "pricing", "question"):
                if lead.state == "contacted":
                    store.transition_lead(lead.id, "replied",
                                         reason=f"response classified as {classification.classification}")
                elif lead.state == "replied":
                    store.transition_lead(lead.id, "negotiating",
                                         reason=f"response classified as {classification.classification}")
            elif classification.classification == "not_interested":
                store.transition_lead(lead.id, "lost",
                                     reason="prospect not interested")
            elif classification.classification == "do_not_contact":
                store.transition_lead(lead.id, "do_not_contact",
                                     reason="prospect requested no contact")

            # Create follow-up if needed
            if classification.needs_response:
                due = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
                store.create_follow_up(
                    lead.id,
                    kind="reply_pending",
                    due_date=due,
                    reason=classification.summary,
                    recommended_action=classification.next_action,
                )

            print(f"RESPONSE CLASSIFIED — Lead #{lead.id}")
            print(f"  Classification:  {classification.classification}")
            print(f"  Confidence:      {classification.confidence}")
            print(f"  Summary:         {classification.summary}")
            print(f"  Next action:     {classification.next_action}")
            print(f"  Needs response:  {classification.needs_response}")
            # Objection playbook (sales machine §20): WHAT THIS MEANS / WHAT TO SAY
            from bam.copilot import playbook_for
            play = playbook_for(classification.classification)
            if play:
                print()
                print("  PLAYBOOK")
                print(f"    what this means : {play['means']}")
                print(f"    recommended say : {play['say']}")
                print(f"    next            : {play['next']}")
                if play["quote_needed"]:
                    print(f"    quote needed    : YES — run: bam quote {lead.id}")
            return 0

        if args.command == "classify-response":
            from bam.commercial import classify_response

            classification = classify_response(args.response_text, args.outreach)
            print(f"Classification:  {classification.classification}")
            print(f"Confidence:      {classification.confidence}")
            print(f"Summary:         {classification.summary}")
            print(f"Next action:     {classification.next_action}")
            print(f"Needs response:  {classification.needs_response}")
            from bam.copilot import playbook_for
            play = playbook_for(classification.classification)
            if play:
                print()
                print("PLAYBOOK")
                print(f"  what this means : {play['means']}")
                print(f"  recommended say : {play['say']}")
                print(f"  next            : {play['next']}")
            return 0

        if args.command == "followups":
            store = _store()
            if args.overdue:
                follow_ups = store.overdue_follow_ups()
                title = "OVERDUE FOLLOW-UPS"
            else:
                follow_ups = store.pending_follow_ups()
                title = "PENDING FOLLOW-UPS"

            print(f"{title}")
            print(f"{'=' * 50}")
            if not follow_ups:
                print("(none)")
                return 0
            for fu in follow_ups:
                lead = store.get_lead(fu.lead_id)
                company = lead.company_name if lead else f"lead#{fu.lead_id}"
                status = "OVERDUE" if fu.due_date and fu.due_date < _utcnow()[:10] else "pending"
                print(f"  [{status}] {company} — {fu.kind}")
                print(f"    Due: {fu.due_date or '-'}")
                print(f"    Reason: {fu.reason or '-'}")
                print(f"    Action: {fu.recommended_action or '-'}")
                print()
            return 0

        if args.command == "quote":
            from bam.commercial import generate_quote

            store = _store()
            lead = store.get_lead(args.lead_id)
            if not lead:
                print(f"lead {args.lead_id} not found", file=sys.stderr)
                return 2

            service = args.service or lead.recommended_service or "pdf-to-excel"

            evidence = store.evidence_for_lead(lead.id)

            suggestion = generate_quote(
                service_id=service,
                company_name=lead.company_name,
                scope=args.scope,
                estimated_files=args.files,
                estimated_pages=args.pages,
                estimated_rows=args.rows,
                complexity=args.complexity,
                evidence=evidence,
            )

            # Create quote record
            quote_id = store.create_quote(
                lead.id,
                service_id=service,
                scope=args.scope,
                estimated_files=args.files,
                estimated_pages=args.pages,
                estimated_rows=args.rows,
                complexity=args.complexity,
                suggested_price=suggestion.suggested_price,
                suggested_currency=suggestion.currency,
                delivery_estimate=suggestion.delivery_estimate,
                assumptions=suggestion.assumptions,
                exclusions=suggestion.exclusions,
            )

            print(f"QUOTE — {lead.company_name}")
            print(f"{'=' * 50}")
            print(f"  Quote:       #{quote_id}")
            print(f"  Service:     {service}")
            print(f"  Price:       {suggestion.currency} {suggestion.suggested_price}")
            print(f"  Delivery:    {suggestion.delivery_estimate}")
            print(f"  Assumptions: {suggestion.assumptions}")
            print(f"  Exclusions:  {suggestion.exclusions}")
            print(f"  Notes:       {suggestion.notes}")
            print(f"  LLM used:    {suggestion.llm_used}")
            print(f"\n  State: DRAFT (approve with: bam approve-quote {quote_id})")
            return 0

        if args.command == "approve-quote":
            from bam.store import TransitionError

            store = _store()
            quote = store.get_quote(args.quote_id)
            if not quote:
                print(f"quote {args.quote_id} not found", file=sys.stderr)
                return 2

            if not args.yes:
                answer = input(
                    f"Approve quote #{args.quote_id} "
                    f"({quote.suggested_currency} {quote.suggested_price})? [y/N] "
                )
                if answer.strip().lower() not in ("y", "yes"):
                    print("aborted")
                    return 2

            store.update_quote(args.quote_id, state="approved", approved_by="operator")
            print(f"quote {args.quote_id}: draft -> approved")
            return 0

        if args.command == "next":
            # Daily Sales Queue (sales machine §17): the commercial cockpit.
            # WHAT matters, WHY, contact, NEXT ACTION — with anti-spam caps.
            from bam.copilot import build_daily_queue, playbook_for
            from bam.signals import recommend_offer

            store = _store()
            followups = store.pending_follow_ups()
            candidates = store.queue_candidates()
            entries, stats = build_daily_queue(candidates, limit=5)

            print("TODAY'S SALES QUEUE")
            print("=" * 60)
            if not entries:
                print("(empty) Run 'bam discover --campaign <service>' to find")
                print("companies, then 'bam research <url>' to investigate them.")
            shown = 0
            for e in entries:
                if e.tier == "D" and shown >= 5:
                    continue  # never bury the operator in rejects
                shown += 1
                print(f"\n{shown}. {e.company}" + (f" ({e.domain})" if e.domain else ""))
                print(f"   STATE: {e.state}  SCORE: {e.score if e.score is not None else '-'}"
                      f"  TIER: {e.tier}  ACTION: {e.action}")
                if e.service:
                    offer = recommend_offer(e.service)
                    print(f"   SELL: {offer['name'] if offer else e.service}")
                if e.contact:
                    print(f"   CONTACT: {e.contact}")
                if e.why.bullets:
                    print("   WHY:")
                    for b in e.why.bullets[:3]:
                        print(f"     - {b}")
                if e.why.unknowns:
                    print("   UNKNOWN:")
                    for u in e.why.unknowns[:2]:
                        print(f"     - {u}")
                if e.tier == "D":
                    break
            print()
            print("RECOMMENDED TODAY")
            print("=" * 60)
            contactable_a = sum(1 for e in entries if e.tier == "A")
            research_b = sum(1 for e in entries if e.tier == "B")
            print(f"Contact-ready (A): {contactable_a}   Needs research (B): {research_b}")
            print(f"Follow-ups due: {len(followups)}")
            if followups:
                for fu in followups[:3]:
                    lead = store.get_lead(fu.lead_id)
                    company = lead.company_name if lead else f"lead#{fu.lead_id}"
                    print(f"  FOLLOW UP TODAY: {company} — {fu.kind}"
                          f" (due {fu.due_date})")
                    print(f"    reason: {(fu.reason or '-')[:90]}")
                    print(f"    action: {(fu.recommended_action or '-')[:90]}")
            quotes_pending = store._conn().execute(
                "SELECT COUNT(*) FROM quotes WHERE state='approved'").fetchone()[0]
            print(f"Quotes pending: {quotes_pending}")
            return 0

        if args.command == "clients":
            store = _store()
            from bam.store import _row_to
            conn = store._conn()
            rows = conn.execute(
                "SELECT cl.*, l.state AS lead_state, l.score"
                " FROM clients cl LEFT JOIN leads l ON l.id = cl.lead_id"
                " ORDER BY cl.id"
            ).fetchall()
            if not rows:
                print("(no clients)")
                return 0
            print(f"CLIENTS ({len(rows)})")
            print(f"{'=' * 50}")
            for r in rows:
                print(f"  #{r['id']} {r['name']}")
                print(f"    Created:  {r['created_at']}")
                if r["lead_state"]:
                    print(f"    Lead:     #{r['lead_id']} ({r['lead_state']})")
                print()
            return 0

    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
