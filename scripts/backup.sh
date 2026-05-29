#!/usr/bin/env bash
#
# Fichtelink database backup.
#
# Runs `pg_dump` inside the `db` container, writes a timestamped gzip dump to
# BACKUP_DIR, and prunes dumps older than KEEP_DAYS. Designed to be invoked
# from the host's crontab (see README / "Backups").
#
# IMPORTANT: a database dump alone is NOT a complete backup. The FERNET_KEY in
# .env is required to decrypt LIST_RECORD_VALUE rows — without it the dump is
# unrecoverable PII ciphertext. Back up .env (FERNET_KEY) off-host separately
# and never store it next to the dumps.
#
# Usage:
#   COMPOSE_DIR=/opt/fi-link BACKUP_DIR=/var/backups/fichtelink ./scripts/backup.sh
#
set -euo pipefail

COMPOSE_DIR="${COMPOSE_DIR:-/opt/fi-link}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/fichtelink}"
KEEP_DAYS="${KEEP_DAYS:-14}"
DB_USER="${DB_USER:-fichtelink}"
DB_NAME="${DB_NAME:-fichtelink}"

timestamp="$(date +%Y-%m-%dT%H-%M-%S)"
outfile="${BACKUP_DIR}/fichtelink-${timestamp}.sql.gz"

mkdir -p "$BACKUP_DIR"
cd "$COMPOSE_DIR"

# --clean --if-exists makes the dump self-restoring onto a populated database.
docker compose exec -T db pg_dump --clean --if-exists -U "$DB_USER" "$DB_NAME" \
    | gzip > "$outfile"

# Fail loudly if the dump came out empty (e.g. container down, auth error).
if [ ! -s "$outfile" ]; then
    echo "backup FAILED: ${outfile} is empty" >&2
    rm -f "$outfile"
    exit 1
fi

# Rotate: drop dumps older than KEEP_DAYS.
find "$BACKUP_DIR" -name 'fichtelink-*.sql.gz' -mtime "+${KEEP_DAYS}" -delete

echo "backup OK: ${outfile} ($(du -h "$outfile" | cut -f1))"
