"""Customers' own domains for published sites.

1. Someone allowed to deploy adds a hostname to a project (www.shop.example).
2. They prove they control it with a DNS TXT record:  _novara-challenge.www.shop.example = <token>
   and point the hostname at the sites host with a CNAME (NOVARA_SITES_CNAME_TARGET).
3. Once proven, requests for that Host get the project's production site (main.py routes them here),
   and GET /api/v1/public/domains/check?domain=... answers 200, which is what a TLS proxy such as
   Caddy (on-demand TLS, see infrastructure/deployment/Caddyfile) asks before getting a certificate.

Hostnames are kept in lower case and IDNA form, so Arabic domains work. Several organizations may
claim a hostname while it is unproven; the first to prove it gets it.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
import time
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from novara.db import utcnow
from novara.studio.models import SiteDomain, SiteRoute

CHALLENGE = "_novara-challenge"
CACHE_SECONDS = 60
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_RESERVED = (".localhost", ".local", ".internal", ".test", ".invalid", ".example", ".arpa")


class DomainError(ValueError):
    pass


def normalise(hostname: str) -> str:
    host = hostname.strip().lower().rstrip(".")
    if "://" in host or "/" in host or ":" in host or "*" in host:
        raise DomainError("give just the host name, like www.example.com")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise DomainError("that isn't a valid host name")
    try:
        ipaddress.ip_address(host)
        raise DomainError("use a host name, not an IP address")
    except ValueError:
        pass
    labels = host.split(".")
    if len(host) > 253 or len(labels) < 2 or not all(_LABEL.match(label) for label in labels) or labels[-1].isdigit():
        raise DomainError("that isn't a valid host name")
    if host == "localhost" or host.endswith(_RESERVED):
        raise DomainError("that name can't be used on the internet")
    return host


def own_hosts(settings) -> set[str]:
    """The platform's own host names: never a customer's domain."""
    hosts = {"localhost", "127.0.0.1", "testserver"}
    for url in (settings.app_base_url, settings.sites_base_url):
        if url:
            hosts.add((urlsplit(url).hostname or "").lower())
    if settings.sites_cname_target:
        hosts.add(settings.sites_cname_target.lower())
    return {h for h in hosts if h}


def cname_target(settings) -> str:
    return settings.sites_cname_target or (urlsplit(settings.sites_base_url).hostname if settings.sites_base_url else "") \
        or (urlsplit(settings.app_base_url).hostname or "")


def new_token() -> str:
    return "novara-" + secrets.token_urlsafe(24)


def txt_records(name: str) -> list[str]:
    """The TXT strings published for ``name`` (empty if there are none)."""
    import dns.exception
    import dns.resolver

    try:
        answer = dns.resolver.resolve(name, "TXT", lifetime=5)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as e:
        raise DomainError(f"the DNS lookup didn't answer ({type(e).__name__}); try again in a few minutes")
    return [b"".join(r.strings).decode("utf-8", "replace") for r in answer]


def verify(db: Session, domain: SiteDomain, lookup=txt_records) -> bool:
    domain.last_checked_at = utcnow()
    taken = db.scalar(select(SiteDomain.id).where(SiteDomain.hostname == domain.hostname, SiteDomain.id != domain.id,
                                                  SiteDomain.verified_at.is_not(None)))
    if taken:
        domain.last_error = "another organization has already proven this domain"
        return False
    try:
        found = lookup(f"{CHALLENGE}.{domain.hostname}")
    except DomainError as e:
        domain.last_error = str(e)[:300]
        return False
    if domain.token not in (r.strip() for r in found):
        domain.last_error = (f"no TXT record {CHALLENGE}.{domain.hostname} with the expected value yet "
                             "(DNS changes can take a while)")
        return False
    domain.verified_at, domain.last_error = utcnow(), ""
    forget(domain.hostname)
    return True


# Which site a Host gets, cached briefly so unknown hosts don't cost a query each time.
_cache: dict[str, tuple[float, bool, str | None]] = {}


def forget(hostname: str) -> None:
    _cache.pop(hostname, None)


def _proven(db: Session, hostname: str) -> SiteDomain | None:
    return db.scalar(select(SiteDomain).where(SiteDomain.hostname == hostname, SiteDomain.verified_at.is_not(None)))


def production_route(db: Session, hostname: str) -> SiteRoute | None:
    domain = _proven(db, hostname)
    if domain is None:
        return None
    return db.scalar(select(SiteRoute).where(SiteRoute.project_id == domain.project_id,
                                            SiteRoute.organization_id == domain.organization_id,
                                            SiteRoute.environment == "production", SiteRoute.active.is_(True)))


def lookup(db: Session, hostname: str) -> tuple[bool, str | None]:
    """(is this a proven custom domain, the site key of its live production site or None)."""
    now = time.monotonic()
    hit = _cache.get(hostname)
    if hit and hit[0] > now:
        return hit[1], hit[2]
    proven = _proven(db, hostname) is not None
    route = production_route(db, hostname) if proven else None
    _cache[hostname] = (now + CACHE_SECONDS, proven, route.site_key if route else None)
    return proven, route.site_key if route else None
