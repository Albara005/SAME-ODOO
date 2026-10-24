"""Passkeys: added after the password is checked again, then used to sign in, each challenge once."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import struct

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from tests.test_security import OWNER, PASSWORD, enable_mfa

UP, UV, AT = 0x01, 0x04, 0x40  # authenticator data flags: user present, user verified, credential data included


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class SoftAuthenticator:
    """Just enough of a WebAuthn authenticator (P-256, "none" attestation) to exercise the server for real."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(32)
        self.count = 0

    def _cose(self) -> bytes:
        n = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})

    def create(self, options: dict, origin: str, flags: int = UP | UV | AT) -> dict:
        client = json.dumps({"type": "webauthn.create", "challenge": options["challenge"], "origin": origin}).encode()
        auth = (hashlib.sha256(options["rp"]["id"].encode()).digest() + bytes([flags]) + struct.pack(">I", self.count)
                + bytes(16) + struct.pack(">H", len(self.cred_id)) + self.cred_id + self._cose())
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})
        return {"id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": b64u(client), "attestationObject": b64u(attestation),
                             "transports": ["internal"]}}

    def get(self, options: dict, origin: str, flags: int = UP | UV) -> dict:
        self.count += 1
        client = json.dumps({"type": "webauthn.get", "challenge": options["challenge"], "origin": origin}).encode()
        auth = hashlib.sha256(options["rpId"].encode()).digest() + bytes([flags]) + struct.pack(">I", self.count)
        signature = self.key.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": b64u(client), "authenticatorData": b64u(auth),
                             "signature": b64u(signature), "userHandle": None}}


@pytest.fixture
def origin(app) -> str:
    return app.state.settings.app_base_url


def register(api, origin, device, email=OWNER, name="Laptop"):
    start = api.req("POST", email, "/me/passkeys/options", json={"password": PASSWORD})
    assert start.status_code == 200, start.text
    body = start.json()
    return body, api.req("POST", email, "/me/passkeys", json={
        "challenge_id": body["challenge_id"], "name": name, "credential": device.create(body["options"], origin)})


def sign_in(client, origin, device, **kw):
    start = client.post("/api/v1/auth/passkey/options").json()
    return client.post("/api/v1/auth/passkey", json={"challenge_id": start["challenge_id"],
                                                     "credential": device.get(start["options"], origin, **kw)})


def test_a_passkey_is_added_then_signs_in(api, client, app, origin):
    api.signup(OWNER)
    assert api.req("POST", OWNER, "/me/passkeys/options", json={"password": "wrong"}).status_code == 403
    device = SoftAuthenticator()
    started, added = register(api, origin, device)
    sel = started["options"]["authenticatorSelection"]
    assert (started["options"]["rp"]["id"], sel["residentKey"], sel["userVerification"]) == ("localhost", "required", "required")
    assert added.status_code == 201, added.text
    assert [p["name"] for p in api.req("GET", OWNER, "/me/passkeys").json()] == ["Laptop"]
    assert any("passkey" in m.subject.lower() or "مفتاح" in m.subject for m in app.state.mailer.sent)
    # The same challenge can't be used twice, and a device that didn't verify the person is refused.
    again = api.req("POST", OWNER, "/me/passkeys", json={
        "challenge_id": started["challenge_id"], "credential": SoftAuthenticator().create(started["options"], origin)})
    assert again.status_code == 400
    start = api.req("POST", OWNER, "/me/passkeys/options", json={"password": PASSWORD}).json()
    unverified = api.req("POST", OWNER, "/me/passkeys", json={
        "challenge_id": start["challenge_id"], "credential": SoftAuthenticator().create(start["options"], origin, flags=UP | AT)})
    assert unverified.status_code == 400

    signed = sign_in(client, origin, device)
    assert signed.status_code == 200, signed.text
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {signed.json()['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == OWNER
    assert api.req("GET", OWNER, "/me/passkeys").json()[0]["last_used_at"] is not None

    # Replays, other sites, unverified people and cloned devices are all refused.
    start = client.post("/api/v1/auth/passkey/options").json()
    response = device.get(start["options"], origin)
    assert client.post("/api/v1/auth/passkey", json={"challenge_id": start["challenge_id"], "credential": response}).status_code == 200
    assert client.post("/api/v1/auth/passkey", json={"challenge_id": start["challenge_id"], "credential": response}).status_code == 400
    assert sign_in(client, "https://evil.example.com", device).status_code == 401
    assert sign_in(client, origin, device, flags=UP).status_code == 401
    clone = copy.copy(device)
    clone.count = 0                                                                # a copy of the key, its own counter
    assert sign_in(client, origin, clone).status_code == 401                       # the count went backwards
    assert sign_in(client, origin, SoftAuthenticator()).status_code == 401         # not registered here


def test_passkeys_count_as_two_step_and_can_be_removed(api, client, origin):
    api.signup(OWNER)
    enable_mfa(api)
    device = SoftAuthenticator()
    _, added = register(api, origin, device)
    assert added.status_code == 201
    signed = sign_in(client, origin, device)
    assert signed.status_code == 200 and signed.json()["access_token"]           # no app code asked for

    other = "other@example.com"
    api.signup(other)
    pid = added.json()["id"]
    assert api.req("DELETE", other, f"/me/passkeys/{pid}").status_code == 404
    assert api.req("PATCH", OWNER, f"/me/passkeys/{pid}", json={"name": "Phone"}).json()["name"] == "Phone"
    assert api.req("DELETE", OWNER, f"/me/passkeys/{pid}").status_code == 204
    assert sign_in(client, origin, device).status_code == 401


def test_failed_passkey_sign_ins_count_against_the_limit(api, client, origin):
    api.signup(OWNER)
    device = SoftAuthenticator()
    assert register(api, origin, device)[1].status_code == 201
    for _ in range(5):
        assert sign_in(client, origin, device, flags=UP).status_code == 401
    assert sign_in(client, origin, device).status_code == 429
