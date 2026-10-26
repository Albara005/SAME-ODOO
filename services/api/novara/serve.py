"""Run the whole platform in one container: the API, the built web app and the background worker.

For hosts that give one container and a PostgreSQL database (Railway, Render, Fly...):

    python -m novara.serve

With only an administrator's database address (``DATABASE_URL``, as Railway's PostgreSQL gives it, or
``NOVARA_ADMIN_DATABASE_URL``), it prepares the database before starting:

* creates the API's runtime role ``novara_app`` (``NOVARA_APP_DB_ROLE``; not owner, not superuser, NOBYPASSRLS:
  row-level security always applies to it), with a password derived from the encryption key and reset on every
  start;
* applies the migrations as the administrator, granting the runtime role what each one says;
* runs the API as that role (``NOVARA_DATABASE_URL`` is set to it).

``NOVARA_DATABASE_URL`` given explicitly skips all of that (the database is prepared some other way).

Secrets: set ``NOVARA_JWT_SECRET`` and ``NOVARA_ENCRYPTION_KEY`` (32+ characters each). When either is missing,
one is generated once and kept in the database, in a schema the runtime role can't read, so a first deployment
works without them; a warning says so on every start, since keeping a key next to what it protects is weaker.

The public address comes from ``NOVARA_APP_BASE_URL``, else Railway's ``RAILWAY_PUBLIC_DOMAIN``. The server
listens on ``PORT`` (default 8000) and trusts the forwarding headers of the host's proxy, the only thing that
reaches the container. The worker runs alongside and is restarted if it stops; when the server stops, so does
the container, and the host restarts it.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import signal
import subprocess
import sys
import time

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from novara.migrate import migrate

log = logging.getLogger("novara.serve")

ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")
SECRET_NAMES = ("NOVARA_JWT_SECRET", "NOVARA_ENCRYPTION_KEY")


def sqlalchemy_url(url: str) -> str:
    """postgres:// and postgresql:// (as hosts give them) with the psycopg driver SQLAlchemy needs."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def app_role(env: dict) -> str:
    role = env.get("NOVARA_APP_DB_ROLE", "novara_app")
    if not ROLE_NAME.fullmatch(role):
        raise SystemExit("NOVARA_APP_DB_ROLE must be a lower-case PostgreSQL name")
    return role


def app_password(encryption_key: str, role: str) -> str:
    """The runtime role's password: derived from the encryption key, so nothing else needs keeping."""
    return hmac.new(encryption_key.encode(), f"novara database role {role}".encode(), hashlib.sha256).hexdigest()


def runtime_url(admin_url: str, role: str, password: str) -> str:
    return make_url(admin_url).set(username=role, password=password).render_as_string(hide_password=False)


def ensure_secrets(admin_url: str, env: dict) -> list[str]:
    """Fill in missing secrets from the database, generating them the first time. Returns the names generated
    or loaded (empty when both were given)."""
    missing = [n for n in SECRET_NAMES if len(env.get(n, "")) < 32]
    if not missing:
        return []
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS novara_deploy"))
        conn.execute(text("REVOKE ALL ON SCHEMA novara_deploy FROM PUBLIC"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS novara_deploy.secrets (name text PRIMARY KEY, value text NOT NULL)"))
        for name in missing:
            conn.execute(text("INSERT INTO novara_deploy.secrets (name, value) VALUES (:n, :v) ON CONFLICT (name) DO NOTHING"),
                         {"n": name, "v": secrets.token_urlsafe(48)})
            env[name] = conn.scalar(text("SELECT value FROM novara_deploy.secrets WHERE name = :n"), {"n": name})
    engine.dispose()
    return missing


def ensure_app_role(admin_url: str, role: str, password: str) -> None:
    # Both go into SQL as written (roles and passwords can't be bound parameters): checked first.
    if not ROLE_NAME.fullmatch(role) or not re.fullmatch(r"[0-9a-f]{64}", password):
        raise ValueError("unexpected role name or password format")
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        exists = conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})
        verb = "ALTER" if exists else "CREATE"
        conn.execute(text(f"{verb} ROLE {role} WITH LOGIN PASSWORD '{password}' "
                          "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"))
        database = conn.scalar(text("SELECT current_database()")).replace('"', '""')
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{database}" TO {role}'))
    engine.dispose()


def prepare(env: dict) -> None:
    """Database, secrets and defaults, in ``env`` (os.environ when run)."""
    env.setdefault("NOVARA_ENV", "production")
    domain = env.get("RAILWAY_PUBLIC_DOMAIN", "")
    if domain and not env.get("NOVARA_APP_BASE_URL"):
        env["NOVARA_APP_BASE_URL"] = f"https://{domain}"
    if env.get("NOVARA_DATABASE_URL"):
        env["NOVARA_DATABASE_URL"] = sqlalchemy_url(env["NOVARA_DATABASE_URL"])
        if any(len(env.get(n, "")) < 32 for n in SECRET_NAMES):
            raise SystemExit("set NOVARA_JWT_SECRET and NOVARA_ENCRYPTION_KEY (32+ characters each)")
        return
    admin = env.get("NOVARA_ADMIN_DATABASE_URL") or env.get("DATABASE_URL")
    if not admin:
        raise SystemExit("no database: set DATABASE_URL (Railway: a reference to the PostgreSQL service's) "
                         "or NOVARA_DATABASE_URL")
    admin = sqlalchemy_url(admin)
    generated = ensure_secrets(admin, env)
    if generated:
        log.warning("%s kept in the database (novara_deploy.secrets); set them as variables for real use",
                    " and ".join(generated))
    role = app_role(env)
    password = app_password(env["NOVARA_ENCRYPTION_KEY"], role)
    ensure_app_role(admin, role, password)
    for version in migrate(admin, app_role=role):
        log.info("applied migration %s", version)
    env["NOVARA_DATABASE_URL"] = runtime_url(admin, role, password)


def server_command(env: dict) -> list[str]:
    return [sys.executable, "-m", "uvicorn", "novara.main:create_app", "--factory", "--host", "0.0.0.0",
            "--port", env.get("PORT", "8000"), "--proxy-headers", "--forwarded-allow-ips", "*",
            "--timeout-graceful-shutdown", "20"]


def run() -> int:
    env = os.environ
    prepare(env)
    server = subprocess.Popen(server_command(env))
    worker = subprocess.Popen([sys.executable, "-m", "novara.worker"])
    stopping = False

    def stop(signum, _frame):
        nonlocal stopping
        stopping = True
        for p in (server, worker):
            if p.poll() is None:
                p.send_signal(signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        code = server.poll()
        if code is not None:
            if worker.poll() is None:
                worker.terminate()
                worker.wait(timeout=20)
            return code
        if worker.poll() is not None and not stopping:
            log.error("the worker stopped (exit %s); starting it again", worker.returncode)
            time.sleep(5)
            worker = subprocess.Popen([sys.executable, "-m", "novara.worker"])
        time.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(run())
