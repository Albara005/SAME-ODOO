"""Proof of work on requests from published sites: solved once per request, bound to the key, harder under load."""

from __future__ import annotations

import hashlib

from novara.studio import proof
from novara.studio.models import AppDataKey
from novara.tenancy import bind_tenant
from tests.test_bookings import OWNER as BOOKINGS_OWNER, at, cal  # noqa: F401
from tests.test_web_orders import OWNER, project  # noqa: F401


def solve(challenge: str, bits: int) -> str:
    n = 0
    while proof.leading_zero_bits(hashlib.sha256(f"{challenge}:{n}".encode()).digest()) < bits:
        n += 1
    return str(n)


def unsolved(challenge: str, bits: int) -> str:
    n = 0
    while proof.leading_zero_bits(hashlib.sha256(f"{challenge}:{n}".encode()).digest()) >= bits:
        n += 1
    return str(n)


def challenge(client, key):
    r = client.get("/api/v1/public/challenge", headers={"X-Novara-Key": key})
    assert r.status_code == 200, r.text
    return r.json()


def solved(client, key):
    c = challenge(client, key)
    return {"challenge": c["challenge"], "nonce": solve(c["challenge"], c["difficulty"])}


def order(client, key, product, **extra):
    return client.post("/api/v1/public/orders", headers={"X-Novara-Key": key}, json={
        "name": "Hala Stores", "phone": "+966500000000", "lines": [{"product_id": product, "quantity": 1}], **extra})


def test_an_order_request_needs_a_solved_challenge_used_once(api, client, project, monkeypatch):
    keys = f"/orgs/{project['org']}/studio/projects/{project['project']}/data-keys"
    made = api.req("POST", OWNER, keys, json={"name": "Shop", "scopes": ["catalog", "orders"]}).json()
    assert made["require_proof"] is True                                       # on unless asked otherwise
    key, product = made["key"], project["product"]
    catalog_only = api.req("POST", OWNER, keys, json={"name": "Menu", "scopes": ["catalog"]}).json()["key"]
    assert client.get("/api/v1/public/challenge", headers={"X-Novara-Key": catalog_only}).status_code == 401

    missing = order(client, key, product)
    assert missing.status_code == 403 and "proof-of-work" in missing.json()["detail"]
    assert missing.headers["access-control-allow-origin"] == "*"              # the site's script can read why

    c = challenge(client, key)
    assert (c["algorithm"], c["difficulty"]) == ("SHA-256", proof.BASE_BITS)
    lazy = order(client, key, product, proof={"challenge": c["challenge"], "nonce": unsolved(c["challenge"], c["difficulty"])})
    assert lazy.status_code == 403 and "isn't solved" in lazy.json()["detail"]
    good = {"challenge": c["challenge"], "nonce": solve(c["challenge"], c["difficulty"])}
    assert order(client, key, product, proof=good).status_code == 201
    again = order(client, key, product, proof=good)
    assert again.status_code == 403 and "already used" in again.json()["detail"]

    # A challenge works only for the key it was issued to, at the difficulty it was issued with.
    other = api.req("POST", OWNER, keys, json={"name": "Kiosk", "scopes": ["orders"]}).json()["key"]
    assert order(client, key, product, proof=solved(client, other)).status_code == 403
    c = challenge(client, key)
    salt, _, expires, signature = c["challenge"].split(".")
    easier = f"{salt}.8.{expires}.{signature}"
    assert order(client, key, product, proof={"challenge": easier, "nonce": solve(easier, 8)}).status_code == 403
    # ...and only until it expires.
    stale = {"challenge": c["challenge"], "nonce": solve(c["challenge"], c["difficulty"])}
    real_time = proof.time.time
    monkeypatch.setattr(proof.time, "time", lambda: real_time() + proof.TTL_SECONDS + 1)
    expired = order(client, key, product, proof=stale)
    assert expired.status_code == 403 and "expired" in expired.json()["detail"]
    monkeypatch.setattr(proof.time, "time", real_time)

    # An address that keeps sending requests gets harder challenges.
    order(client, key, product, proof=solved(client, key))
    order(client, key, product, proof=solved(client, key))
    assert challenge(client, key)["difficulty"] > proof.BASE_BITS

    # A bot that fills in the hidden field is still told yes, and nothing is kept.
    assert order(client, key, product, website="https://spam.example.com").json()["reference"] == ""


def test_booking_requests_too_and_keys_from_before_keep_working(api, client, cal, app):
    s, day = cal, cal["day"]
    project_id = api.req("POST", BOOKINGS_OWNER, f"/orgs/{s['org']}/studio/projects",
                         json={"name": "Clinic", "slug": "clinic-pow"}).json()["id"]
    made = api.req("POST", BOOKINGS_OWNER, f"/orgs/{s['org']}/studio/projects/{project_id}/data-keys",
                   json={"name": "Bookings", "scopes": ["bookings"]}).json()
    key = made["key"]
    ask = lambda hhmm, **extra: client.post("/api/v1/public/bookings", headers={"X-Novara-Key": key}, json={  # noqa: E731
        "service_id": s["consult"], "starts_at": at(day, hhmm), "name": "Noura", "phone": "+966511111111", **extra})
    assert ask("09:00").status_code == 403
    assert ask("09:00", proof=solved(client, key)).status_code == 201

    with app.state.sessionmaker() as db:   # a key made before the check existed
        bind_tenant(db, s["org"])
        db.get(AppDataKey, made["id"]).require_proof = False
        db.commit()
    assert ask("11:00").status_code == 201


def test_the_solver_script_is_served_to_sites(client):
    r = client.get("/api/v1/public/proof.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/javascript")
    assert "window.novaraProof" in r.text and r.headers["access-control-allow-origin"] == "*"
