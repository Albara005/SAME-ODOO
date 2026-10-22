"""Database-level isolation: PostgreSQL row-level security holds even when the
ORM filter is bypassed. Runs only when a PostgreSQL test database is configured."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from novara.tenancy import bind_tenant, system_scope
from tests.conftest import requires_pg

pytestmark = requires_pg


@pytest.fixture
def two_orgs(api):
    api.signup("a@example.com")
    api.signup("b@example.com")
    a = api.create_org("a@example.com", "org-a")
    b = api.create_org("b@example.com", "org-b")
    api.req("POST", "a@example.com", f"/orgs/{a}/files", params={"filename": "a.txt"}, content=b"a")
    api.req("POST", "b@example.com", f"/orgs/{b}/files", params={"filename": "b.txt"}, content=b"b")
    return a, b


def test_raw_sql_sees_only_bound_organization(app, two_orgs):
    a, _ = two_orgs
    with app.state.sessionmaker() as db:
        bind_tenant(db, a)
        with system_scope(db):  # turn the ORM filter off; only RLS remains
            rows = db.execute(text("SELECT DISTINCT organization_id FROM files")).scalars().all()
            roles = db.execute(text("SELECT DISTINCT organization_id FROM roles")).scalars().all()
    assert rows == [a]
    assert roles == [a]


def test_raw_sql_without_organization_sees_nothing(app, two_orgs):
    with app.state.sessionmaker() as db, system_scope(db):
        assert db.execute(text("SELECT count(*) FROM files")).scalar() == 0
        assert db.execute(text("SELECT count(*) FROM audit_logs")).scalar() == 0


def test_raw_insert_into_other_organization_is_refused(app, two_orgs):
    a, b = two_orgs
    with app.state.sessionmaker() as db:
        bind_tenant(db, a)
        with system_scope(db), pytest.raises(ProgrammingError, match="row-level security"):
            db.execute(text(
                "INSERT INTO roles (id, organization_id, key, name, permissions, is_system, created_at) "
                "VALUES ('x', :b, 'evil', 'evil', '[]', false, now())"
            ), {"b": b})


def test_audit_log_cannot_be_updated_or_deleted_by_app_role(app, two_orgs):
    a, _ = two_orgs
    for stmt in ("UPDATE audit_logs SET action = 'x'", "DELETE FROM audit_logs"):
        with app.state.sessionmaker() as db:
            bind_tenant(db, a)
            with pytest.raises(ProgrammingError, match="permission denied"):
                db.execute(text(stmt))


def test_app_versions_are_immutable_for_the_api_role(api, app):
    api.signup("dev@pg.example.com")
    org = api.create_org("dev@pg.example.com", "pg-studio")
    pid = api.req("POST", "dev@pg.example.com", f"/orgs/{org}/studio/projects",
                  json={"name": "Site", "slug": "pg-site"}).json()["id"]
    api.req("POST", "dev@pg.example.com", f"/orgs/{org}/studio/projects/{pid}/versions", json={"message": "v1"})
    for stmt in ("UPDATE app_versions SET checks_passed = true", "DELETE FROM app_versions"):
        with app.state.sessionmaker() as db:
            bind_tenant(db, org)
            with system_scope(db), pytest.raises(ProgrammingError, match="permission denied"):
                db.execute(text(stmt))
