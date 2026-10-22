"""Test fixtures.

By default tests run on a throwaway SQLite file. To run the same suite against
PostgreSQL with row-level security active, set both:

    NOVARA_TEST_PG_OWNER_URL  schema owner, used to migrate and reset tables
    NOVARA_TEST_PG_APP_URL    non-owner runtime role the API connects as
    NOVARA_TEST_PG_APP_ROLE   name of that runtime role (default: novara_app)
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from novara.config import Settings
from novara.main import create_app
from novara.migrate import migrate
from novara.modules.files.storage import LocalObjectStorage

PASSWORD = "correct-horse-battery"

PG_OWNER_URL = os.environ.get("NOVARA_TEST_PG_OWNER_URL")
PG_APP_URL = os.environ.get("NOVARA_TEST_PG_APP_URL")
PG_APP_ROLE = os.environ.get("NOVARA_TEST_PG_APP_ROLE", "novara_app")
USING_PG = bool(PG_OWNER_URL and PG_APP_URL)

requires_pg = pytest.mark.skipif(not USING_PG, reason="PostgreSQL test database not configured")


@pytest.fixture(scope="session", autouse=True)
def fast_password_hashing():
    """Argon2 is deliberately slow; tests use minimal cost. Production keeps the library defaults."""
    from argon2 import PasswordHasher

    from novara import security

    original = security._hasher
    security._hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
    yield
    security._hasher = original


@pytest.fixture(scope="session")
def pg_owner_engine():
    if not USING_PG:
        yield None
        return
    engine = create_engine(PG_OWNER_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    migrate(PG_OWNER_URL, app_role=PG_APP_ROLE)
    yield engine
    engine.dispose()


@pytest.fixture
def database_url(tmp_path, pg_owner_engine):
    if not USING_PG:
        return f"sqlite:///{tmp_path / 'test.db'}", True
    with pg_owner_engine.begin() as conn:
        tables = conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'schema_migrations'"
        )).scalars().all()
        conn.execute(text(f"TRUNCATE {', '.join(tables)} CASCADE"))
    return PG_APP_URL, False


@pytest.fixture
def app(tmp_path, database_url):
    url, create_schema = database_url
    settings = Settings(
        env="test",
        database_url=url,
        jwt_secret="test-secret-that-is-long-enough-1234567890",
        storage_root=str(tmp_path / "storage"),
        max_upload_bytes=1024,
    )
    app = create_app(settings, storage=LocalObjectStorage(settings.storage_root), create_schema=create_schema)
    yield app
    app.state.engine.dispose()


@pytest.fixture
def client(app):
    return TestClient(app)


class Api:
    """Small helper that remembers each user's token."""

    def __init__(self, client: TestClient):
        self.c = client
        self.tokens: dict[str, str] = {}

    def signup(self, email: str, password: str = PASSWORD) -> str:
        r = self.c.post("/api/v1/auth/register", json={"email": email, "password": password, "full_name": email})
        assert r.status_code == 201, r.text
        r = self.c.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        self.tokens[email] = r.json()["access_token"]
        return self.tokens[email]

    def h(self, email: str) -> dict:
        return {"Authorization": f"Bearer {self.tokens[email]}"}

    def req(self, method: str, email: str, path: str, **kw):
        return self.c.request(method, f"/api/v1{path}", headers={**self.h(email), **kw.pop("headers", {})}, **kw)

    def create_org(self, email: str, slug: str) -> str:
        r = self.req("POST", email, "/orgs", json={"name": slug.title(), "slug": slug})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def add_member(self, owner: str, org: str, email: str, role: str):
        return self.req("POST", owner, f"/orgs/{org}/members", json={"email": email, "role": role})


@pytest.fixture
def api(client):
    return Api(client)
