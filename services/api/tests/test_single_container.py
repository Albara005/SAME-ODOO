"""One container for the whole platform (Railway and the like): the API serves the built web app with the same
policy nginx would, and novara.serve prepares the database from an administrator's address alone: a runtime role
that row-level security applies to, the migrations, and secrets kept where that role can't read them."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from novara import serve
from novara.config import Settings
from novara.main import create_app


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>NOVARA</title><div id=root></div>")
    (d / "assets" / "index-3f2a.js").write_text("console.log('app')")
    (d / "sw.js").write_text("self.addEventListener('fetch', () => {})")
    (d / "manifest.webmanifest").write_text('{"name": "NOVARA"}')
    return d


@pytest.fixture
def web(tmp_path, dist):
    settings = Settings(env="test", database_url=f"sqlite:///{tmp_path / 'web.db'}",
                        jwt_secret="test-secret-that-is-long-enough-1234567890", storage_root=str(tmp_path / "s"),
                        web_dist=str(dist))
    app = create_app(settings, create_schema=True)
    with TestClient(app) as client:
        yield client
    app.state.engine.dispose()


def test_the_web_app_is_served_with_the_strict_policy(web):
    home = web.get("/")
    assert home.status_code == 200 and "NOVARA" in home.text
    assert "script-src 'self'" in home.headers["content-security-policy"]
    assert (home.headers["cache-control"], home.headers["x-content-type-options"]) == ("no-cache", "nosniff")
    # The app's own addresses (opened directly or reloaded) get the app.
    deep = web.get("/o/0b0e/invoices/new")
    assert deep.status_code == 200 and "NOVARA" in deep.text

    asset = web.get("/assets/index-3f2a.js")
    assert asset.status_code == 200 and asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "javascript" in asset.headers["content-type"]
    assert web.get("/assets/gone-1234.js").status_code == 404            # never index.html for a missing script
    assert web.get("/sw.js").headers["cache-control"] == "no-cache"
    assert web.get("/manifest.webmanifest").headers["content-type"].startswith("application/manifest+json")


def test_the_api_keeps_its_own_answers(web):
    assert web.get("/health").json() == {"status": "ok"}
    missing = web.get("/api/v1/no-such-thing")
    assert missing.status_code == 404 and missing.json() == {"detail": "Not Found"}
    assert web.get("/sites/unknown/").status_code == 404
    assert web.get("/openapi.json").json()["info"]["title"] == "NOVARA Platform API"
    assert web.post("/api/v1/auth/login", json={"email": "a@b.co", "password": "x" * 12}).status_code == 401


def test_nothing_outside_the_build_is_served(web, dist):
    (dist.parent / "secret.txt").write_text("not for the web")
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
        r = web.get(path)
        assert "not for the web" not in r.text


def test_database_addresses_as_hosts_give_them():
    assert serve.sqlalchemy_url("postgres://u:p@h:5432/railway") == "postgresql+psycopg://u:p@h:5432/railway"
    assert serve.sqlalchemy_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    url = serve.runtime_url("postgresql+psycopg://postgres:admin-pw@db.internal:5432/railway", "novara_app", "ab" * 32)
    parsed = make_url(url)
    assert (parsed.username, parsed.password, parsed.host, parsed.database) == ("novara_app", "ab" * 32, "db.internal", "railway")
    assert serve.app_password("k" * 40, "novara_app") != serve.app_password("j" * 40, "novara_app")
    with pytest.raises(ValueError):
        serve.ensure_app_role("postgresql+psycopg://x@h/d", "novara_app; DROP ROLE postgres", "ab" * 32)
    with pytest.raises(SystemExit, match="no database"):
        serve.prepare({})
    with pytest.raises(SystemExit, match="NOVARA_JWT_SECRET"):
        serve.prepare({"NOVARA_DATABASE_URL": "postgres://app@h/d"})


ADMIN_URL = os.environ.get("NOVARA_TEST_PG_ADMIN_URL")


@pytest.mark.skipif(not ADMIN_URL, reason="needs NOVARA_TEST_PG_ADMIN_URL (a PostgreSQL superuser)")
def test_an_empty_database_is_prepared_from_the_administrators_address():
    name = f"novara_serve_{uuid.uuid4().hex[:8]}"
    role = f"novara_app_{uuid.uuid4().hex[:8]}"
    root = create_engine(serve.sqlalchemy_url(ADMIN_URL), isolation_level="AUTOCOMMIT")
    with root.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin = make_url(serve.sqlalchemy_url(ADMIN_URL)).set(database=name).render_as_string(hide_password=False)
    try:
        env = {"DATABASE_URL": admin.replace("postgresql+psycopg://", "postgres://"), "NOVARA_APP_DB_ROLE": role,
               "RAILWAY_PUBLIC_DOMAIN": "novara-production.up.railway.app"}
        serve.prepare(env)
        assert env["NOVARA_ENV"] == "production"
        assert env["NOVARA_APP_BASE_URL"] == "https://novara-production.up.railway.app"
        assert len(env["NOVARA_JWT_SECRET"]) >= 32 and len(env["NOVARA_ENCRYPTION_KEY"]) >= 32
        runtime = make_url(env["NOVARA_DATABASE_URL"])
        assert runtime.username == role and runtime.database == name

        # A second start finds the same secrets and nothing left to migrate.
        again = {"DATABASE_URL": env["DATABASE_URL"], "NOVARA_APP_DB_ROLE": role}
        serve.prepare(again)
        assert (again["NOVARA_JWT_SECRET"], again["NOVARA_ENCRYPTION_KEY"]) == (env["NOVARA_JWT_SECRET"], env["NOVARA_ENCRYPTION_KEY"])

        app_engine = create_engine(env["NOVARA_DATABASE_URL"])
        with app_engine.connect() as conn:
            attrs = conn.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
            assert tuple(attrs) == (False, False)                     # row-level security applies to it
            assert conn.scalar(text("SELECT count(*) FROM organizations")) == 0
            with pytest.raises(Exception, match="permission denied"):
                conn.execute(text("SELECT value FROM novara_deploy.secrets"))
        app_engine.dispose()

        # The API starts on it and answers.
        settings = Settings(env="production", database_url=env["NOVARA_DATABASE_URL"],
                            jwt_secret=env["NOVARA_JWT_SECRET"], encryption_key=env["NOVARA_ENCRYPTION_KEY"])
        api = create_app(settings)
        with TestClient(api) as client:
            assert client.get("/health").json() == {"status": "ok"}
            assert client.post("/api/v1/auth/register", json={"email": "owner@serve.example.com",
                                                               "password": "a-long-password-123",
                                                               "full_name": "Owner"}).status_code == 201
        api.state.engine.dispose()
    finally:
        with root.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
        root.dispose()
