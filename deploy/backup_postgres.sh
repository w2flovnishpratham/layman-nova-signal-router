#!/usr/bin/env bash
set -euo pipefail

umask 077

env_file="${LAYMAN_ENV_FILE:-/etc/layman/layman.env}"
backup_dir="${LAYMAN_BACKUP_DIR:-/var/backups/layman/postgres}"
retention_count="${LAYMAN_BACKUP_RETENTION_COUNT:-14}"

if ! [[ "$retention_count" =~ ^[1-9][0-9]*$ ]]; then
  echo "LAYMAN_BACKUP_RETENTION_COUNT must be a positive integer." >&2
  exit 1
fi

database_url="${DATABASE_URL:-}"
if [[ -z "$database_url" ]]; then
  if [[ ! -r "$env_file" ]]; then
    echo "Database configuration is unavailable." >&2
    exit 1
  fi
  database_url="$(awk -F= '$1 == "DATABASE_URL" { sub(/^[^=]*=/, ""); print; exit }' "$env_file")"
fi
if [[ -z "$database_url" ]]; then
  echo "DATABASE_URL is not configured." >&2
  exit 1
fi
if ! command -v pg_dump >/dev/null 2>&1; then
  echo "pg_dump is not installed." >&2
  exit 1
fi

install -m 700 -d "$backup_dir"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
final_path="$backup_dir/layman-postgres-$timestamp.dump"
temporary_path="$final_path.tmp"

cleanup() {
  rm -f "$temporary_path"
}
trap cleanup EXIT

pg_dump --dbname="$database_url" --format=custom --file="$temporary_path"
chmod 600 "$temporary_path"
mv "$temporary_path" "$final_path"
sha256sum "$final_path" > "$final_path.sha256"
chmod 600 "$final_path.sha256"

mapfile -t old_backups < <(
  find "$backup_dir" -maxdepth 1 -type f -name 'layman-postgres-*.dump' -printf '%T@ %p\n' \
    | sort -rn \
    | awk -v keep="$retention_count" 'NR > keep { sub(/^[^ ]+ /, ""); print }'
)
for backup in "${old_backups[@]}"; do
  rm -f -- "$backup" "$backup.sha256"
done

printf 'PostgreSQL backup completed: %s\n' "$(basename "$final_path")"
