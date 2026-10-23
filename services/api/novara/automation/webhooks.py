"""Outgoing webhooks from workflows: signed, and unable to reach anything but the public internet.

Server-side request forgery is the risk: a workflow author could point a webhook at the
platform's own network (databases, cloud metadata, admin ports). So a destination must be
https on a normal port, its name must resolve only to public addresses, the request goes to
the exact address that was checked (no second lookup an attacker could change in between),
the TLS certificate is verified for the original name, and redirects are never followed.

Each delivery is signed with the organization's webhook secret:
    X-Novara-Timestamp: <unix seconds>
    X-Novara-Signature: v1=<hex HMAC-SHA256 of "<timestamp>.<body>">
Receivers recompute the HMAC with the secret (and reject old timestamps). Retries of the
same workflow step carry the same Idempotency-Key, so a receiver can ignore duplicates.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import SplitResult, urlsplit

import httpx
from sqlalchemy import String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from novara import crypto
from novara.db import Base, IdMixin, TimestampMixin
from novara.tenancy import TenantScoped, current_tenant

ALLOWED_PORTS = {443, 8443}
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class UnsafeDestination(ValueError):
    pass


class WebhookSecret(IdMixin, TimestampMixin, TenantScoped, Base):
    """The organization's signing secret, encrypted at rest. Rotating it replaces the row's value."""

    __tablename__ = "webhook_secrets"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_webhook_secret_org"),)

    secret: Mapped[str] = mapped_column(Text, nullable=False)
    hint: Mapped[str] = mapped_column(String(12), nullable=False)  # last characters, to tell secrets apart


def check_url(url: str) -> SplitResult:
    """Static checks: https, a host name or public address, no credentials, a normal port."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise UnsafeDestination("not a valid URL")
    if parts.scheme != "https":
        raise UnsafeDestination("webhooks must use https")
    if not parts.hostname or parts.username or parts.password:
        raise UnsafeDestination("the URL needs a host and no user name or password")
    if port is not None and port not in ALLOWED_PORTS:
        raise UnsafeDestination(f"only ports {sorted(ALLOWED_PORTS)} are allowed")
    host = parts.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UnsafeDestination("that host is not on the public internet")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        return parts
    if literal.version == 6 and literal.ipv4_mapped:
        literal = literal.ipv4_mapped
    if not literal.is_global:
        raise UnsafeDestination("that address is not on the public internet")
    return parts


def resolve_public(host: str, port: int) -> str:
    """One address for ``host``, after checking that every address it resolves to is public."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise UnsafeDestination(f"cannot resolve {host}")
    addresses = [info[4][0] for info in infos]
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:  # ::ffff:10.0.0.1 is 10.0.0.1
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise UnsafeDestination(f"{host} resolves to an address that is not on the public internet")
    if not addresses:
        raise UnsafeDestination(f"cannot resolve {host}")
    return addresses[0]


def post_pinned(url: str, body: bytes, headers: dict[str, str]) -> int:
    """POST to the checked address itself, verifying TLS for the original host name."""
    parts = check_url(url)
    port = parts.port or 443
    address = resolve_public(parts.hostname, port)
    netloc = f"[{address}]:{port}" if ":" in address else f"{address}:{port}"
    target = f"https://{netloc}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")
    host_header = parts.hostname if port == 443 else f"{parts.hostname}:{port}"
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False) as client:
        response = client.post(target, content=body, headers={**headers, "Host": host_header},
                               extensions={"sni_hostname": parts.hostname})
    return response.status_code


def sign(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def _new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def org_secret(db: Session, settings, *, rotate: bool = False) -> str:
    """The bound organization's secret, created on first use."""
    row = db.scalar(select(WebhookSecret))
    if row is not None and not rotate:
        return crypto.decrypt(settings, row.secret, context=current_tenant(db))
    raw = _new_secret()
    sealed = crypto.encrypt(settings, raw, context=current_tenant(db))
    if row is None:
        db.add(WebhookSecret(secret=sealed, hint=raw[-6:]))
    else:
        row.secret, row.hint = sealed, raw[-6:]
    db.flush()
    return raw


@dataclass
class WebhookSender:
    """What the workflow engine uses to deliver webhooks. Tests swap ``transport``."""

    settings: object
    transport: Callable[[str, bytes, dict[str, str]], int] = post_pinned

    def send(self, db: Session, url: str, payload: dict, *, idempotency_key: str) -> int:
        secret = org_secret(db, self.settings)
        body = json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True).encode()
        stamp = int(time.time())
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "NOVARA-Webhooks/1",
            "X-Novara-Timestamp": str(stamp),
            "X-Novara-Signature": f"v1={sign(secret, stamp, body)}",
            "Idempotency-Key": idempotency_key,
        }
        return self.transport(url, body, headers)
