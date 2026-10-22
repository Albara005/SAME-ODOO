#!/usr/bin/env bash
# Prove a backup can actually be restored: restore it into a scratch database,
# run sanity checks, then drop the scratch database. Run this on a schedule;
# a backup that has never been restored is not a backup.
#
# Usage: verify-restore.sh <backup file>
# Required:
#   NOVARA_RESTORE_ADMIN_URL   libpq URL of a role that may CREATE/DROP DATABASE (connect to 'postgres')
# Optional:
#   NOVARA_BACKUP_AGE_IDENTITY  age private key file, for .age backups
set -euo pipefail

backup="${1:?usage: verify-restore.sh <backup file>}"
: "${NOVARA_RESTORE_ADMIN_URL:?set NOVARA_RESTORE_ADMIN_URL}"

if [[ -f "$backup.sha256" ]]; then
  sha256sum --check --status "$backup.sha256" || { echo "checksum mismatch: $backup" >&2; exit 1; }
fi

scratch="novara_restore_check_$(date -u +%s)_$$"
cleanup() { psql "$NOVARA_RESTORE_ADMIN_URL" -qc "DROP DATABASE IF EXISTS \"$scratch\"" >/dev/null; }
trap cleanup EXIT

psql "$NOVARA_RESTORE_ADMIN_URL" -qc "CREATE DATABASE \"$scratch\""
scratch_url="$(python3 -c 'import sys,urllib.parse as u; p=u.urlsplit(sys.argv[1]); print(u.urlunsplit(p._replace(path="/"+sys.argv[2])))' "$NOVARA_RESTORE_ADMIN_URL" "$scratch")"

if [[ "$backup" == *.age ]]; then
  : "${NOVARA_BACKUP_AGE_IDENTITY:?set NOVARA_BACKUP_AGE_IDENTITY to decrypt}"
  age --decrypt --identity "$NOVARA_BACKUP_AGE_IDENTITY" "$backup" \
    | pg_restore --no-owner --exit-on-error --dbname="$scratch_url"
else
  pg_restore --no-owner --exit-on-error --dbname="$scratch_url" "$backup"
fi

q() { psql "$scratch_url" -Atqc "$1"; }
latest="$(q "SELECT max(version) FROM schema_migrations")"
orgs="$(q "SELECT count(*) FROM organizations")"
users="$(q "SELECT count(*) FROM users")"
orphans="$(q "SELECT count(*) FROM memberships m LEFT JOIN organizations o ON o.id = m.organization_id WHERE o.id IS NULL")"
rls="$(q "SELECT count(*) FROM pg_class WHERE relname IN ('roles','memberships','audit_logs','files') AND relrowsecurity")"

echo "schema version: $latest | organizations: $orgs | users: $users"
[[ -n "$latest" ]] || { echo "FAIL: no schema_migrations" >&2; exit 1; }
[[ "$orphans" == "0" ]] || { echo "FAIL: $orphans memberships without organization" >&2; exit 1; }
[[ "$rls" == "4" ]] || { echo "FAIL: row-level security missing on restored tables" >&2; exit 1; }
echo "restore verified"
