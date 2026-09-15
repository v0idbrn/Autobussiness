"""Denylist tests: categories, matching, normalization."""

from __future__ import annotations

from bam.denylist import Denylist, normalize_company, normalize_domain


def _dl() -> Denylist:
    return Denylist({
        "do_not_research": ["secret.test"],
        "do_not_contact": ["Former Client Inc"],
        "blocked_domain": ["competitor.io"],
        "blocked_company": ["Bad Actors LLC"],
        "category_rules": [{"match": "government agency", "category": "do_not_contact"}],
    })


def test_normalize_domain() -> None:
    assert normalize_domain("HTTPS://Www.Example.com/path?x=1") == "example.com"
    assert normalize_domain("sub.example.co.uk:8080") == "sub.example.co.uk"


def test_normalize_company() -> None:
    assert normalize_company("  ACME   Corp ") == "acme corp"


def test_blocked_domain_exact() -> None:
    hit = _dl().check(domain="competitor.io")
    assert hit is not None
    assert hit.status == "BLOCKED_BLOCKED_DOMAIN"


def test_blocked_domain_subdomain() -> None:
    hit = _dl().check(domain="portal.competitor.io")
    assert hit is not None


def test_do_not_research() -> None:
    hit = _dl().check(domain="secret.test")
    assert hit.category == "do_not_research"


def test_blocked_company() -> None:
    hit = _dl().check(company="bad actors llc")
    assert hit is not None
    assert hit.category == "blocked_company"


def test_do_not_contact_company() -> None:
    hit = _dl().check(company="former client inc")
    assert hit.category == "do_not_contact"


def test_category_rule() -> None:
    hit = _dl().check(company="Government Agency of Somewhere")
    assert hit is not None


def test_clear_domain_passes() -> None:
    assert _dl().check(domain="normal-business.test") is None
