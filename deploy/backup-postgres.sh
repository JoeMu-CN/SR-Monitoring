#!/usr/bin/env sh
set -eu
umask 077

: "${POSTGRES_CONTAINER:=supplierriskmonitoring-postgres-1}"
: "${BACKUP_DIR:=./backups}"

mkdir -p "$BACKUP_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_name="supplier-risk-${timestamp}.backup"
tmp_path="/tmp/${backup_name}"
target_path="${BACKUP_DIR%/}/${backup_name}"

cleanup() {
    docker exec "$POSTGRES_CONTAINER" rm -f "$tmp_path" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker exec "$POSTGRES_CONTAINER" sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "$1"' sh "$tmp_path"
docker cp "${POSTGRES_CONTAINER}:${tmp_path}" "$target_path"

printf '%s\n' "$target_path"
