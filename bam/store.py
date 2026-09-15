"""SQLite store: schema, migrations, state machine, audit log.

Design (plan v3.1 §15, §11):
- WAL + foreign keys ON + numbered migrations + transactions + basic indexes.
- Transition classes: AUTOMATIC transitions are the only ones generic code may
  perform; HUMAN transitions raise TransitionError unless recorded through
  record_approval().
- Every runtime write goes through the audit log.
"""

from __future__ import annotations

import math
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator

from bam.config import Config, load_config
from bam.intent import is_expired

_SCHEMA_VERSION = 6

_SCHEMA = [
    # migration 1 (schema_meta is created by _migrate(); do not re-create here)
    """
    CREATE TABLE companies (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        domain TEXT UNIQUE,
        industry TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE leads (
        id INTEGER PRIMARY KEY,
        company_id INTEGER NOT NULL REFERENCES companies(id),
        source_url TEXT,
        state TEXT NOT NULL DEFAULT 'discovered'
            CHECK (state IN (
                'discovered','researched','qualified','approval_required','approved',
                'contacted','replied','negotiating','won','onboarding','delivery',
                'delivered','follow_up','repeat','lost','disqualified',
                'do_not_contact','blocked'
            )),
        score REAL,
        confidence TEXT CHECK (confidence IN ('low','medium','high') OR confidence IS NULL),
        recommended_service TEXT,
        reason TEXT,
        run_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (company_id)
    );

    CREATE TABLE evidence (
        id INTEGER PRIMARY KEY,
        lead_id INTEGER NOT NULL REFERENCES leads(id),
        kind TEXT NOT NULL,
        url TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        excerpt TEXT,
        status TEXT NOT NULL CHECK (status IN ('OBSERVED','INFERRED','UNKNOWN')),
        confidence REAL
    );
    CREATE INDEX idx_evidence_lead ON evidence(lead_id);

    CREATE TABLE claims (
        id INTEGER PRIMARY KEY,
        lead_id INTEGER NOT NULL REFERENCES leads(id),
        kind TEXT NOT NULL,             -- detected_service | detected_technology | observable_problem
        value TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('OBSERVED','INFERRED','UNKNOWN')),
        evidence_id INTEGER REFERENCES evidence(id),
        reasoning TEXT
    );
    CREATE INDEX idx_claims_lead ON claims(lead_id);

    CREATE TABLE approvals (
        id INTEGER PRIMARY KEY,
        subject_type TEXT NOT NULL CHECK (subject_type IN ('lead','job','other')),
        subject_id INTEGER,
        kind TEXT NOT NULL CHECK (kind IN ('commercial','external_action','financial','public_comms','exception')),
        state_before TEXT,
        state_after TEXT,
        decided_by TEXT NOT NULL,
        reason TEXT,
        run_id TEXT,
        decided_at TEXT NOT NULL
    );

    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY,
        client_id INTEGER REFERENCES clients(id),
        service_id TEXT NOT NULL,
        title TEXT,
        input_path TEXT,
        sandbox_dir TEXT,
        state TEXT NOT NULL DEFAULT 'intake'
            CHECK (state IN ('intake','classified','assigned','delivery','delivered','failed')),
        quoted_amount REAL,
        agreed_amount REAL,
        currency TEXT DEFAULT 'EUR',
        payment_status TEXT CHECK (payment_status IN ('unpaid','invoiced','paid','refunded')
                                   OR payment_status IS NULL),
        paid_amount REAL,
        payment_date TEXT,
        delivery_date TEXT,
        run_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE clients (
        id INTEGER PRIMARY KEY,
        lead_id INTEGER REFERENCES leads(id),
        name TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE run_manifests (
        run_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        manifest_path TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        actor TEXT NOT NULL CHECK (actor IN ('human','agent','system')),
        action TEXT NOT NULL,
        entity_type TEXT,
        entity_id INTEGER,
        payload_json TEXT
    );

    CREATE INDEX idx_audit_entity ON audit_log(entity_type, entity_id);
    CREATE INDEX idx_leads_state ON leads(state);
    """,
    # migration 2: metrics events for bam digest
    """
    CREATE TABLE metrics (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        lead_id INTEGER,
        job_id INTEGER,
        name TEXT NOT NULL,
        value REAL NOT NULL,
        unit TEXT
    );
    CREATE INDEX idx_metrics_name ON metrics(name);
    """,
    # migration 3: contacts, interactions, quotes, follow_ups tables
    """
    CREATE TABLE contacts (
        id INTEGER PRIMARY KEY,
        lead_id INTEGER NOT NULL REFERENCES leads(id),
        name TEXT,
        role TEXT,
        email TEXT,
        phone TEXT,
        channel TEXT,
        source TEXT NOT NULL,
        evidence TEXT,
        confidence TEXT CHECK (confidence IN ('observed','inferred','unknown') OR confidence IS NULL),
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_contacts_lead ON contacts(lead_id);

    CREATE TABLE interactions (
        id INTEGER PRIMARY KEY,
        lead_id INTEGER NOT NULL REFERENCES leads(id),
        contact_id INTEGER REFERENCES contacts(id),
        channel TEXT NOT NULL,
        direction TEXT NOT NULL CHECK (direction IN ('outbound','inbound')),
        kind TEXT NOT NULL,
        subject TEXT,
        body TEXT,
        message_hash TEXT,
        timestamp TEXT NOT NULL,
        outcome TEXT,
        next_action TEXT,
        run_id TEXT
    );
    CREATE INDEX idx_interactions_lead ON interactions(lead_id);

    CREATE TABLE quotes (
        id INTEGER PRIMARY KEY,
        lead_id INTEGER NOT NULL REFERENCES leads(id),
        job_id INTEGER REFERENCES jobs(id),
        service_id TEXT NOT NULL,
        scope TEXT,
        estimated_files INTEGER,
        estimated_pages INTEGER,
        estimated_rows INTEGER,
        complexity TEXT CHECK (complexity IN ('simple','moderate','complex') OR complexity IS NULL),
        suggested_price REAL,
        suggested_currency TEXT DEFAULT 'EUR',
        delivery_estimate TEXT,
        assumptions TEXT,
        exclusions TEXT,
        state TEXT NOT NULL DEFAULT 'draft'
            CHECK (state IN ('draft','sent','approved','accepted','rejected','expired')),
        approved_by TEXT,
        sent_at TEXT,
        responded_at TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_quotes_lead ON quotes(lead_id);

    CREATE TABLE follow_ups (
        id INTEGER PRIMARY KEY,
        lead_id INTEGER NOT NULL REFERENCES leads(id),
        kind TEXT NOT NULL CHECK (kind IN ('first_contact','follow_up','reply_pending','quote_pending','payment_overdue','repeat_opportunity')),
        due_date TEXT,
        reason TEXT,
        recommended_action TEXT,
        completed INTEGER NOT NULL DEFAULT 0,
        completed_at TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_follow_ups_lead ON follow_ups(lead_id);
    CREATE INDEX idx_follow_ups_due ON follow_ups(due_date);
    """,
    # migration 5: commercial feedback loop (sales machine §27/§29-§31).
    # Experiment records: which sources/queries actually produce qualified,
    # contactable, positive, won leads — so bad sources get dropped.
    """
    CREATE TABLE IF NOT EXISTS campaign_runs (
        id INTEGER PRIMARY KEY,
        started_at TEXT NOT NULL,
        services TEXT NOT NULL,
        sources TEXT NOT NULL,
        markets TEXT,
        queries TEXT,
        limits_json TEXT,
        results_json TEXT,
        lead_ids TEXT
    );
    CREATE TABLE IF NOT EXISTS query_stats (
        id INTEGER PRIMARY KEY,
        campaign_id INTEGER REFERENCES campaign_runs(id),
        query TEXT NOT NULL,
        source TEXT NOT NULL,
        service TEXT,
        candidates INTEGER NOT NULL DEFAULT 0,
        researched INTEGER NOT NULL DEFAULT 0,
        qualified INTEGER NOT NULL DEFAULT 0,
        contactable INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_query_stats_query ON query_stats(query);
    """,
    # migration 6: commercial intent opportunities (intent engine §9-§11)
    """
    CREATE TABLE IF NOT EXISTS opportunities (
        id INTEGER PRIMARY KEY,
        company_id INTEGER REFERENCES companies(id),
        lead_id INTEGER REFERENCES leads(id),
        company_label TEXT,
        source_url TEXT NOT NULL,
        source_type TEXT NOT NULL,
        published_at TEXT,
        title TEXT NOT NULL,
        snippet TEXT,
        intent_tier TEXT NOT NULL CHECK (intent_tier IN
            ('explicit','strong','medium','weak')),
        intent_score INTEGER NOT NULL,
        service_fit TEXT,
        freshness TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'discovered' CHECK (state IN
            ('discovered','researched','qualified','approval_required',
             'approved','contacted','replied','quoted','won','lost','expired',
             'dismissed')),
        dedup_key TEXT NOT NULL,
        evidence_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE UNIQUE INDEX idx_opportunities_dedup ON opportunities(dedup_key);
    CREATE INDEX idx_opportunities_state ON opportunities(state);
    """,
    # migration 7: query catalog for learning and selection (§27-§30).
    # Tracks per-query cumulative performance and state across campaigns.
    """
    CREATE TABLE IF NOT EXISTS query_catalog (
        id INTEGER PRIMARY KEY,
        query TEXT NOT NULL,
        service TEXT,
        source TEXT NOT NULL DEFAULT 'unknown',
        state TEXT NOT NULL DEFAULT 'new'
            CHECK (state IN ('new','active','promising','weak','retired')),
        runs INTEGER NOT NULL DEFAULT 0,
        total_candidates INTEGER NOT NULL DEFAULT 0,
        total_researched INTEGER NOT NULL DEFAULT 0,
        total_qualified INTEGER NOT NULL DEFAULT 0,
        total_contactable INTEGER NOT NULL DEFAULT 0,
        total_contacted INTEGER NOT NULL DEFAULT 0,
        total_replies INTEGER NOT NULL DEFAULT 0,
        total_positive INTEGER NOT NULL DEFAULT 0,
        total_quotes INTEGER NOT NULL DEFAULT 0,
        total_won INTEGER NOT NULL DEFAULT 0,
        last_run_at TEXT,
        created_at TEXT NOT NULL
    );
    CREATE UNIQUE INDEX idx_query_catalog_dedup ON query_catalog(query, service, source);
    CREATE INDEX idx_query_catalog_state ON query_catalog(state);
    """,
]

# ---------------------------------------------------------------------------
# State machine (plan v3.1 §11)
# ---------------------------------------------------------------------------


class LeadState(StrEnum):
    DISCOVERED = "discovered"
    RESEARCHED = "researched"
    QUALIFIED = "qualified"
    APPROVAL_REQUIRED = "approval_required"
    APPROVED = "approved"
    CONTACTED = "contacted"
    REPLIED = "replied"
    NEGOTIATING = "negotiating"
    WON = "won"
    ONBOARDING = "onboarding"
    DELIVERY = "delivery"
    DELIVERED = "delivered"
    FOLLOW_UP = "follow_up"
    REPEAT = "repeat"
    LOST = "lost"
    DISQUALIFIED = "disqualified"
    DO_NOT_CONTACT = "do_not_contact"
    BLOCKED = "blocked"


class JobState(StrEnum):
    INTAKE = "intake"
    CLASSIFIED = "classified"
    ASSIGNED = "assigned"
    DELIVERY = "delivery"
    DELIVERED = "delivered"
    FAILED = "failed"


class TransitionClass(StrEnum):
    AUTOMATIC = "automatic"
    HUMAN = "human"


LEAD_TRANSITIONS: dict[tuple[str, str], TransitionClass] = {
    ("discovered", "researched"): TransitionClass.AUTOMATIC,
    ("discovered", "disqualified"): TransitionClass.AUTOMATIC,
    ("researched", "qualified"): TransitionClass.AUTOMATIC,
    ("researched", "disqualified"): TransitionClass.AUTOMATIC,
    ("qualified", "approval_required"): TransitionClass.AUTOMATIC,
    ("approval_required", "approved"): TransitionClass.HUMAN,
    ("approved", "contacted"): TransitionClass.HUMAN,
    ("contacted", "replied"): TransitionClass.AUTOMATIC,      # recorded outcome; classification may be automatic
    ("replied", "negotiating"): TransitionClass.AUTOMATIC,
    ("negotiating", "won"): TransitionClass.HUMAN,
    ("won", "onboarding"): TransitionClass.AUTOMATIC,
    ("onboarding", "delivery"): TransitionClass.AUTOMATIC,
    ("delivery", "delivered"): TransitionClass.AUTOMATIC,
    ("delivered", "follow_up"): TransitionClass.AUTOMATIC,
    ("follow_up", "repeat"): TransitionClass.AUTOMATIC,
    ("contacted", "lost"): TransitionClass.AUTOMATIC,
    ("negotiating", "lost"): TransitionClass.AUTOMATIC,
    ("qualified", "disqualified"): TransitionClass.AUTOMATIC,
    ("approval_required", "disqualified"): TransitionClass.AUTOMATIC,
    ("discovered", "blocked"): TransitionClass.AUTOMATIC,
    ("researched", "do_not_contact"): TransitionClass.AUTOMATIC,
    ("researched", "blocked"): TransitionClass.AUTOMATIC,
    ("qualified", "do_not_contact"): TransitionClass.AUTOMATIC,
}


class TransitionError(RuntimeError):
    """Illegal state transition (wrong class, wrong direction, or missing approval)."""


def _row_to(cls: type, row: sqlite3.Row):
    """Map a DB row onto a dataclass, ignoring extra SELECT columns."""
    names = {f.name for f in fields(cls)}
    return cls(**{k: row[k] for k in row.keys() if k in names})


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_state(state: str) -> str:
    """Raise unless *state* is a known lead state (schema integrity)."""
    valid = {s.value for s in LeadState}
    if state not in valid:
        raise ValueError(f"unknown lead state {state!r}; valid: {sorted(valid)}")
    return state


# Automatic follow-up policy per key transition: (due_days, kind, action).
# Created in the SAME transaction as the transition so a crash can never
# leave a moved lead without its follow-up (or vice versa).
_FOLLOW_UP_POLICY: dict[tuple[str, str], tuple[int, str, str]] = {
    ("approved", "contacted"): (3, "first_contact",
                                "check reply; send one polite reminder if silent"),
    ("contacted", "replied"): (7, "reply_pending",
                               "respond to prospect reply"),
    ("delivered", "follow_up"): (30, "repeat_opportunity",
                                 "offer repeat business or referral"),
}
# Quote approved -> chase the quote response.
_FOLLOW_UP_QUOTE = (5, "quote_pending", "chase quote response after 5 days")


@dataclass(frozen=True)
class LeadRow:
    id: int
    company_id: int
    state: str
    score: float | None
    confidence: str | None
    recommended_service: str | None
    reason: str | None
    run_id: str | None
    company_name: str = ""
    domain: str | None = None


@dataclass(frozen=True)
class JobRow:
    id: int
    client_id: int | None
    service_id: str
    title: str | None
    input_path: str | None
    sandbox_dir: str | None
    state: str
    quoted_amount: float | None
    agreed_amount: float | None
    currency: str
    payment_status: str | None
    paid_amount: float | None
    payment_date: str | None
    delivery_date: str | None
    run_id: str | None


@dataclass(frozen=True)
class ContactRow:
    id: int
    lead_id: int
    name: str | None
    role: str | None
    email: str | None
    phone: str | None
    channel: str | None
    source: str
    evidence: str | None
    confidence: str | None


@dataclass(frozen=True)
class InteractionRow:
    id: int
    lead_id: int
    contact_id: int | None
    channel: str
    direction: str
    kind: str
    subject: str | None
    body: str | None
    message_hash: str | None
    timestamp: str
    outcome: str | None
    next_action: str | None
    run_id: str | None


@dataclass(frozen=True)
class QuoteRow:
    id: int
    lead_id: int
    job_id: int | None
    service_id: str
    scope: str | None
    estimated_files: int | None
    estimated_pages: int | None
    estimated_rows: int | None
    complexity: str | None
    suggested_price: float | None
    suggested_currency: str
    delivery_estimate: str | None
    assumptions: str | None
    exclusions: str | None
    state: str
    approved_by: str | None
    sent_at: str | None
    responded_at: str | None


@dataclass(frozen=True)
class FollowUpRow:
    id: int
    lead_id: int
    kind: str
    due_date: str | None
    reason: str | None
    recommended_action: str | None
    completed: bool
    completed_at: str | None


class Store:
    """Single-writer SQLite store. All writes are audited."""

    def __init__(self, config: Config | None = None, db_path: Path | None = None) -> None:
        self.config = config or load_config()
        self.db_path = Path(db_path) if db_path else self.config.paths.database
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._local = threading.local()
        self._migrate()

    # -- connection handling -------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
        self._local.conn = None

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._conn()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _migrate(self) -> None:
        with self.transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)")
            row = conn.execute("SELECT MAX(version) AS v FROM schema_meta").fetchone()
            current = row["v"] or 0
            for idx, script in enumerate(_SCHEMA, start=1):
                if idx > current:
                    conn.executescript(script)
                    conn.execute("INSERT INTO schema_meta(version) VALUES (?)", (idx,))
            # WAL + FK pragmas are per-connection; schema_version avoids re-running.
            conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")

    # -- audit ----------------------------------------------------------------

    def audit(
        self,
        conn: sqlite3.Connection,
        *,
        actor: str,
        action: str,
        entity_type: str | None = None,
        entity_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        import json

        conn.execute(
            "INSERT INTO audit_log (ts, actor, action, entity_type, entity_id, payload_json)"
            " VALUES (?,?,?,?,?,?)",
            (
                _utcnow(),
                actor,
                action,
                entity_type,
                entity_id,
                json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload else None,
            ),
        )

    def audit_standalone(self, *, actor: str, action: str, **kwargs: Any) -> None:
        with self.transaction() as conn:
            self.audit(conn, actor=actor, action=action, **kwargs)

    # -- companies / leads -----------------------------------------------------

    def upsert_company(self, name: str, domain: str | None = None,
                       industry: str | None = None) -> int:
        with self.transaction() as conn:
            # NOTE: domain IS ? matches both NULL and the value; guard the NULL
            # case explicitly so a domain-less company never collides with a
            # domain company (or with every other domain-less company).
            if domain:
                row = conn.execute(
                    "SELECT id FROM companies WHERE domain = ?", (domain,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id FROM companies WHERE domain IS NULL AND name = ?", (name,)
                ).fetchone()
            if row:
                return int(row["id"])
            cur = conn.execute(
                "INSERT INTO companies (name, domain, industry, created_at) VALUES (?,?,?,?)",
                (name, domain, industry, _utcnow()),
            )
            self.audit(conn, actor="system", action="company.upsert",
                       entity_type="company", entity_id=cur.lastrowid,
                       payload={"name": name, "domain": domain})
            return int(cur.lastrowid)

    def upsert_lead(
        self,
        company_id: int,
        source_url: str | None,
        run_id: str,
        state: str = LeadState.DISCOVERED.value,
    ) -> int:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT id, state FROM leads WHERE company_id = ?", (company_id,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE leads SET source_url = COALESCE(?, source_url),"
                    " run_id = ?, updated_at = ? WHERE id = ?",
                    (source_url, run_id, _utcnow(), row["id"]),
                )
                return int(row["id"])
            cur = conn.execute(
                "INSERT INTO leads (company_id, source_url, state, run_id, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (company_id, source_url, state, run_id, _utcnow(), _utcnow()),
            )
            self.audit(conn, actor="system", action="lead.create",
                       entity_type="lead", entity_id=cur.lastrowid,
                       payload={"company_id": company_id, "state": state})
            return int(cur.lastrowid)

    def get_lead(self, lead_id: int) -> LeadRow | None:
        row = self._conn().execute(
            "SELECT l.*, c.name AS company_name, c.domain AS domain"
            " FROM leads l JOIN companies c ON c.id = l.company_id WHERE l.id = ?",
            (lead_id,),
        ).fetchone()
        return _row_to(LeadRow, row) if row else None

    def find_lead_by_domain(self, domain: str) -> LeadRow | None:
        row = self._conn().execute(
            "SELECT l.*, c.name AS company_name, c.domain AS domain"
            " FROM leads l JOIN companies c ON c.id = l.company_id WHERE c.domain = ?",
            (domain,),
        ).fetchone()
        return _row_to(LeadRow, row) if row else None

    def list_leads(self) -> list[LeadRow]:
        rows = self._conn().execute(
            "SELECT l.*, c.name AS company_name, c.domain AS domain"
            " FROM leads l JOIN companies c ON c.id = l.company_id ORDER BY l.id"
        ).fetchall()
        return [_row_to(LeadRow, r) for r in rows]

    # -- transitions -----------------------------------------------------------

    def reset_lead_for_research(self, lead_id: int, *, run_id: str) -> None:
        """Re-research support (P1 fix, hardening pass): reset a lead that is
        NOT in an active commercial relationship back to `discovered`, audited,
        so a new research run can transition it legally. Commercial states
        (approved..won..repeat) are never reset - use do_not_contact/lost etc."""
        resettable = {
            LeadState.DISQUALIFIED.value, LeadState.BLOCKED.value,
            LeadState.RESEARCHED.value, LeadState.QUALIFIED.value,
            LeadState.APPROVAL_REQUIRED.value,
        }
        with self.transaction() as conn:
            row = conn.execute("SELECT state FROM leads WHERE id = ?", (lead_id,)).fetchone()
            if not row:
                raise ValueError(f"lead {lead_id} not found")
            frm = row["state"]
            if frm in resettable and frm != LeadState.DISCOVERED.value:
                conn.execute(
                    "UPDATE leads SET state = ?, run_id = ?, updated_at = ? WHERE id = ?",
                    (LeadState.DISCOVERED.value, run_id, _utcnow(), lead_id),
                )
                self.audit(conn, actor="agent", action="lead.reset_for_research",
                           entity_type="lead", entity_id=lead_id,
                           payload={"from": frm, "to": LeadState.DISCOVERED.value,
                                    "run_id": run_id})

    def transition_lead(
        self,
        lead_id: int,
        to_state: str,
        *,
        actor: str = "agent",
        reason: str | None = None,
        run_id: str | None = None,
        score: float | None = None,
        confidence: str | None = None,
        recommended_service: str | None = None,
    ) -> None:
        """Perform a lead state transition, enforcing the transition class.

        HUMAN transitions must go through record_approval(); calling this with
        a HUMAN transition directly raises TransitionError.
        """
        with self.transaction() as conn:
            row = conn.execute("SELECT state FROM leads WHERE id = ?", (lead_id,)).fetchone()
            if not row:
                raise ValueError(f"lead {lead_id} not found")
            frm = row["state"]
            if frm == to_state:
                return
            validate_state(to_state)  # re-check against current schema, always
            klass = LEAD_TRANSITIONS.get((frm, to_state))
            if klass is None:
                raise TransitionError(f"illegal lead transition {frm} -> {to_state}")
            if klass is TransitionClass.HUMAN:
                raise TransitionError(
                    f"transition {frm} -> {to_state} is HUMAN-gated; use record_approval()"
                )
            sets = ["state = ?", "updated_at = ?"]
            params: list[Any] = [to_state, _utcnow()]
            if score is not None:
                sets.append("score = ?")
                params.append(score)
            if confidence is not None:
                sets.append("confidence = ?")
                params.append(confidence)
            if recommended_service is not None:
                sets.append("recommended_service = ?")
                params.append(recommended_service)
            params.append(lead_id)
            conn.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id = ?", params)
            self._create_follow_up_for_transition(conn, lead_id, frm, to_state)
            self.audit(conn, actor=actor, action="lead.transition",
                       entity_type="lead", entity_id=lead_id,
                       payload={"from": frm, "to": to_state, "class": klass.value,
                                "reason": reason, "run_id": run_id})

    def record_approval(
        self,
        *,
        subject_type: str,
        subject_id: int,
        kind: str,
        to_state: str,
        decided_by: str,
        reason: str | None = None,
        run_id: str | None = None,
    ) -> int:
        """Record a human approval and perform the HUMAN transition atomically."""
        if kind not in {"commercial", "external_action", "financial", "public_comms", "exception"}:
            raise ValueError(f"unknown approval kind: {kind}")
        if subject_type != "lead":
            # V1 scope: only lead transitions are HUMAN-gated through this path;
            # generic approvals without a verified transition would be
            # unauditable rubber stamps (plan v3.1 §10).
            raise ValueError("V1 approvals apply to leads only")
        with self.transaction() as conn:
            row = conn.execute("SELECT state FROM leads WHERE id = ?", (subject_id,)).fetchone()
            if not row:
                raise ValueError(f"lead {subject_id} not found")
            frm = row["state"]
            validate_state(to_state)  # schema integrity, independent of the table
            klass = LEAD_TRANSITIONS.get((frm, to_state))
            if klass is not TransitionClass.HUMAN:
                raise TransitionError(
                    f"{frm} -> {to_state} is not a HUMAN transition (class={klass})"
                )
            conn.execute(
                "UPDATE leads SET state = ?, updated_at = ? WHERE id = ?",
                (to_state, _utcnow(), subject_id),
            )
            cur = conn.execute(
                "INSERT INTO approvals (subject_type, subject_id, kind, state_before,"
                " state_after, decided_by, reason, run_id, decided_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (subject_type, subject_id, kind, frm, to_state, decided_by,
                 reason, run_id, _utcnow()),
            )
            self.audit(conn, actor="human", action="approval.record",
                       entity_type=subject_type, entity_id=subject_id,
                       payload={"kind": kind, "from": frm, "to": to_state,
                                "decided_by": decided_by, "reason": reason})
            self._create_follow_up_for_transition(conn, subject_id, frm, to_state)
            return int(cur.lastrowid)

    def _create_follow_up_for_transition(
        self, conn: sqlite3.Connection, lead_id: int, frm: str, to: str,
    ) -> None:
        """Create the policy follow-up for a key transition (same transaction)."""
        rule = _FOLLOW_UP_POLICY.get((frm, to))
        if rule is None:
            return
        days, kind, action = rule
        due = (datetime.now(timezone.utc)
               + timedelta(days=days)).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO follow_ups (lead_id, kind, due_date, reason,"
            " recommended_action, created_at) VALUES (?,?,?,?,?,?)",
            (lead_id, kind, due, f"auto: transition {frm} -> {to}", action,
             _utcnow()),
        )
        self.audit(conn, actor="system", action="follow_up.auto_create",
                   entity_type="lead", entity_id=lead_id,
                   payload={"kind": kind, "due_date": due,
                            "transition": f"{frm}->{to}"})

    def _create_follow_up_for_quote(
        self, conn: sqlite3.Connection, lead_id: int,
    ) -> None:
        """Create the quote-chase follow-up when a quote is approved."""
        days, kind, action = _FOLLOW_UP_QUOTE
        due = (datetime.now(timezone.utc)
               + timedelta(days=days)).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO follow_ups (lead_id, kind, due_date, reason,"
            " recommended_action, created_at) VALUES (?,?,?,?,?,?)",
            (lead_id, kind, due, "auto: quote approved", action, _utcnow()),
        )
        self.audit(conn, actor="system", action="follow_up.auto_create",
                   entity_type="quote", entity_id=lead_id,
                   payload={"kind": kind, "due_date": due})

    # -- evidence / claims ------------------------------------------------------

    def add_evidence(self, lead_id: int, *, kind: str, url: str, sha256: str,
                     observed_at: str, excerpt: str | None, status: str) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO evidence (lead_id, kind, url, sha256, observed_at, excerpt, status,"
                " confidence) VALUES (?,?,?,?,?,?,?,?)",
                (lead_id, kind, url, sha256, observed_at, excerpt, status, None),
            )
            self.audit(conn, actor="agent", action="evidence.add",
                       entity_type="lead", entity_id=lead_id,
                       payload={"sha256": sha256, "status": status, "url": url})
            return int(cur.lastrowid)

    def add_claim(self, lead_id: int, *, kind: str, value: str, status: str,
                  evidence_id: int | None, reasoning: str | None = None) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO claims (lead_id, kind, value, status, evidence_id, reasoning)"
                " VALUES (?,?,?,?,?,?)",
                (lead_id, kind, value, status, evidence_id, reasoning),
            )
            self.audit(conn, actor="agent", action="claim.add",
                       entity_type="lead", entity_id=lead_id,
                       payload={"kind": kind, "value": value, "status": status})
            return int(cur.lastrowid)

    def evidence_for_lead(self, lead_id: int) -> list[dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT * FROM evidence WHERE lead_id = ? ORDER BY id", (lead_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def claims_for_lead(self, lead_id: int) -> list[dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT * FROM claims WHERE lead_id = ? ORDER BY id", (lead_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- contacts ---------------------------------------------------------------

    def add_contact(
        self,
        lead_id: int,
        *,
        name: str | None = None,
        role: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        channel: str | None = None,
        source: str,
        evidence: str | None = None,
        confidence: str | None = None,
    ) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO contacts (lead_id, name, role, email, phone, channel,"
                " source, evidence, confidence, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (lead_id, name, role, email, phone, channel, source, evidence,
                 confidence, _utcnow()),
            )
            self.audit(conn, actor="agent", action="contact.add",
                       entity_type="lead", entity_id=lead_id,
                       payload={"name": name, "email": email, "source": source})
            return int(cur.lastrowid)

    def list_contacts(self, lead_id: int) -> list[ContactRow]:
        rows = self._conn().execute(
            "SELECT * FROM contacts WHERE lead_id = ? ORDER BY id", (lead_id,)
        ).fetchall()
        return [_row_to(ContactRow, r) for r in rows]

    def get_contact(self, contact_id: int) -> ContactRow | None:
        row = self._conn().execute(
            "SELECT * FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()
        return _row_to(ContactRow, row) if row else None

    def lead_contacts(self, lead_id: int) -> list[dict[str, Any]]:
        """Return contacts for a lead as list of dicts (for hunt output)."""
        rows = self._conn().execute(
            "SELECT name, role, email, phone, source, confidence"
            " FROM contacts WHERE lead_id = ? ORDER BY id", (lead_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def followups_due(self) -> list[Any]:
        """Return follow-ups that are due (not completed, due_date <= today)."""
        from datetime import date
        today = date.today().isoformat()
        rows = self._conn().execute(
            "SELECT * FROM follow_ups"
            " WHERE completed = 0 AND (due_date IS NULL OR due_date <= ?)"
            " ORDER BY due_date ASC LIMIT 10",
            (today,),
        ).fetchall()
        return [_row_to(FollowUpRow, r) for r in rows]

    def quotes_pending(self) -> list[dict[str, Any]]:
        """Return quotes in draft or sent state."""
        rows = self._conn().execute(
            "SELECT * FROM quotes WHERE state IN ('draft', 'sent')"
            " ORDER BY id DESC LIMIT 10"
        ).fetchall()
        return [dict(r) for r in rows]

    def add_followup(
        self,
        lead_id: int,
        *,
        kind: str,
        due_date: str | None = None,
        reason: str | None = None,
        recommended_action: str | None = None,
    ) -> int:
        """Insert a follow-up (used by tests and manual creation)."""
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO follow_ups (lead_id, kind, due_date, reason,"
                " recommended_action, created_at) VALUES (?,?,?,?,?,?)",
                (lead_id, kind, due_date, reason, recommended_action, _utcnow()),
            )
            return int(cur.lastrowid)

    def add_quote(
        self,
        lead_id: int,
        *,
        service_id: str,
        state: str = "draft",
        scope: str | None = None,
    ) -> int:
        """Insert a quote (used by tests and manual creation)."""
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO quotes (lead_id, service_id, scope, state, created_at)"
                " VALUES (?,?,?,?,?)",
                (lead_id, service_id, scope, state, _utcnow()),
            )
            return int(cur.lastrowid)

    # -- interactions -----------------------------------------------------------

    def add_interaction(
        self,
        lead_id: int,
        *,
        contact_id: int | None = None,
        channel: str,
        direction: str,
        kind: str,
        subject: str | None = None,
        body: str | None = None,
        message_hash: str | None = None,
        outcome: str | None = None,
        next_action: str | None = None,
        run_id: str | None = None,
    ) -> int:
        import hashlib
        if direction not in ("outbound", "inbound"):
            raise ValueError(f"direction must be 'outbound' or 'inbound', got {direction!r}")
        if body and not message_hash:
            message_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO interactions (lead_id, contact_id, channel, direction, kind,"
                " subject, body, message_hash, timestamp, outcome, next_action, run_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (lead_id, contact_id, channel, direction, kind, subject, body,
                 message_hash, _utcnow(), outcome, next_action, run_id),
            )
            self.audit(conn, actor="human" if direction == "outbound" else "agent",
                       action="interaction.add",
                       entity_type="lead", entity_id=lead_id,
                       payload={"channel": channel, "direction": direction,
                                "kind": kind, "outcome": outcome})
            return int(cur.lastrowid)

    def list_interactions(self, lead_id: int) -> list[InteractionRow]:
        rows = self._conn().execute(
            "SELECT * FROM interactions WHERE lead_id = ? ORDER BY id", (lead_id,)
        ).fetchall()
        return [_row_to(InteractionRow, r) for r in rows]

    # -- quotes -----------------------------------------------------------------

    def create_quote(
        self,
        lead_id: int,
        *,
        service_id: str,
        scope: str | None = None,
        estimated_files: int | None = None,
        estimated_pages: int | None = None,
        estimated_rows: int | None = None,
        complexity: str | None = None,
        suggested_price: float | None = None,
        suggested_currency: str = "EUR",
        delivery_estimate: str | None = None,
        assumptions: str | None = None,
        exclusions: str | None = None,
    ) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO quotes (lead_id, service_id, scope, estimated_files,"
                " estimated_pages, estimated_rows, complexity, suggested_price,"
                " suggested_currency, delivery_estimate, assumptions, exclusions,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (lead_id, service_id, scope, estimated_files, estimated_pages,
                 estimated_rows, complexity, suggested_price, suggested_currency,
                 delivery_estimate, assumptions, exclusions, _utcnow()),
            )
            self.audit(conn, actor="agent", action="quote.create",
                       entity_type="lead", entity_id=lead_id,
                       payload={"service_id": service_id,
                                "suggested_price": suggested_price})
            return int(cur.lastrowid)

    def get_quote(self, quote_id: int) -> QuoteRow | None:
        row = self._conn().execute(
            "SELECT * FROM quotes WHERE id = ?", (quote_id,)
        ).fetchone()
        return _row_to(QuoteRow, row) if row else None

    def update_quote(self, quote_id: int, **fields: Any) -> None:
        allowed = {
            "state", "approved_by", "sent_at", "responded_at", "job_id",
            "suggested_price", "scope", "assumptions", "exclusions",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown quote fields: {sorted(unknown)}")
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
            if not row:
                raise ValueError(f"quote {quote_id} not found")
            sets = ", ".join(f"{k} = ?" for k in fields)
            params = list(fields.values()) + [quote_id]
            conn.execute(f"UPDATE quotes SET {sets} WHERE id = ?", params)
            self.audit(conn, actor="system", action="quote.update",
                       entity_type="quote", entity_id=quote_id, payload=fields)
            if fields.get("state") == "approved" and row["state"] != "approved":
                self._create_follow_up_for_quote(conn, int(row["lead_id"]))

    def list_quotes(self, lead_id: int | None = None) -> list[QuoteRow]:
        if lead_id is not None:
            rows = self._conn().execute(
                "SELECT * FROM quotes WHERE lead_id = ? ORDER BY id", (lead_id,)
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT * FROM quotes ORDER BY id"
            ).fetchall()
        return [_row_to(QuoteRow, r) for r in rows]

    # -- follow_ups -------------------------------------------------------------

    def create_follow_up(
        self,
        lead_id: int,
        *,
        kind: str,
        due_date: str | None = None,
        reason: str | None = None,
        recommended_action: str | None = None,
    ) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO follow_ups (lead_id, kind, due_date, reason,"
                " recommended_action, created_at) VALUES (?,?,?,?,?,?)",
                (lead_id, kind, due_date, reason, recommended_action, _utcnow()),
            )
            self.audit(conn, actor="system", action="follow_up.create",
                       entity_type="lead", entity_id=lead_id,
                       payload={"kind": kind, "due_date": due_date})
            return int(cur.lastrowid)

    def complete_follow_up(self, follow_up_id: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE follow_ups SET completed = 1, completed_at = ? WHERE id = ?",
                (_utcnow(), follow_up_id),
            )

    def pending_follow_ups(self) -> list[FollowUpRow]:
        rows = self._conn().execute(
            "SELECT * FROM follow_ups WHERE completed = 0 ORDER BY due_date ASC"
        ).fetchall()
        return [_row_to(FollowUpRow, r) for r in rows]

    def overdue_follow_ups(self) -> list[FollowUpRow]:
        rows = self._conn().execute(
            "SELECT * FROM follow_ups WHERE completed = 0 AND due_date < ? ORDER BY due_date",
            (_utcnow()[:10],),
        ).fetchall()
        return [_row_to(FollowUpRow, r) for r in rows]

    # -- sales copilot data (daily queue + feedback loop) -------------------------

    def queue_candidates(self) -> list[dict[str, Any]]:
        """All leads plausibly worth acting on, enriched for the copilot queue:
        state, score, recommended service, evidence, commercial signals,
        contactability and the best observed contact. Pure reads."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT l.id, l.state, l.score, l.confidence, l.recommended_service,"
            " c.name AS company_name, c.domain"
            " FROM leads l JOIN companies c ON c.id = l.company_id"
            " WHERE l.state NOT IN ('lost','disqualified','do_not_contact','blocked',"
            "                      'repeat','onboarding','delivery','delivered')"
            " ORDER BY l.score DESC").fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            profile = self._profile_signals(d["domain"])
            d["evidence"] = self.evidence_for_lead(d["id"])
            d["commercial"] = (profile or {}).get("commercial", {})
            contacts = self.list_contacts(d["id"])
            d["contactability"] = (profile or {}).get("commercial", {}).get(
                "contactability", {})
            best = None
            for ct in contacts:
                if ct.email:
                    best = ct.email
                    break
                if best is None and (ct.channel or ct.phone):
                    best = ct.channel or ct.phone
            if best is None:
                # fall back to observed emails in the profile signals
                emails = (profile or {}).get("emails") or []
                best = emails[0] if emails else None
            d["contact"] = best
            out.append(d)
        return out

    def _profile_signals(self, domain: str | None) -> dict[str, Any] | None:
        """Load signals from the lead profile.json if it exists (read-only)."""
        if not domain:
            return None
        from pathlib import Path

        p = self.config.paths.evidence_dir / domain / "profile.json"
        if not p.exists():
            return None
        try:
            import json
            return json.loads(p.read_text(encoding="utf-8")).get("signals", {})
        except (OSError, ValueError):
            return None

    def start_campaign(
        self,
        *,
        services: list[str],
        sources: list[str],
        markets: list[str] | None = None,
        queries: list[str] | None = None,
        limits: dict[str, Any] | None = None,
    ) -> int:
        """Record a controlled experiment (§31). Returns the campaign id."""
        import json as _json
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO campaign_runs (started_at, services, sources, markets,"
                " queries, limits_json) VALUES (?,?,?,?,?,?)",
                (_utcnow(), _json.dumps(services), _json.dumps(sources),
                 _json.dumps(markets or []), _json.dumps(queries or []),
                 _json.dumps(limits or {}, sort_keys=True)))
            return int(cur.lastrowid)

    def finish_campaign(
        self,
        campaign_id: int,
        *,
        results: dict[str, Any],
        lead_ids: list[int],
        query_stats: list[dict[str, Any]] | None = None,
    ) -> None:
        """Close the experiment with measured results (§30: learn from outcomes)."""
        import json as _json
        with self.transaction() as conn:
            conn.execute(
                "UPDATE campaign_runs SET results_json = ?, lead_ids = ? WHERE id = ?",
                (_json.dumps(results, ensure_ascii=False, sort_keys=True),
                 _json.dumps(lead_ids), campaign_id))
            for qs in (query_stats or []):
                conn.execute(
                    "INSERT INTO query_stats (campaign_id, query, source, service,"
                    " candidates, researched, qualified, contactable, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (campaign_id, str(qs.get("query", ""))[:200],
                     str(qs.get("source", ""))[:60], qs.get("service"),
                     int(qs.get("candidates", 0)), int(qs.get("researched", 0)),
                     int(qs.get("qualified", 0)), int(qs.get("contactable", 0)),
                     _utcnow()))
            self.audit(conn, actor="agent", action="campaign.run",
                       entity_type="campaign", entity_id=campaign_id,
                       payload={"results": results, "leads": lead_ids})
        # Update query catalog for learning (§27-§30)
        for qs in (query_stats or []):
            self.update_query_catalog(
                query=str(qs.get("query", ""))[:200],
                service=qs.get("service"),
                source=str(qs.get("source", ""))[:60],
                candidates=int(qs.get("candidates", 0)),
                researched=int(qs.get("researched", 0)),
                qualified=int(qs.get("qualified", 0)),
                contactable=int(qs.get("contactable", 0)),
                contacted=int(qs.get("contacted", 0)),
                replies=int(qs.get("replies", 0)),
                positive=int(qs.get("positive", 0)),
                quotes=int(qs.get("quotes", 0)),
                won=int(qs.get("won", 0)),
            )

    def source_quality(self) -> list[dict[str, Any]]:
        """Per-source funnel: candidates → researched → qualified → contactable.
        Deterministic analytics over recorded campaigns (§29)."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT source, SUM(candidates) AS candidates, SUM(researched) AS researched,"
            " SUM(qualified) AS qualified, SUM(contactable) AS contactable"
            " FROM query_stats GROUP BY source ORDER BY qualified DESC").fetchall()
        return [dict(r) for r in rows]

    def query_quality(self, limit: int = 10) -> list[dict[str, Any]]:
        """Per-query funnel, best first (§30). Only meaningful with data."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT query, service, SUM(candidates) AS candidates,"
            " SUM(researched) AS researched, SUM(qualified) AS qualified"
            " FROM query_stats GROUP BY query ORDER BY qualified DESC, researched DESC"
            " LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    # -- query catalog: learning and selection (§27-§30) ------------------------

    def update_query_catalog(
        self,
        *,
        query: str,
        service: str | None = None,
        source: str = "unknown",
        candidates: int = 0,
        researched: int = 0,
        qualified: int = 0,
        contactable: int = 0,
        contacted: int = 0,
        replies: int = 0,
        positive: int = 0,
        quotes: int = 0,
        won: int = 0,
    ) -> None:
        """Update the query_catalog after a campaign run. Accumulates stats
        and automatically transitions states based on outcome thresholds.

        State rules (conservative, §4):
        - NEW: just created, insufficient data
        - ACTIVE: has been run at least once
        - PROMISING: produces qualified or strong/intent leads
        - WEAK: many runs with few qualified outcomes
        - RETIRED: consistently poor performance with sufficient sample
        """
        now = _utcnow()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM query_catalog WHERE query = ? AND service IS ? AND source = ?",
                (query, service, source)).fetchone()

            if row:
                r = dict(row)
                new_runs = r["runs"] + 1
                new_candidates = r["total_candidates"] + candidates
                new_researched = r["total_researched"] + researched
                new_qualified = r["total_qualified"] + qualified
                new_contactable = r["total_contactable"] + contactable
                new_contacted = r["total_contacted"] + contacted
                new_replies = r["total_replies"] + replies
                new_positive = r["total_positive"] + positive
                new_quotes = r["total_quotes"] + quotes
                new_won = r["total_won"] + won

                # State transitions based on cumulative performance
                state = r["state"]
                if state == "retired":
                    pass  # retired stays retired
                elif new_won > 0 or new_positive > 0:
                    state = "promising"
                elif new_qualified > 0:
                    state = "promising"
                elif new_runs >= 3 and new_qualified == 0 and new_candidates > 20:
                    state = "weak"
                elif new_runs >= 1:
                    state = "active"

                conn.execute(
                    "UPDATE query_catalog SET runs = ?, total_candidates = ?,"
                    " total_researched = ?, total_qualified = ?,"
                    " total_contactable = ?, total_contacted = ?,"
                    " total_replies = ?, total_positive = ?,"
                    " total_quotes = ?, total_won = ?,"
                    " state = ?, last_run_at = ?"
                    " WHERE id = ?",
                    (new_runs, new_candidates, new_researched, new_qualified,
                     new_contactable, new_contacted, new_replies, new_positive,
                     new_quotes, new_won, state, now, r["id"]))
            else:
                # Determine initial state
                state = "new"
                if qualified > 0 or positive > 0 or won > 0:
                    state = "promising"
                elif candidates > 0:
                    state = "active"

                conn.execute(
                    "INSERT INTO query_catalog"
                    " (query, service, source, state, runs,"
                    "  total_candidates, total_researched, total_qualified,"
                    "  total_contactable, total_contacted,"
                    "  total_replies, total_positive, total_quotes, total_won,"
                    "  last_run_at, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (query, service, source, state, 1,
                     candidates, researched, qualified,
                     contactable, contacted,
                     replies, positive, quotes, won,
                     now, now))

    def query_catalog_entries(self, *, state: str | None = None,
                              limit: int = 50) -> list[dict[str, Any]]:
        """List query catalog entries, ordered by quality then sample size."""
        conn = self._conn()
        q = "SELECT * FROM query_catalog"
        params: list[Any] = []
        if state:
            q += " WHERE state = ?"
            params.append(state)
        q += " ORDER BY total_qualified DESC, total_won DESC, total_candidates DESC"
        if limit:
            q += " LIMIT ?"
            params.append(int(limit))
        return [dict(r) for r in conn.execute(q, params).fetchall()]

    def best_queries_for_next_campaign(
        self,
        *,
        exploration_pct: int = 20,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Select queries for the next campaign using exploration + exploitation.

        Strategy (§5):
        - 80% exploitation: promising/active queries ranked by quality
        - 20% exploration: new/weak queries with insufficient data

        Quality ranking uses: qualified > won > contacted > candidates.
        Never retires queries with insufficient sample (runs < 3).
        """
        conn = self._conn()

        # Exploitation: promising + active queries, ranked by quality
        exploit = conn.execute(
            "SELECT * FROM query_catalog"
            " WHERE state IN ('promising', 'active')"
            " ORDER BY total_qualified DESC, total_won DESC, total_researched DESC"
            " LIMIT ?", (int(limit * (100 - exploration_pct) / 100),)).fetchall()

        # Exploration: new + weak queries (insufficient sample or low quality)
        explore = conn.execute(
            "SELECT * FROM query_catalog"
            " WHERE state IN ('new', 'weak')"
            " ORDER BY runs ASC, total_candidates DESC"
            " LIMIT ?", (int(limit * exploration_pct / 100),)).fetchall()

        result = [dict(r) for r in exploit] + [dict(r) for r in explore]
        return result[:limit]

    def retire_query(self, query_id: int) -> None:
        """Manually retire a query. Only allowed with sufficient sample."""
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT id, runs, total_qualified FROM query_catalog WHERE id = ?",
                (query_id,)).fetchone()
            if not row:
                raise ValueError(f"query {query_id} not found")
            if row["runs"] < 3:
                raise ValueError(
                    f"query {query_id} has only {row['runs']} runs —"
                    " need at least 3 before retirement")
            conn.execute(
                "UPDATE query_catalog SET state = 'retired' WHERE id = ?",
                (query_id,))
            self.audit(conn, actor="agent", action="query.retired",
                       entity_type="query_catalog", entity_id=query_id,
                       payload={"runs": row["runs"],
                                "qualified": row["total_qualified"]})

    # -- opportunities (intent engine §9-§11, §19) --------------------------------

    _OPPORTUNITY_STATES = (
        "discovered", "researched", "qualified", "approval_required",
        "approved", "contacted", "replied", "quoted", "won", "lost",
        "expired", "dismissed",
    )

    def add_opportunity(
        self,
        *,
        source_url: str,
        source_type: str,
        title: str,
        intent_tier: str,
        intent_score: int,
        service_fit: str | None,
        freshness: str,
        published_at: str | None,
        snippet: str | None = None,
        company_label: str | None = None,
        company_id: int | None = None,
        dedup_key: str | None = None,
        evidence: list[dict[str, Any]] | None = None,
    ) -> tuple[int, bool]:
        """Insert an opportunity, dedup by (company, normalized intent, host).
        Returns (id, created). On duplicate, the NEW source is merged into the
        stored evidence (§19: never destroy evidence) and (id, False) returned.
        Expired-by-date items are stored as 'expired' (§18)."""
        import hashlib
        import json as _json

        key = dedup_key or f"{company_label}|{source_url}"
        khash = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        now = _utcnow()
        state = "expired" if is_expired(published_at) else "discovered"
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT id, evidence_json FROM opportunities WHERE dedup_key = ?",
                (khash,)).fetchone()
            if existing:
                merged = _json.loads(existing["evidence_json"] or "[]")
                for e in (evidence or []):
                    if e not in merged:
                        merged.append(e)
                conn.execute(
                    "UPDATE opportunities SET evidence_json = ?, updated_at = ?"
                    " WHERE id = ?", (_json.dumps(merged), now, existing["id"]))
                self.audit(conn, actor="agent", action="opportunity.deduped",
                           entity_type="opportunity", entity_id=existing["id"],
                           payload={"source_url": source_url})
                return int(existing["id"]), False
            cur = conn.execute(
                "INSERT INTO opportunities (company_id, lead_id, company_label,"
                " source_url, source_type, published_at, title, snippet,"
                " intent_tier, intent_score, service_fit, freshness, state,"
                " dedup_key, evidence_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (company_id, None, company_label, source_url, source_type,
                 published_at, title, snippet, intent_tier, int(intent_score),
                 service_fit, freshness, state, khash,
                 _json.dumps(evidence or []), now, now))
            self.audit(conn, actor="agent", action="opportunity.add",
                       entity_type="opportunity", entity_id=int(cur.lastrowid),
                       payload={"tier": intent_tier, "service": service_fit,
                                "source_type": source_type, "state": state})
            return int(cur.lastrowid), True

    def get_opportunity(self, opp_id: int) -> dict[str, Any] | None:
        import json as _json
        r = self._conn().execute(
            "SELECT * FROM opportunities WHERE id = ?", (opp_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["evidence"] = _json.loads(d.pop("evidence_json") or "[]")
        return d

    def list_opportunities(
        self, *, state: str | None = None,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        """Opportunities, hottest first: intent tier (explicit>strong>medium),
        then freshness, then score. Expired hidden by default (§18)."""
        tier_rank = "CASE intent_tier WHEN 'explicit' THEN 0 WHEN 'strong' THEN 1"
        tier_rank += " WHEN 'medium' THEN 2 ELSE 3 END"
        fresh_rank = "CASE freshness WHEN 'very_high' THEN 0 WHEN 'high' THEN 1"
        fresh_rank += " WHEN 'medium' THEN 2 WHEN 'low' THEN 3 WHEN 'very_low'"
        fresh_rank += " THEN 4 ELSE 5 END"
        q = "SELECT * FROM opportunities"
        conds, params = [], []
        if state:
            conds.append("state = ?")
            params.append(state)
        if not include_expired and not state:
            conds.append("state != 'expired'")
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += f" ORDER BY {tier_rank}, {fresh_rank}, intent_score DESC, id"
        rows = self._conn().execute(q, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d.pop("evidence_json", None)
            out.append(d)
        return out

    def campaign_report(self) -> dict[str, Any]:
        """Aggregate commercial funnel from stored campaign data.

        Returns funnel counts, source_quality, query_quality, and
        recommended next actions. No invented metrics: every number
        comes from stored campaign_runs, query_stats, opportunities,
        leads, interactions, quotes, and follow-ups.
        """
        conn = self._conn()

        opps = self.list_opportunities(include_expired=True)
        funnel: dict[str, int] = {
            "candidates": 0,
            "valid_companies": 0,
            "opportunities": len(opps),
            "explicit_intent": 0,
            "strong": 0,
            "medium": 0,
            "qualified": 0,
            "contactable": 0,
            "contacted": 0,
            "replies": 0,
            "positive": 0,
            "quotes": 0,
            "won": 0,
        }

        for o in opps:
            if o["intent_tier"] == "explicit":
                funnel["explicit_intent"] += 1
            elif o["intent_tier"] == "strong":
                funnel["strong"] += 1
            elif o["intent_tier"] == "medium":
                funnel["medium"] += 1
            if o.get("company_id") is not None or o.get("company_label"):
                funnel["valid_companies"] += 1
            if o.get("intent_score", 0) >= 60 or o["intent_tier"] in ("explicit", "strong"):
                funnel["qualified"] += 1
            if o.get("company_id") is not None and o["intent_tier"] in ("explicit", "strong"):
                funnel["contactable"] += 1
            if o.get("state") == "contacted":
                funnel["contacted"] += 1
            if o.get("state") == "replied":
                funnel["replies"] += 1
            if o.get("state") == "won":
                funnel["won"] += 1
            if o.get("state") == "quoted":
                funnel["quotes"] += 1

        leads = self.list_leads()
        for lead in leads:
            if lead.state == "replied":
                funnel["replies"] += 1
            if lead.state == "contacted":
                funnel["contacted"] += 1
            if lead.state == "approved":
                funnel["contacted"] += 1
            if lead.state == "negotiating":
                funnel["positive"] += 1
            if lead.state == "won":
                funnel["won"] += 1

        quotes = self.list_quotes()
        funnel["quotes"] = len(quotes)
        for q in quotes:
            if q.state == "won":
                funnel["won"] += 1

        source_quality = self.source_quality()
        for row in source_quality:
            row["sample_size"] = row.get("candidates", 0)
            row["qualified_rate"] = (
                row["qualified"] / row["researched"] if row.get("researched", 0) > 0
                else None
            )
            row["replies"] = row.get("replies", 0)

        query_quality = self.query_quality(limit=10)
        for row in query_quality:
            row["sample_size"] = row.get("candidates", 0)
            row["qualified_rate"] = (
                row["qualified"] / row["researched"] if row.get("researched", 0) > 0
                else None
            )

        recommended: list[dict[str, Any]] = []
        for row in query_quality:
            if row.get("qualified", 0) > 0:
                recommended.append({
                    "query": row["query"],
                    "service": row.get("service"),
                    "source": row.get("source"),
                    "reason": f"produced {row['qualified']} qualified leads from {row['researched']} researched",
                })

        return {
            "funnel": funnel,
            "source_quality": source_quality,
            "query_quality": query_quality,
            "recommended": recommended,
        }

    def transition_opportunity(self, opp_id: int, to_state: str, *,
                               actor: str = "agent",
                               reason: str | None = None) -> None:
        """Opportunity lifecycle transitions. HUMAN states (approval_required,
        approved, contacted) require actor='human' and an approval record -
        same discipline as leads: the LLM and generic code can never advance
        a human-gated state."""
        import json as _json
        if to_state not in self._OPPORTUNITY_STATES:
            raise ValueError(f"unknown opportunity state: {to_state!r}")
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT state FROM opportunities WHERE id = ?", (opp_id,)).fetchone()
            if not row:
                raise ValueError(f"opportunity {opp_id} not found")
            frm = row["state"]
            if frm == to_state:
                return
            human_gated = to_state in ("approval_required", "approved", "contacted")
            if human_gated and actor != "human":
                raise ValueError(
                    f"opportunity transition {frm} -> {to_state} is HUMAN-gated")
            conn.execute(
                "UPDATE opportunities SET state = ?, updated_at = ? WHERE id = ?",
                (to_state, _utcnow(), opp_id))
            self.audit(conn, actor=actor, action="opportunity.transition",
                       entity_type="opportunity", entity_id=opp_id,
                       payload={"from": frm, "to": to_state, "reason": reason})

    def next_lead(self) -> dict[str, Any] | None:
        """Find the highest-priority lead needing action today.

        Priority order:
        1. approval_required (needs human approval to proceed)
        2. replied (needs response to prospect)
        3. negotiating (needs deal progression)
        4. contacted (awaiting reply, check if follow-up due)
        5. overdue follow-ups
        6. follow_up (needs re-engagement)
        7. qualified (needs outreach prep)
        """
        conn = self._conn()
        # Priority 1: approval_required
        for state in ("approval_required", "replied", "negotiating", "qualified"):
            row = conn.execute(
                "SELECT l.id, l.state, l.score, l.confidence, l.recommended_service,"
                " l.reason, l.company_id, c.name AS company_name, c.domain"
                " FROM leads l JOIN companies c ON c.id = l.company_id"
                " WHERE l.state = ? ORDER BY l.score DESC LIMIT 1",
                (state,),
            ).fetchone()
            if row:
                return dict(row)
        # Priority 5: overdue follow-ups
        row = conn.execute(
            "SELECT l.id, l.state, l.score, l.confidence, l.recommended_service,"
            " l.reason, l.company_id, c.name AS company_name, c.domain"
            " FROM follow_ups f JOIN leads l ON l.id = f.lead_id"
            " JOIN companies c ON c.id = l.company_id"
            " WHERE f.completed = 0 AND f.due_date < ?"
            " ORDER BY f.due_date ASC LIMIT 1",
            (_utcnow()[:10],),
        ).fetchone()
        if row:
            return dict(row)
        # Priority 6: follow_up state
        row = conn.execute(
            "SELECT l.id, l.state, l.score, l.confidence, l.recommended_service,"
            " l.reason, l.company_id, c.name AS company_name, c.domain"
            " FROM leads l JOIN companies c ON c.id = l.company_id"
            " WHERE l.state = 'follow_up' ORDER BY l.score DESC LIMIT 1"
        ).fetchone()
        if row:
            return dict(row)
        return None

    # -- jobs / clients ----------------------------------------------------------

    def create_client(self, name: str, lead_id: int | None = None) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO clients (lead_id, name, created_at) VALUES (?,?,?)",
                (lead_id, name, _utcnow()),
            )
            self.audit(conn, actor="system", action="client.create",
                       entity_type="client", entity_id=cur.lastrowid, payload={"name": name})
            return int(cur.lastrowid)

    def create_job(self, *, service_id: str, title: str | None, input_path: str | None,
                   client_id: int | None = None, quoted_amount: float | None = None,
                   agreed_amount: float | None = None, currency: str = "EUR",
                   run_id: str | None = None) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO jobs (client_id, service_id, title, input_path, state,"
                " quoted_amount, agreed_amount, currency, run_id, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (client_id, service_id, title, input_path, JobState.INTAKE.value,
                 quoted_amount, agreed_amount, currency, run_id, _utcnow(), _utcnow()),
            )
            self.audit(conn, actor="system", action="job.create",
                       entity_type="job", entity_id=cur.lastrowid,
                       payload={"service_id": service_id, "input_path": input_path})
            return int(cur.lastrowid)

    def update_job(self, job_id: int, **fields: Any) -> None:
        allowed = {
            "state", "sandbox_dir", "quoted_amount", "agreed_amount", "currency",
            "payment_status", "paid_amount", "payment_date", "delivery_date",
            "title", "input_path", "client_id", "run_id",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown job fields: {sorted(unknown)}")
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise ValueError(f"job {job_id} not found")
            if "state" in fields:
                valid = {s.value for s in JobState}
                if fields["state"] not in valid:
                    raise TransitionError(
                        f"illegal job state {fields['state']!r}; valid: {sorted(valid)}"
                    )
            # Payment-field validation (revenue integrity, plan v3.1 §12):
            # validate the MERGED post-update state so neither field order can
            # produce a paid job without an amount, a negative amount, or a
            # corrupting currency - which would poison bam digest metrics.
            merged = {**dict(row), **fields}
            if merged.get("payment_status") not in ("unpaid", "invoiced", "paid",
                                                    "refunded", None):
                raise ValueError(
                    f"illegal payment_status {merged['payment_status']!r}"
                )
            for money in ("quoted_amount", "agreed_amount", "paid_amount"):
                if merged.get(money) is not None:
                    amount = float(merged[money])  # raises on non-numeric strings
                    if not math.isfinite(amount):
                        raise ValueError(
                            f"{money} must be a finite number (got {merged[money]!r})")
                    if amount < 0:
                        raise ValueError(f"{money} cannot be negative")
            if merged.get("payment_status") == "paid" and \
                    merged.get("paid_amount") in (None, 0):
                raise ValueError(
                    "payment_status 'paid' requires a positive paid_amount"
                )
            if merged.get("currency") is not None and len(str(merged["currency"])) != 3:
                raise ValueError("currency must be a 3-letter code")
            sets = ", ".join(f"{k} = ?" for k in fields)
            params = list(fields.values()) + [_utcnow(), job_id]
            conn.execute(f"UPDATE jobs SET {sets}, updated_at = ? WHERE id = ?", params)
            self.audit(conn, actor="system", action="job.update",
                       entity_type="job", entity_id=job_id, payload=fields)

    def get_job(self, job_id: int) -> JobRow | None:
        row = self._conn().execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to(JobRow, row) if row else None

    def list_jobs(self) -> list[JobRow]:
        rows = self._conn().execute("SELECT * FROM jobs ORDER BY id").fetchall()
        return [_row_to(JobRow, r) for r in rows]

    def record_payment(self, job_id: int, *, paid_amount: float, currency: str = "EUR",
                       payment_date: str | None = None) -> None:
        self.update_job(
            job_id,
            payment_status="paid",
            paid_amount=paid_amount,
            payment_date=payment_date or _utcnow()[:10],
        )

    # -- metrics ------------------------------------------------------------------

    def metric(self, *, name: str, value: float, unit: str | None = None,
               lead_id: int | None = None, job_id: int | None = None) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO metrics (ts, lead_id, job_id, name, value, unit)"
                " VALUES (?,?,?,?,?,?)",
                (_utcnow(), lead_id, job_id, name, value, unit),
            )

    # -- run manifests ---------------------------------------------------------------

    def save_run_manifest(self, run_id: str, *, kind: str, status: str,
                          manifest_path: str | None) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO run_manifests (run_id, kind, status, manifest_path,"
                " created_at) VALUES (?,?,?,?,?)",
                (run_id, kind, status, manifest_path, _utcnow()),
            )
            self.audit(conn, actor="system", action="run.save_manifest",
                       entity_type="run", entity_id=None,
                       payload={"run_id": run_id, "kind": kind, "status": status})

    # -- digest -----------------------------------------------------------------------

    def contact_actions_last_7_days(self) -> int:
        """Count real contact actions in the trailing 7 days (audit_log is the
        source of truth: both record_approval() for external_action and
        lead.transition to 'contacted' write there). Feeds the §16 anti-spam
        cap so the queue's daily budget reflects what was actually sent,
        not an assumed zero. Pure read."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(
            timespec="seconds")
        conn = self._conn()
        approvals = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_log"
            " WHERE action = 'approval.record' AND ts >= ?"
            " AND payload_json LIKE '%\"to\": \"contacted\"%'",
            (cutoff,),
        ).fetchone()["n"]
        transitions = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_log"
            " WHERE action = 'lead.transition' AND ts >= ?"
            " AND entity_type = 'lead' AND payload_json LIKE '%\"to\": \"contacted\"%'",
            (cutoff,),
        ).fetchone()["n"]
        return int(approvals + transitions)

    def digest(self) -> dict[str, Any]:
        """Business metrics (plan v3.1 §13). Pure reads; no dashboard."""
        conn = self._conn()
        leads_by_state: dict[str, int] = {
            r["state"]: r["n"] for r in conn.execute(
                "SELECT state, COUNT(*) AS n FROM leads GROUP BY state"
            )
        }
        jobs_by_state: dict[str, int] = {
            r["state"]: r["n"] for r in conn.execute(
                "SELECT state, COUNT(*) AS n FROM jobs GROUP BY state"
            )
        }
        revenue = conn.execute(
            "SELECT COALESCE(SUM(paid_amount), 0) AS paid FROM jobs WHERE payment_status = 'paid'"
        ).fetchone()["paid"]
        outstanding = conn.execute(
            "SELECT COALESCE(SUM(agreed_amount), 0) AS v FROM jobs WHERE payment_status != 'paid' OR payment_status IS NULL"
        ).fetchone()["v"]
        manual_minutes = conn.execute(
            "SELECT COALESCE(SUM(value), 0) AS v FROM metrics WHERE name = 'manual_minutes'"
        ).fetchone()["v"]
        engineering_minutes = conn.execute(
            "SELECT COALESCE(SUM(value), 0) AS v FROM metrics WHERE name = 'engineering_minutes'"
        ).fetchone()["v"]
        savings = conn.execute(
            "SELECT COALESCE(SUM(value), 0) AS v FROM metrics WHERE name = 'automation_savings_minutes'"
        ).fetchone()["v"]
        researched = leads_by_state.get("discovered", 0) + sum(
            n for s, n in leads_by_state.items() if s != "discovered" and s != "blocked"
        )
        # conversion lead -> client: leads that reached won/onboarding/delivery/delivered...
        won = sum(leads_by_state.get(s, 0) for s in
                  ("won", "onboarding", "delivery", "delivered", "follow_up", "repeat"))
        conversion = (won / researched) if researched else 0.0

        # commercial pipeline
        opportunities = sum(leads_by_state.get(s, 0) for s in
                           ("approved", "contacted", "replied", "negotiating", "won",
                            "onboarding", "delivery", "delivered", "follow_up", "repeat"))
        quotes_sent = conn.execute(
            "SELECT COUNT(*) AS n FROM quotes WHERE state IN ('sent','approved','accepted')"
        ).fetchone()["n"]
        quotes_pending = conn.execute(
            "SELECT COUNT(*) AS n FROM quotes WHERE state = 'draft'"
        ).fetchone()["n"]
        contacts_discovered = conn.execute(
            "SELECT COUNT(DISTINCT lead_id) AS n FROM contacts"
        ).fetchone()["n"]
        interactions_count = conn.execute(
            "SELECT COUNT(*) AS n FROM interactions"
        ).fetchone()["n"]
        pending_follow_ups = conn.execute(
            "SELECT COUNT(*) AS n FROM follow_ups WHERE completed = 0"
        ).fetchone()["n"]
        overdue_follow_ups = conn.execute(
            "SELECT COUNT(*) AS n FROM follow_ups WHERE completed = 0 AND due_date < ?",
            (_utcnow()[:10],),
        ).fetchone()["n"]
        # §28: A/B tiers from the same candidate view the daily queue uses.
        # Excludes terminal-reject states; keeps scoring purely for priority.
        tier_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
        try:
            from bam.copilot import classify_tier
            for cand in self.queue_candidates():
                t = classify_tier(
                    score=cand.get("score"),
                    commercial=cand.get("commercial") or {},
                    contactability=cand.get("contactability") or {},
                    recommended_service=cand.get("recommended_service"),
                    lead_state=cand.get("state", ""))
                if t in tier_counts:
                    tier_counts[t] += 1
        except Exception:
            pass  # digest must never fail for copilot import issues

        return {
            "leads_by_state": leads_by_state,
            "jobs_by_state": jobs_by_state,
            "leads_researched": researched,
            "leads_won": won,
            "lead_to_client_conversion": round(conversion, 4),
            "revenue_paid": float(revenue),
            "revenue_outstanding": float(outstanding),
            "manual_minutes": float(manual_minutes),
            "engineering_minutes": float(engineering_minutes),
            "automation_savings_minutes": float(savings),
            "revenue_per_manual_hour": (
                round(float(revenue) / (manual_minutes / 60), 2) if manual_minutes else None
            ),
            "revenue_per_engineering_hour": (
                round(float(revenue) / (engineering_minutes / 60), 2) if engineering_minutes else None
            ),
            "commercial_pipeline": {
                "opportunities": opportunities,
                "quotes_sent": quotes_sent,
                "quotes_pending": quotes_pending,
                "contacts_discovered": contacts_discovered,
                "interactions": interactions_count,
                "pending_follow_ups": pending_follow_ups,
                "overdue_follow_ups": overdue_follow_ups,
            },
            "tiers": tier_counts,
            "source_quality": self.source_quality(),
            "query_quality": self.query_quality(limit=5),
        }

    # -- backup / restore -----------------------------------------------------------

    def _verify_backup_roundtrip(self, backup_path: Path) -> dict[str, Any]:
        """Prove a backup is a restorable snapshot of the live DB.

        1. integrity_check on the backup file itself;
        2. row counts of every user table must match the live DB (taken under
           the store write-lock, so the snapshot point is consistent);
        3. a real restore drill: file-copy the backup to a scratch path and
           integrity-check the copy (exactly what restore() does).

        Raises RuntimeError on any failure - the caller must treat the
        backup as NOT proven.
        """
        import shutil
        import tempfile

        conn = sqlite3.connect(str(backup_path))
        try:
            check = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise RuntimeError(f"backup failed integrity_check: {check}")
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                    " AND name NOT LIKE 'sqlite_%' ORDER BY name")
            ]
            backup_counts = {
                t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                for t in tables
            }
            check_rows = conn.execute(
                "SELECT MAX(version) FROM schema_meta").fetchone()[0] \
                if any(t == "schema_meta" for t in tables) else None
        finally:
            conn.close()

        # live counts under the write lock (consistent snapshot point)
        with self.transaction() as live:
            live_counts = {
                t: live.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                for t in tables
            }
        mismatched = {
            t: (live_counts[t], backup_counts[t])
            for t in tables if live_counts[t] != backup_counts[t]
        }
        if mismatched:
            raise RuntimeError(
                f"backup row counts diverge from live DB: {mismatched}")

        # restore drill: same file-copy mechanics as restore(), into scratch
        with tempfile.TemporaryDirectory() as td:
            scratch = Path(td) / "drill.db"
            shutil.copy2(backup_path, scratch)
            drill = sqlite3.connect(str(scratch))
            try:
                check2 = drill.execute("PRAGMA integrity_check").fetchone()[0]
                if check2 != "ok":
                    raise RuntimeError(f"restore drill failed integrity_check: {check2}")
            finally:
                drill.close()

        return {
            "verified": True,
            "tables": len(tables),
            "rows": sum(backup_counts.values()),
            "schema_version": check_rows,
        }

    def backup(self, backup_dir: Path | None = None) -> dict[str, Any]:
        """Create a verified, consistent SQLite backup.

        The roundtrip is verified automatically on every backup (integrity,
        row counts vs live, restore drill). A backup that cannot be proven
        is renamed to *.failed and RuntimeError is raised - we never present
        an unproven backup as a safety net.

        Retention: after a verified backup, older verified backups are pruned
        down to `backup.keep` (config.yaml; default 5). Quarantined *.failed
        files are never pruned - they are failure evidence.
        """
        import hashlib

        raw_keep = self.config.raw.get("backup", {}).get("keep", 5)
        try:
            keep = int(raw_keep)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"backup.keep must be an integer, got {raw_keep!r}") from exc
        if keep < 1:
            raise ValueError(f"backup.keep must be >= 1, got {keep}")

        bd = backup_dir or (self.config.paths.database.parent.parent / "backups")
        bd.mkdir(parents=True, exist_ok=True)
        ts = _utcnow().replace(":", "-").replace(" ", "_")
        dest = bd / f"bam_{ts}.db"
        counter = 1
        while dest.exists():  # two backups within the same second must not collide
            dest = bd / f"bam_{ts}_{counter}.db"
            counter += 1
        # bound parameter (paths with quotes used to break the SQL literal)
        with self.transaction() as conn:
            conn.execute("VACUUM INTO ?", (str(dest),))
        size = dest.stat().st_size
        sha = hashlib.sha256(dest.read_bytes()).hexdigest()
        try:
            verification = self._verify_backup_roundtrip(dest)
        except RuntimeError:
            dest.rename(dest.with_suffix(".failed"))
            self.audit_standalone(actor="system", action="db.backup_failed",
                                  payload={"path": str(dest.with_suffix(".failed")),
                                           "size": size, "sha256": sha})
            raise
        payload = {"path": str(dest), "size": size, "sha256": sha,
                   "kept": keep, **verification}
        self.audit_standalone(actor="system", action="db.backup", payload=payload)
        payload["pruned"] = [str(p) for p in self._prune_backups(bd, keep)]
        return payload

    def _prune_backups(self, backup_dir: Path, keep: int) -> list[Path]:
        """Delete the oldest verified backups (bam_*.db) beyond `keep`,
        oldest first by filename (names embed a UTC timestamp, so name order
        IS time order). *.failed quarantine files are never touched. A file
        that cannot be deleted (locked, race with another process) is left
        in place - pruning never fails a good backup. Each pruning run that
        deletes something is audited."""
        backups = sorted(backup_dir.glob("bam_*.db"))
        excess = len(backups) - keep
        if excess <= 0:
            return []
        pruned: list[Path] = []
        for old in backups[:excess]:
            try:
                old.unlink()
                pruned.append(old)
            except OSError:
                continue
        if pruned:
            self.audit_standalone(
                actor="system", action="db.backup_pruned",
                payload={"kept": len(backups) - len(pruned),
                         "pruned": [str(p) for p in pruned]})
        return pruned

    def restore(self, backup_path: Path) -> bool:
        """Restore database from a verified backup. Creates a consistent
        safety copy of the pre-restore state first (SQLite backup API,
        WAL-safe). Refuses to touch the live DB if the backup fails its
        integrity check - fail closed."""
        import shutil

        if not backup_path.exists():
            raise ValueError(f"backup not found: {backup_path}")

        # 1. verify BEFORE touching the live DB (non-SQLite garbage fails closed)
        check_conn = sqlite3.connect(str(backup_path))
        try:
            try:
                check = check_conn.execute("PRAGMA integrity_check").fetchone()[0]
            except sqlite3.DatabaseError as exc:
                raise RuntimeError(
                    f"backup {backup_path} is not a valid database ({exc}); "
                    "live DB left untouched") from exc
        finally:
            check_conn.close()
        if check != "ok":
            raise RuntimeError(
                f"backup {backup_path} failed integrity_check ({check}); "
                "live DB left untouched")

        # 2. consistent pre-restore safety copy (WAL-aware, unlike file copy)
        safety = self.db_path.with_suffix(f".pre_restore_{_utcnow().replace(':', '-')}.db")
        src = sqlite3.connect(str(self.db_path))
        dst = sqlite3.connect(str(safety))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        # 3. swap in the backup; stale WAL/SHM from the old DB must not
        #    survive (SQLite would replay old WAL frames over the new file)
        self.close()
        for suffix in ("-wal", "-shm"):
            side = self.db_path.with_name(self.db_path.name + suffix)
            if side.exists():
                side.unlink()
        shutil.copy2(backup_path, self.db_path)

        # 4. audit the restore (auditable in the restored DB itself)
        self.audit_standalone(actor="system", action="db.restore",
                              payload={"from": str(backup_path),
                                       "safety_copy": str(safety),
                                       "integrity": check})
        return True
