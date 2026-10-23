"""Account security: sign-in limits, email verification, password reset, two-factor sign-in, invitations."""

from __future__ import annotations

import re
import time
from datetime import timedelta

from sqlalchemy import select, update

from novara.db import utcnow
from novara.modules.identity import totp
from novara.modules.identity.models import LoginAttempt, User
from tests.conftest import PASSWORD

OWNER = "owner@sec.example.com"


def token_from(mail, path: str) -> str:
    """The token in the last email linking to ``path``."""
    msg = next(m for m in reversed(mail.sent) if path in m.text)
    return re.search(re.escape(path) + r"\?token=([\w-]+)", msg.text).group(1)


def login(client, email, password=PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def age_attempts(ops_sessionmaker, minutes: int) -> None:
    """The API's own database role can only add attempts, never change them, so this uses the operator's."""
    with ops_sessionmaker() as db:
        db.execute(update(LoginAttempt).values(created_at=utcnow() - timedelta(minutes=minutes)))
        db.commit()


# ---------------------------------------------------------------- sign-in limits


def test_failed_sign_ins_are_limited_per_email(api, client, ops_sessionmaker):
    api.signup(OWNER)
    for _ in range(5):
        assert login(client, OWNER, "wrong-password-1").status_code == 401
    r = login(client, OWNER, "wrong-password-1")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert login(client, OWNER).status_code == 429          # even the right password waits
    assert login(client, "other@sec.example.com", "x" * 10).status_code == 401  # other emails unaffected
    age_attempts(ops_sessionmaker, 16)
    assert login(client, OWNER).status_code == 200
    # A success starts the count again.
    for _ in range(4):
        login(client, OWNER, "wrong-password-1")
    assert login(client, OWNER).status_code == 200


def test_failed_sign_ins_are_limited_per_address(client):
    for i in range(30):
        assert login(client, f"guess{i}@sec.example.com", "wrong-password-1").status_code == 401
    assert login(client, "guess-new@sec.example.com", "wrong-password-1").status_code == 429


# ---------------------------------------------------------------- email verification and password reset


def test_email_verification(api, client, app):
    mail = app.state.mailer
    api.signup(OWNER)
    assert api.req("GET", OWNER, "/me").json()["email_verified"] is False
    token = token_from(mail, "/verify-email")
    assert client.post("/api/v1/auth/verify-email", json={"token": token}).json() == {"verified": True}
    assert api.req("GET", OWNER, "/me").json()["email_verified"] is True
    assert client.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 400   # single use
    assert client.post("/api/v1/auth/verify-email", json={"token": "x" * 40}).status_code == 400
    assert api.req("POST", OWNER, "/me/verify-email").json()["already_verified"] is True


def test_resending_verification_is_rate_limited(api, app):
    api.signup(OWNER)
    assert api.req("POST", OWNER, "/me/verify-email").status_code == 429   # registration just sent one


def test_password_reset(api, client, app):
    mail = app.state.mailer
    api.signup(OWNER)
    old = api.h(OWNER)
    before = len(mail.sent)
    assert client.post("/api/v1/auth/password-reset", json={"email": "nobody@sec.example.com"}).status_code == 202
    assert len(mail.sent) == before                                    # same answer, no email
    assert client.post("/api/v1/auth/password-reset", json={"email": OWNER}).status_code == 202
    token = token_from(mail, "/reset-password")
    assert client.post("/api/v1/auth/password-reset/confirm",
                       json={"token": token, "password": "short"}).status_code == 422
    r = client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "password": "a-new-password-42"})
    assert r.status_code == 200
    assert client.get("/api/v1/me", headers=old).status_code == 401    # every device signs in again
    assert login(client, OWNER).status_code == 401
    assert login(client, OWNER, "a-new-password-42").status_code == 200
    assert client.post("/api/v1/auth/password-reset/confirm",
                       json={"token": token, "password": "another-password-1"}).status_code == 400
    for _ in range(3):
        client.post("/api/v1/auth/password-reset", json={"email": OWNER})
    sent = len(mail.sent)
    client.post("/api/v1/auth/password-reset", json={"email": OWNER})
    assert len(mail.sent) == sent                                      # at most 3 an hour


def test_change_password_keeps_this_device_only(api, client):
    api.signup(OWNER)
    other = login(client, OWNER).json()["access_token"]
    assert api.req("POST", OWNER, "/me/password",
                   json={"current_password": "wrong", "new_password": "a-new-password-42"}).status_code == 403
    assert api.req("POST", OWNER, "/me/password",
                   json={"current_password": PASSWORD, "new_password": "a-new-password-42"}).status_code == 204
    assert api.req("GET", OWNER, "/me").status_code == 200
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {other}"}).status_code == 401


# ---------------------------------------------------------------- two-factor sign-in


def enable_mfa(api, email=OWNER) -> tuple[str, list[str]]:
    assert api.req("POST", email, "/me/mfa/setup", json={"password": "wrong"}).status_code == 403
    setup = api.req("POST", email, "/me/mfa/setup", json={"password": PASSWORD}).json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/NOVARA:")
    assert api.req("POST", email, "/me/mfa/enable", json={"code": "000000"}).status_code == 422
    r = api.req("POST", email, "/me/mfa/enable", json={"code": totp.code_at(setup["secret"], time.time())})
    assert r.status_code == 200, r.text
    return setup["secret"], r.json()["recovery_codes"]


def test_two_factor_sign_in(api, client, app):
    api.signup(OWNER)
    secret, codes = enable_mfa(api)
    assert len(codes) == 10 and api.req("GET", OWNER, "/me").json()["mfa_enabled"] is True
    with app.state.sessionmaker() as db:
        stored = db.scalar(select(User.mfa_secret).where(User.email == OWNER))
    assert stored.startswith("v1:") and secret not in stored        # encrypted at rest

    first = login(client, OWNER).json()
    assert first["mfa_required"] is True and first["access_token"] is None
    # The half-way token is not a session.
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {first['mfa_token']}"}).status_code == 401
    # The code used to switch it on can't be replayed; the next one works.
    now_code = totp.code_at(secret, time.time())
    assert client.post("/api/v1/auth/login/mfa", json={"mfa_token": first["mfa_token"], "code": now_code}).status_code == 401
    next_code = totp.code_at(secret, time.time() + totp.STEP_SECONDS)
    r = client.post("/api/v1/auth/login/mfa", json={"mfa_token": first["mfa_token"], "code": next_code})
    assert r.status_code == 200 and r.json()["access_token"]

    # A recovery code works once.
    second = login(client, OWNER).json()["mfa_token"]
    assert client.post("/api/v1/auth/login/mfa", json={"mfa_token": second, "code": codes[0].lower()}).status_code == 200
    third = login(client, OWNER).json()["mfa_token"]
    assert client.post("/api/v1/auth/login/mfa", json={"mfa_token": third, "code": codes[0]}).status_code == 401
    assert api.req("GET", OWNER, "/me/mfa/recovery-codes/remaining").json() == {"remaining": 9}
    assert client.post("/api/v1/auth/login/mfa", json={"mfa_token": "nonsense", "code": "123456"}).status_code == 401

    # Turning it off needs the password and a code.
    assert api.req("POST", OWNER, "/me/mfa/disable", json={"password": PASSWORD, "code": "000000"}).status_code == 403
    assert api.req("POST", OWNER, "/me/mfa/disable", json={"password": PASSWORD, "code": codes[1]}).status_code == 204
    assert login(client, OWNER).json()["access_token"]


def test_second_factor_guessing_is_limited(api, client):
    api.signup(OWNER)
    enable_mfa(api)
    mfa = login(client, OWNER).json()["mfa_token"]
    for _ in range(5):
        assert client.post("/api/v1/auth/login/mfa", json={"mfa_token": mfa, "code": "000000"}).status_code == 401
    assert client.post("/api/v1/auth/login/mfa", json={"mfa_token": mfa, "code": "000000"}).status_code == 429


# ---------------------------------------------------------------- invitations


def test_invite_someone_without_an_account(api, client, app):
    mail = app.state.mailer
    api.signup(OWNER)
    org = api.create_org(OWNER, "sec-co")
    r = api.req("POST", OWNER, f"/orgs/{org}/invitations", json={"email": "New.Person@sec.example.com", "role": "sales"})
    assert r.status_code == 201, r.text
    token = token_from(mail, "/invite")
    assert [i["email"] for i in api.req("GET", OWNER, f"/orgs/{org}/invitations").json()] == ["new.person@sec.example.com"]

    info = client.get(f"/api/v1/invitations/{token}").json()   # public, before signing in
    assert (info["organization_name"], info["role"], info["email"]) == ("Sec-Co", "sales", "new.person@sec.example.com")
    assert client.get("/api/v1/invitations/not-a-real-token-123").status_code == 404

    api.signup("someone.else@sec.example.com")
    assert api.req("POST", "someone.else@sec.example.com", f"/invitations/{token}/accept").status_code == 403
    api.signup("new.person@sec.example.com")
    r = api.req("POST", "new.person@sec.example.com", f"/invitations/{token}/accept")
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "sales" and "invoices.write" in r.json()["permissions"]
    assert api.req("GET", "new.person@sec.example.com", "/me").json()["email_verified"] is True
    assert api.req("POST", "new.person@sec.example.com", f"/invitations/{token}/accept").status_code == 404
    assert api.req("GET", OWNER, f"/orgs/{org}/invitations").json() == []
    assert api.req("POST", OWNER, f"/orgs/{org}/invitations",
                   json={"email": "new.person@sec.example.com", "role": "viewer"}).status_code == 409


def test_invitations_respect_permissions_and_can_be_revoked(api, client, app):
    mail = app.state.mailer
    api.signup(OWNER)
    api.signup("admin@sec.example.com")
    org = api.create_org(OWNER, "sec-co")
    api.add_member(OWNER, org, "admin@sec.example.com", "admin")
    # Admins can't hand out ownership (they don't hold every owner permission).
    assert api.req("POST", "admin@sec.example.com", f"/orgs/{org}/invitations",
                   json={"email": "x@sec.example.com", "role": "owner"}).status_code == 403
    inv = api.req("POST", OWNER, f"/orgs/{org}/invitations", json={"email": "x@sec.example.com", "role": "viewer"}).json()
    first = token_from(mail, "/invite")
    api.req("POST", OWNER, f"/orgs/{org}/invitations", json={"email": "x@sec.example.com", "role": "member"})
    second = token_from(mail, "/invite")
    assert client.get(f"/api/v1/invitations/{first}").status_code == 404      # replaced by the new link
    assert client.get(f"/api/v1/invitations/{second}").json()["role"] == "member"
    pending = api.req("GET", OWNER, f"/orgs/{org}/invitations").json()
    assert [p["role"] for p in pending] == ["member"] and pending[0]["id"] != inv["id"]
    assert api.req("DELETE", OWNER, f"/orgs/{org}/invitations/{pending[0]['id']}").status_code == 204
    assert client.get(f"/api/v1/invitations/{second}").status_code == 404
