"""Approval queue (plan v3.1 §9-§10).

Approval is a real transaction recorded by store.record_approval(); this module
adds the queue/history views used by the CLI. No transition logic lives here.
"""

from __future__ import annotations

from typing import Any

from bam.store import Store


def pending_approvals(store: Store) -> list[dict[str, Any]]:
    conn = store._conn()
    rows = conn.execute(
        "SELECT l.id, l.state, l.score, l.confidence, l.recommended_service, l.reason,"
        " c.name AS company, c.domain"
        " FROM leads l JOIN companies c ON c.id = l.company_id"
        " WHERE l.state = 'approval_required' ORDER BY l.score DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def approval_history(store: Store, limit: int = 50) -> list[dict[str, Any]]:
    conn = store._conn()
    rows = conn.execute(
        "SELECT * FROM approvals ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
