"""The backup scripts end to end: an encrypted dump of a database with real business data
restores into a scratch database and passes the checks; unsafe or damaged backups are refused."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import PG_OWNER_URL, requires_pg

ADMIN_URL = os.environ.get("NOVARA_TEST_PG_ADMIN_URL")  # a role that may CREATE/DROP DATABASE
TOOLS = all(shutil.which(t) for t in ("age", "age-keygen", "pg_dump", "pg_restore", "psql"))
SCRIPTS = Path(__file__).resolve().parents[3] / "infrastructure" / "backup"

pytestmark = [requires_pg, pytest.mark.skipif(not (ADMIN_URL and TOOLS),
                                              reason="needs age, PostgreSQL client tools and NOVARA_TEST_PG_ADMIN_URL")]
OWNER = "owner@backup.example.com"
SECRET_NAME = "Backup Witness Trading"


def libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def run(script: str, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPTS / script), *args], env={**os.environ, **env},
                          capture_output=True, text=True, timeout=180)


def new_key(path: Path) -> str:
    out = subprocess.run(["age-keygen", "-o", str(path)], capture_output=True, text=True, check=True)
    return re.search(r"age1[0-9a-z]+", out.stderr + out.stdout).group(0)


@pytest.fixture
def business(api):
    api.signup(OWNER)
    org = api.create_org(OWNER, "backup-co")
    customer = api.req("POST", OWNER, f"/orgs/{org}/customers", json={"name": SECRET_NAME}).json()["id"]
    inv = api.req("POST", OWNER, f"/orgs/{org}/invoices", json={
        "customer_id": customer, "issue_date": "2026-03-01",
        "lines": [{"description": "Work", "quantity": "3", "unit_price": "250"}]}).json()
    assert api.req("POST", OWNER, f"/orgs/{org}/invoices/{inv['id']}/post").status_code == 200
    return org


def backup(tmp_path: Path, recipient: str) -> Path:
    r = run("backup-postgres.sh", NOVARA_BACKUP_DATABASE_URL=libpq(PG_OWNER_URL),
            NOVARA_BACKUP_DIR=str(tmp_path / "out"), NOVARA_BACKUP_AGE_RECIPIENT=recipient)
    assert r.returncode == 0, r.stderr
    return next((tmp_path / "out").glob("novara-*.dump.age"))


def test_encrypted_backup_restores_with_its_data(business, tmp_path):
    key = tmp_path / "key.txt"
    archive = backup(tmp_path, new_key(key))
    data = archive.read_bytes()
    assert data.startswith(b"age-encryption.org/v1")
    assert SECRET_NAME.encode() not in data                      # nothing readable without the key
    assert (archive.parent / (archive.name + ".sha256")).exists()

    v = run("verify-restore.sh", str(archive), NOVARA_RESTORE_ADMIN_URL=ADMIN_URL, NOVARA_BACKUP_AGE_IDENTITY=str(key))
    assert v.returncode == 0, v.stdout + v.stderr
    assert "invoices: 1" in v.stdout and "ledger balanced" in v.stdout and "restore verified" in v.stdout


def test_unencrypted_damaged_or_foreign_backups_are_refused(business, tmp_path):
    plain = run("backup-postgres.sh", NOVARA_BACKUP_DATABASE_URL=libpq(PG_OWNER_URL), NOVARA_BACKUP_DIR=str(tmp_path / "x"))
    assert plain.returncode == 2 and "refusing to write an unencrypted backup" in plain.stderr

    key = tmp_path / "key.txt"
    archive = backup(tmp_path, new_key(key))
    other = tmp_path / "other.txt"
    new_key(other)
    wrong = run("verify-restore.sh", str(archive), NOVARA_RESTORE_ADMIN_URL=ADMIN_URL, NOVARA_BACKUP_AGE_IDENTITY=str(other))
    assert wrong.returncode != 0                                 # another key can't read it

    with archive.open("ab") as f:
        f.write(b"x")
    damaged = run("verify-restore.sh", str(archive), NOVARA_RESTORE_ADMIN_URL=ADMIN_URL, NOVARA_BACKUP_AGE_IDENTITY=str(key))
    assert damaged.returncode == 1 and "checksum mismatch" in damaged.stderr
