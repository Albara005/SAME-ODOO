from __future__ import annotations

from sqlalchemy import select

from novara.modules.identity.models import User
from tests.conftest import PASSWORD


def test_register_login_me(api):
    api.signup("Sara@Example.com")
    r = api.c.get("/api/v1/me", headers=api.h("Sara@Example.com"))
    assert r.status_code == 200
    assert r.json()["email"] == "sara@example.com"
    assert r.json()["locale"] == "ar"


def test_password_is_never_stored_in_plain_text(api, app):
    api.signup("a@example.com")
    with app.state.sessionmaker() as db:
        user = db.scalar(select(User))
    assert PASSWORD not in user.password_hash
    assert user.password_hash.startswith("$argon2id$")


def test_duplicate_email_rejected(api):
    api.signup("a@example.com")
    r = api.c.post("/api/v1/auth/register", json={"email": "A@example.com", "password": PASSWORD})
    assert r.status_code == 409


def test_short_password_rejected(client):
    r = client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "short"})
    assert r.status_code == 422


def test_wrong_password_and_unknown_email_look_the_same(api):
    api.signup("a@example.com")
    wrong = api.c.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "nope-nope-nope"})
    unknown = api.c.post("/api/v1/auth/login", json={"email": "z@example.com", "password": "nope-nope-nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_requests_without_valid_token_are_rejected(client):
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_logout_revokes_the_token(api):
    api.signup("a@example.com")
    assert api.c.post("/api/v1/auth/logout", headers=api.h("a@example.com")).status_code == 204
    assert api.c.get("/api/v1/me", headers=api.h("a@example.com")).status_code == 401


def test_list_and_revoke_other_sessions(api):
    api.signup("a@example.com")
    first = api.tokens["a@example.com"]
    second = api.c.post("/api/v1/auth/login", json={"email": "a@example.com", "password": PASSWORD}).json()["access_token"]
    sessions = api.c.get("/api/v1/me/sessions", headers={"Authorization": f"Bearer {second}"}).json()
    assert len(sessions) == 2
    other = next(s for s in sessions if not s["current"])
    r = api.c.delete(f"/api/v1/me/sessions/{other['id']}", headers={"Authorization": f"Bearer {second}"})
    assert r.status_code == 204
    assert api.c.get("/api/v1/me", headers={"Authorization": f"Bearer {first}"}).status_code == 401
    assert api.c.get("/api/v1/me", headers={"Authorization": f"Bearer {second}"}).status_code == 200
