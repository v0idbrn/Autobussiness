"""Denylist engine.

Policy lists are checked at intake, pre-fetch, pre-queue, pre-approval and
pre-contact (plan v3.1 §10... see AGENTS.md rule 10). Matching is exact on
registered domain (case-insensitive) and on normalized company name.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from bam.config import load_denylist_yaml

CATEGORIES = ("do_not_research", "do_not_contact", "blocked_domain", "blocked_company")


def normalize_domain(domain: str) -> str:
    d = domain.strip().lower()
    d = d.removeprefix("http://").removeprefix("https://")
    d = d.split("/")[0].split(":")[0]
    d = d.removeprefix("www.")
    return d


def normalize_company(name: str) -> str:
    return " ".join(name.strip().lower().split())


@dataclass(frozen=True)
class DenyHit:
    category: str
    value: str
    reason: str

    @property
    def status(self) -> str:
        return f"BLOCKED_{self.category.upper()}"


class Denylist:
    def __init__(self, data: dict[str, list[str]] | None = None) -> None:
        data = data or {}
        self._domains: dict[str, str] = {}
        self._companies: dict[str, str] = {}
        self._rules: list[tuple[str, str]] = []
        for cat in CATEGORIES:
            for entry in data.get(cat, []) or []:
                self._domains[entry.strip().lower()] = cat
        for entry in data.get("blocked_company", []) or []:
            self._companies[normalize_company(str(entry))] = "blocked_company"
        for entry in data.get("do_not_contact", []) or []:
            if not entry.strip().lower().startswith(("http", ".")) and " " in entry:
                # free-text company names in do_not_contact
                self._companies[normalize_company(entry)] = "do_not_contact"
        for rule in (data.get("category_rules") or []):
            if isinstance(rule, dict) and rule.get("match") and rule.get("category"):
                self._rules.append(
                    (normalize_company(str(rule["match"])), str(rule["category"]))
                )

    @classmethod
    def load(cls, root: Path | None = None) -> "Denylist":
        # root=None -> project_root() (BAM_ROOT-aware); never silently empty.
        return cls(load_denylist_yaml(root))

    def check(self, *, domain: str | None = None, company: str | None = None) -> DenyHit | None:
        if domain:
            d = normalize_domain(domain)
            if d in self._domains:
                cat = self._domains[d]
                return DenyHit(cat, d, f"domain {d} is listed under {cat}")
            for listed, cat in self._domains.items():
                if d.endswith("." + listed):
                    sub = f"{d} is a subdomain of listed {listed}"
                    return DenyHit(cat, d, sub)
        if company:
            c = normalize_company(company)
            if c in self._companies:
                cat = self._companies[c]
                return DenyHit(cat, c, f"company {c!r} is listed under {cat}")
            for needle, cat in self._rules:
                if needle and needle in c:
                    return DenyHit(cat, c, f"company {c!r} matches rule {needle!r} ({cat})")
        return None


def check_denylist(
    deny: Denylist,
    *,
    domain: str | None = None,
    company: str | None = None,
) -> DenyHit | None:
    """Convenience wrapper preserving the V1 check signature."""
    return deny.check(domain=domain, company=company)
