"""Web Push: encryption as RFC 8291 specifies it (its own test vector), VAPID signatures, subscriptions that
only point at real push services, and notifications that reach a member's devices after commit."""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from novara import push
from novara.automation.models import Notification
from novara.tenancy import bind_tenant

OWNER = "owner@push.example.com"
ENDPOINT = "https://fcm.googleapis.com/fcm/send/device-123"


def decrypt(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """What the browser does with a push message (RFC 8291 / RFC 8188)."""
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    as_public_raw = body[21:21 + idlen]
    ciphertext = body[21 + idlen:]
    assert rs == 4096
    ua_public_raw = ua_private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public_raw))
    h = lambda k, d: push._hmac(k, d)  # noqa: E731
    ikm = h(h(auth, shared), b"WebPush: info\x00" + ua_public_raw + as_public_raw + b"\x01")
    prk = h(salt, ikm)
    plain = AESGCM(h(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]).decrypt(
        h(prk, b"Content-Encoding: nonce\x00\x01")[:12], ciphertext, None)
    return plain.rstrip(b"\x00")[:-1]           # drop the padding delimiter (0x02)


def test_encryption_matches_rfc_8291s_example():
    ua_private = ec.derive_private_key(int.from_bytes(push.unb64u("q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"), "big"),
                                       ec.SECP256R1())
    as_private = ec.derive_private_key(int.from_bytes(push.unb64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
                                       ec.SECP256R1())
    body = push.encrypt(b"When I grow up, I want to be a watermelon",
                        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                        "BTBZMqHH6r4Tts7J_aSIgg", salt=push.unb64u("DGv6ra1nlYgDCS1FRnbzlw"), server_key=as_private)
    assert push.b64u(body) == ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmY"
                               "WAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSg"
                               "Sxsj_Qulcy4a-fN")
    assert decrypt(body, ua_private, push.unb64u("BTBZMqHH6r4Tts7J_aSIgg")) == b"When I grow up, I want to be a watermelon"


def test_vapid_signatures_and_allowed_endpoints():
    key = ec.generate_private_key(ec.SECP256R1())
    header = push.vapid_header(key, ENDPOINT, "mailto:ops@novara.example.com", now=1_700_000_000)
    token, k = header.removeprefix("vapid t=").split(", k=")
    signing_input, sig = token.rsplit(".", 1)
    claims = json.loads(push.unb64u(signing_input.split(".")[1]))
    assert claims == {"aud": "https://fcm.googleapis.com", "exp": 1_700_000_000 + 12 * 3600, "sub": "mailto:ops@novara.example.com"}
    raw = push.unb64u(sig)
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.unb64u(k))
    public.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                  signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    with pytest.raises(InvalidSignature):
        public.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), 1), signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    for ok in (ENDPOINT, "https://updates.push.services.mozilla.com/wpush/v2/x", "https://web.push.apple.com/abc",
               "https://wns2-par02p.notify.windows.com/w/?token=x"):
        assert push.allowed_endpoint(ok)
    for bad in ("http://fcm.googleapis.com/x", "https://127.0.0.1/x", "https://evil.example.com/fcm.googleapis.com",
                "https://fcm.googleapis.com.evil.example.com/x", "https://user:pw@fcm.googleapis.com/x",
                "https://fcm.googleapis.com:8443/x", "https://internal.local/x"):
        assert not push.allowed_endpoint(bad)
    assert push.allowed_endpoint("https://push.corp.example/x", ("push.corp.example",))


@pytest.fixture
def device(api, app):
    """A member with one subscribed device, and a push service that records what reaches it."""
    received: list[httpx.Request] = []
    answers: list[int] = []

    def service(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(answers.pop() if answers else 201)

    sender = push.PushSender(app.state.settings, app.state.sessionmaker,
                             http=httpx.Client(transport=httpx.MockTransport(service)), sync=True)
    app.state.push = sender
    push.configure(sender)
    api.signup(OWNER)
    org = api.create_org(OWNER, "push-co")
    ua_private = ec.generate_private_key(ec.SECP256R1())
    p256dh = push.b64u(ua_private.public_key().public_bytes(serialization.Encoding.X962,
                                                              serialization.PublicFormat.UncompressedPoint))
    auth = b"0123456789abcdef"
    r = api.req("POST", OWNER, "/push/subscriptions",
                json={"endpoint": ENDPOINT, "keys": {"p256dh": p256dh, "auth": push.b64u(auth)}})
    assert r.status_code == 201, r.text
    yield {"org": org, "received": received, "answers": answers, "private": ua_private, "auth": auth, "sender": sender,
           "p256dh": p256dh}
    push.configure(None)


def notify(app, org, user_id, title):
    with app.state.sessionmaker() as db:
        bind_tenant(db, org)
        db.add(Notification(user_id=user_id, title=title, body="تفاصيل"))
        db.commit()


def user_id(app):
    from novara.modules.identity.models import User
    with app.state.sessionmaker() as db:
        return db.scalar(select(User.id).where(User.email == OWNER))


def test_a_notification_reaches_the_members_device(api, app, device):
    key = api.req("GET", OWNER, "/push/key").json()["public_key"]
    assert len(push.unb64u(key)) == 65 and key == device["sender"].public_key
    devices = api.req("GET", OWNER, "/push/subscriptions").json()
    assert [d["service"] for d in devices] == ["fcm.googleapis.com"]

    notify(app, device["org"], user_id(app), "طلب إجازة بانتظار قرارك")
    (sent,) = device["received"]
    assert (sent.headers["content-encoding"], sent.headers["ttl"]) == ("aes128gcm", "86400")
    assert sent.headers["authorization"].startswith("vapid t=") and f"k={key}" in sent.headers["authorization"]
    data = json.loads(decrypt(sent.content, device["private"], device["auth"]))
    assert data["title"] == "طلب إجازة بانتظار قرارك" and data["url"] == f"/o/{device['org']}/notifications"

    with app.state.sessionmaker() as db:                                      # rolled back: nothing is sent
        bind_tenant(db, device["org"])
        db.add(Notification(user_id=user_id(app), title="never", body=""))
        db.flush()
        db.rollback()
    assert len(device["received"]) == 1

    assert api.req("POST", OWNER, "/push/test").json() == {"sent": 1}
    device["answers"].append(410)                                              # the browser unsubscribed
    notify(app, device["org"], user_id(app), "Another")
    assert api.req("GET", OWNER, "/push/subscriptions").json() == []


def test_subscriptions_are_checked_and_removable(api, app, device):
    keys = {"p256dh": device["p256dh"], "auth": push.b64u(device["auth"])}
    for endpoint in ("https://169.254.169.254/latest/meta-data", "http://fcm.googleapis.com/x", "https://example.com/push"):
        r = api.req("POST", OWNER, "/push/subscriptions", json={"endpoint": endpoint, "keys": keys})
        assert r.status_code == 422, endpoint
    bad_key = api.req("POST", OWNER, "/push/subscriptions", json={"endpoint": ENDPOINT + "2", "keys": {
        "p256dh": "A" * 87, "auth": push.b64u(device["auth"])}})
    assert bad_key.status_code == 422
    assert api.req("POST", OWNER, "/push/subscriptions/remove", json={"endpoint": ENDPOINT}).status_code == 204
    assert api.req("GET", OWNER, "/push/subscriptions").json() == []
    assert api.c.post("/api/v1/push/subscriptions", json={"endpoint": ENDPOINT, "keys": keys}).status_code == 401


def test_push_is_off_in_production_without_a_key(api, app, tmp_path):
    settings = replace(app.state.settings, env="production", storage_root=str(tmp_path / "prod"))
    off = push.PushSender(settings, app.state.sessionmaker, sync=True)
    assert not off.enabled
    app.state.push = off
    assert api.c.get("/api/v1/push/key").status_code == 503
    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    assert push.PushSender(replace(settings, vapid_private_key=pem), app.state.sessionmaker, sync=True).enabled
    raw = push.b64u((12345678901234567890).to_bytes(32, "big"))
    assert push.PushSender(replace(settings, vapid_private_key=raw), app.state.sessionmaker, sync=True).enabled
