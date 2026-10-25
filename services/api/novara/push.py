"""Web Push: notifications on members' devices, even with NOVARA closed.

A browser subscribes with the server's VAPID public key and gives back an endpoint at its push service
plus a key pair to encrypt for. Every notification created for a member (approvals, tasks, leave,
payments...) is then sent to each of their devices once the transaction commits, encrypted as RFC 8291
says (aes128gcm) and signed with a VAPID JWT (RFC 8292). A device the push service no longer knows
(404/410) is forgotten.

The server only ever posts to the push services browsers use (PUSH_HOSTS, extended with
NOVARA_PUSH_HOSTS): a subscription is data from the browser, so an arbitrary endpoint would let anyone make
the server call an address of their choosing.

Keys: NOVARA_VAPID_PRIVATE_KEY (PEM, or base64url of the 32-byte P-256 private value). Without it, outside
production a key is generated once and kept under the storage root; in production push stays off.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, String, Text, delete, event, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from novara.db import Base, IdMixin, TimestampMixin, utcnow
from novara.deps import current_user, get_db
from novara.modules.identity.models import User

log = logging.getLogger("novara.push")
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "push.services.mozilla.com", "notify.windows.com",
              "push.apple.com")
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
RECORD_SIZE = 4096


class PushSubscription(IdMixin, TimestampMixin, Base):
    """One of a member's devices. Not tenant-scoped: a device gets the member's notifications from every
    organization they belong to (each says which)."""

    __tablename__ = "push_subscriptions"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)
    auth: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------- crypto


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _raw_public(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None,
            server_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """The request body for one subscription (RFC 8291 + RFC 8188, a single record)."""
    ua_public_raw = unb64u(p256dh)
    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public_raw)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public_raw = _raw_public(server_key.public_key())
    shared = server_key.exchange(ec.ECDH(), ua_public)
    prk_key = _hmac(unb64u(auth), shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public_raw + as_public_raw + b"\x01")
    salt = salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    if len(payload) + 1 + 16 > RECORD_SIZE - 86:
        raise ValueError("the notification is too long for one push message")
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", RECORD_SIZE, len(as_public_raw)) + as_public_raw + ciphertext


def vapid_header(key: ec.EllipticCurvePrivateKey, endpoint: str, subject: str, now: float | None = None) -> str:
    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int((now or time.time()) + 12 * 3600), "sub": subject}
    signing_input = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode()) + "." + \
        b64u(json.dumps(claims, separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    token = signing_input + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={token}, k={b64u(_raw_public(key.public_key()))}"


def load_key(settings) -> ec.EllipticCurvePrivateKey | None:
    """The VAPID key from the environment, or (outside production) one kept under the storage root."""
    value = settings.vapid_private_key.strip()
    if value:
        if value.startswith("-----BEGIN"):
            key = serialization.load_pem_private_key(value.encode(), None)
        else:
            key = ec.derive_private_key(int.from_bytes(unb64u(value), "big"), ec.SECP256R1())
        assert isinstance(key, ec.EllipticCurvePrivateKey)
        return key
    if settings.env == "production":
        return None
    path = Path(settings.storage_root) / "vapid-private.pem"
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), None)  # type: ignore[return-value]
    key = ec.generate_private_key(ec.SECP256R1())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    os.chmod(path, 0o600)
    return key


def allowed_endpoint(endpoint: str, extra_hosts: tuple[str, ...] = ()) -> bool:
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
        return False
    return any(host == h or host.endswith("." + h) for h in PUSH_HOSTS + extra_hosts)


# ---------------------------------------------------------------- sending


class PushSender:
    """Sends after commit. Tests and the worker pass sync=True; requests hand the sending to a small pool."""

    def __init__(self, settings, sessionmaker, http: httpx.Client | None = None, sync: bool = False):
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.http = http
        self.key = load_key(settings)
        self.subject = settings.vapid_subject or f"mailto:{_address(settings.mail_from)}"
        self.extra_hosts = tuple(h.strip().lower() for h in settings.push_hosts if h.strip())
        self.sync = sync
        self._pool = None if sync else ThreadPoolExecutor(max_workers=4, thread_name_prefix="push")

    @property
    def enabled(self) -> bool:
        return self.key is not None

    @property
    def public_key(self) -> str:
        return b64u(_raw_public(self.key.public_key())) if self.key else ""

    def dispatch(self, messages: list[tuple[str, dict]]) -> None:
        """Look up the devices here (one query, nothing to do for most members) and send off-thread."""
        if not self.enabled or not messages:
            return
        with self.sessionmaker() as db:
            subs = list(db.scalars(select(PushSubscription).where(
                PushSubscription.user_id.in_({user_id for user_id, _ in messages}))))
        batch = [(s.id, s.endpoint, s.p256dh, s.auth, data) for user_id, data in messages for s in subs if s.user_id == user_id]
        if not batch:
            return
        if self.sync:
            self._send_batch(batch)
        else:
            self._pool.submit(self._send_batch, batch)

    def send(self, messages: list[tuple[str, dict]]) -> int:
        """messages: (user_id, {"title", "body", "url", "tag"}), sent now. Returns how many deliveries succeeded."""
        with self.sessionmaker() as db:
            subs = list(db.scalars(select(PushSubscription).where(
                PushSubscription.user_id.in_({user_id for user_id, _ in messages}))))
        return self._send_batch([(s.id, s.endpoint, s.p256dh, s.auth, data)
                                 for user_id, data in messages for s in subs if s.user_id == user_id])

    def _send_batch(self, batch: list[tuple[str, str, str, str, dict]]) -> int:
        delivered, gone = [], []
        for sub_id, endpoint, p256dh, auth, data in batch:
            outcome = self._deliver(endpoint, p256dh, auth, data)
            (delivered if outcome == "sent" else gone if outcome == "gone" else []).append(sub_id)
        if delivered or gone:
            with self.sessionmaker() as db:
                if gone:
                    db.execute(delete(PushSubscription).where(PushSubscription.id.in_(gone)))
                for sub in db.scalars(select(PushSubscription).where(PushSubscription.id.in_(delivered))):
                    sub.last_sent_at = utcnow()
                db.commit()
        return len(delivered)

    def _deliver(self, endpoint: str, p256dh: str, auth: str, data: dict) -> str:
        if not allowed_endpoint(endpoint, self.extra_hosts):
            return "skipped"
        try:
            body = encrypt(json.dumps(data, ensure_ascii=False).encode(), p256dh, auth)
            client = self.http or httpx.Client(timeout=TIMEOUT, follow_redirects=False)
            try:
                r = client.post(endpoint, content=body, headers={
                    "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": "86400",
                    "Urgency": "normal", "Authorization": vapid_header(self.key, endpoint, self.subject)})
            finally:
                if self.http is None:
                    client.close()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("push to %s failed: %s", urlsplit(endpoint).hostname, e)
            return "failed"
        if r.status_code in (404, 410):     # the device unsubscribed or the subscription expired
            return "gone"
        if r.status_code >= 400:
            log.warning("push service %s answered %s", urlsplit(endpoint).hostname, r.status_code)
            return "failed"
        return "sent"


def _address(mail_from: str) -> str:
    return mail_from.split("<")[-1].rstrip(">").strip() or "no-reply@localhost"


_sender: PushSender | None = None


def configure(sender: PushSender | None) -> None:
    global _sender
    _sender = sender


def current() -> PushSender | None:
    return _sender


def _register_hooks() -> None:
    from novara.automation.models import Notification

    @event.listens_for(Session, "after_flush")
    def _collect(session: Session, _ctx) -> None:
        for obj in session.new:
            if isinstance(obj, Notification):
                session.info.setdefault("push", []).append((obj.user_id, {
                    "title": obj.title, "body": obj.body[:500], "tag": obj.id,
                    "url": f"/o/{obj.organization_id}/notifications"}))

    @event.listens_for(Session, "after_commit")
    def _send(session: Session) -> None:
        messages = session.info.pop("push", None)
        if messages and _sender is not None:
            _sender.dispatch(messages)

    @event.listens_for(Session, "after_rollback")
    def _drop(session: Session) -> None:
        session.info.pop("push", None)


_register_hooks()


# ---------------------------------------------------------------- API

router = APIRouter(tags=["push"])


class PushKeyOut(BaseModel):
    public_key: str


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=80, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    auth: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class SubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)
    keys: SubscriptionKeys


class UnsubscribeIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)


class DeviceOut(BaseModel):
    id: str
    service: str
    user_agent: str
    created_at: datetime
    last_sent_at: datetime | None


def _sender_or_503(request: Request) -> PushSender:
    sender = request.app.state.push
    if sender is None or not sender.enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "push notifications aren't set up on this server")
    return sender


def _hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()


def _device(sub: PushSubscription) -> DeviceOut:
    return DeviceOut(id=sub.id, service=urlsplit(sub.endpoint).hostname or "", user_agent=sub.user_agent,
                     created_at=sub.created_at, last_sent_at=sub.last_sent_at)


@router.get("/push/key", response_model=PushKeyOut)
def push_key(request: Request):
    """The application server key browsers subscribe with."""
    return PushKeyOut(public_key=_sender_or_503(request).public_key)


@router.get("/push/subscriptions", response_model=list[DeviceOut])
def my_devices(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(PushSubscription).where(PushSubscription.user_id == user.id).order_by(PushSubscription.created_at))
    return [_device(s) for s in rows]


@router.post("/push/subscriptions", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
def subscribe(body: SubscriptionIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Send this member's notifications to this device. The endpoint must be a browser push service."""
    sender = _sender_or_503(request)
    if not allowed_endpoint(body.endpoint, sender.extra_hosts):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "that isn't a push service address")
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(body.keys.p256dh))
        if len(unb64u(body.keys.auth)) != 16:
            raise ValueError
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "the subscription's keys aren't valid") from None
    digest = _hash(body.endpoint)
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint_hash == digest))
    if sub is None:
        sub = PushSubscription(endpoint=body.endpoint, endpoint_hash=digest)
        db.add(sub)
    sub.user_id, sub.p256dh, sub.auth = user.id, body.keys.p256dh, body.keys.auth   # a shared device follows its last user
    sub.user_agent = (request.headers.get("user-agent") or "")[:300]
    db.commit()
    return _device(sub)


@router.post("/push/subscriptions/remove", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def unsubscribe(body: UnsubscribeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.execute(delete(PushSubscription).where(PushSubscription.endpoint_hash == _hash(body.endpoint),
                                              PushSubscription.user_id == user.id))
    db.commit()


@router.post("/push/test")
def test_push(request: Request, user: User = Depends(current_user)) -> dict:
    """Send a notification to the member's own devices, to see that it arrives."""
    sender = _sender_or_503(request)
    sent = sender.send([(user.id, {"title": "NOVARA", "body": "الإشعارات تعمل على هذا الجهاز · Notifications work on this device",
                                   "tag": "novara-test", "url": "/"})])
    return {"sent": sent}
