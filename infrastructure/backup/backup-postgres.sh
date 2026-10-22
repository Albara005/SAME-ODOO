#!/usr/bin/env bash
# Take an encrypted logical backup of the NOVARA database and keep it outside production.
#
# Required:
#   NOVARA_BACKUP_DATABASE_URL   libpq URL of a role allowed to read everything (the schema owner)
#   NOVARA_BACKUP_DIR            where to write backups (mount of off-site / separate storage)
#   NOVARA_BACKUP_AGE_RECIPIENT  age public key; backups are encrypted to it (https://age-encryption.org)
# Optional:
#   NOVARA_BACKUP_ALLOW_UNENCRYPTED=1  only for local development
#   NOVARA_BACKUP_KEEP_DAYS            delete local backups older than this (default 30)
set -euo pipefail

: "${NOVARA_BACKUP_DATABASE_URL:?set NOVARA_BACKUP_DATABASE_URL}"
: "${NOVARA_BACKUP_DIR:?set NOVARA_BACKUP_DIR}"
keep_days="${NOVARA_BACKUP_KEEP_DAYS:-30}"

mkdir -p "$NOVARA_BACKUP_DIR"
umask 077
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
base="$NOVARA_BACKUP_DIR/novara-$stamp.dump"

if [[ -n "${NOVARA_BACKUP_AGE_RECIPIENT:-}" ]]; then
  out="$base.age"
  pg_dump --format=custom --no-owner --dbname="$NOVARA_BACKUP_DATABASE_URL" \
    | age --encrypt --recipient "$NOVARA_BACKUP_AGE_RECIPIENT" --output "$out.partial"
elif [[ "${NOVARA_BACKUP_ALLOW_UNENCRYPTED:-}" == "1" ]]; then
  out="$base"
  pg_dump --format=custom --no-owner --dbname="$NOVARA_BACKUP_DATABASE_URL" --file="$out.partial"
else
  echo "refusing to write an unencrypted backup: set NOVARA_BACKUP_AGE_RECIPIENT" >&2
  exit 2
fi

mv "$out.partial" "$out"
sha256sum "$out" > "$out.sha256"
echo "backup written: $out"

find "$NOVARA_BACKUP_DIR" -maxdepth 1 -name 'novara-*.dump*' -mtime +"$keep_days" -print -delete
