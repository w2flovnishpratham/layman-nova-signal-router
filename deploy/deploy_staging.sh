#!/usr/bin/env bash
set -Eeuo pipefail

read_env_from() {
  sed -n "s/^$2=//p" "$1" | tail -n 1
}

preflight_env() {
  local file="$1" state_dir log_dir
  [[ -r "$file" ]]
  [[ "$(read_env_from "$file" APP_ENV)" == "isolated_staging" ]]
  [[ "$(read_env_from "$file" ENABLE_LIVE_ORDERS | tr '[:upper:]' '[:lower:]')" == "false" ]]
  [[ "$(read_env_from "$file" DHAN_MODE | tr '[:upper:]' '[:lower:]')" == "mock" ]]
  [[ "$(read_env_from "$file" POSITION_DB_TYPED_WRITES_ENABLED | tr '[:upper:]' '[:lower:]')" == "true" ]]
  [[ "$(read_env_from "$file" POSITION_DB_READ_SHADOW_ENABLED | tr '[:upper:]' '[:lower:]')" == "true" ]]
  [[ "$(read_env_from "$file" POSITION_DB_READ_SHADOW_SAMPLE_RATE)" == "1.0" ]]
  [[ "$(read_env_from "$file" STAGING_DATABASE_GUARD)" == "isolated" ]]
  [[ -n "$(read_env_from "$file" DATABASE_URL)" ]]
  state_dir="$(readlink -m "$(read_env_from "$file" RUNTIME_STATE_DIR)")"
  log_dir="$(readlink -m "$(read_env_from "$file" RUNTIME_LOG_DIR)")"
  [[ "$state_dir" =~ ^/.*staging.*$ && "$log_dir" =~ ^/.*staging.*$ ]]
}

if [[ "${1:-}" == "--preflight" ]]; then
  [[ $# -eq 2 ]]
  preflight_env "$(readlink -m "$2")"
  exit 0
fi

[[ $# -eq 6 ]] || { echo "Expected SHA, deploy path, service, env file, health URL and repository." >&2; exit 2; }
IFS= read -r expected_database_url
target_sha="$1"
deploy_root="$(readlink -m "$2")"
service_name="$3"
env_file="$(readlink -m "$4")"
health_url="$5"
repository_slug="$6"

[[ "$target_sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$deploy_root" =~ ^/.*staging.*$ && "${deploy_root,,}" != *production* ]]
[[ "$service_name" =~ ^[A-Za-z0-9_.@-]*staging[A-Za-z0-9_.@-]*\.service$ && "${service_name,,}" != *production* ]]
[[ "$env_file" =~ ^/.*staging.*$ && "${env_file,,}" != *production* ]]
[[ "$health_url" =~ ^http://(127\.0\.0\.1|localhost):[0-9]{2,5}/api/health$ ]]
[[ "$repository_slug" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]
[[ -n "$expected_database_url" && -r "$env_file" ]]
preflight_env "$env_file"

read_env() {
  read_env_from "$env_file" "$1"
}

[[ "$(read_env DATABASE_URL)" == "$expected_database_url" ]]

runtime_state="$(readlink -m "$(read_env RUNTIME_STATE_DIR)")"
runtime_logs="$(readlink -m "$(read_env RUNTIME_LOG_DIR)")"
[[ "$runtime_state" =~ ^/.*staging.*$ && "$runtime_logs" =~ ^/.*staging.*$ ]]

for command in flock git pg_dump python3 curl sudo; do
  command -v "$command" >/dev/null || { echo "Missing staging dependency: $command" >&2; exit 1; }
done

mkdir -p "$deploy_root" "$deploy_root/bin" "$deploy_root/releases" "$deploy_root/backups/database" "$runtime_state" "$runtime_logs"
exec 9>"$deploy_root/deploy.lock"
flock -n 9 || { echo "Another staging deployment is running." >&2; exit 1; }
install -m 755 "$0" "$deploy_root/bin/deploy_staging.sh"

repository="$deploy_root/repository.git"
remote="git@github.com:$repository_slug.git"
if [[ ! -d "$repository" ]]; then
  git clone --mirror "$remote" "$repository"
fi
[[ "$(git -C "$repository" remote get-url origin)" == "$remote" ]]
git -C "$repository" fetch --prune --no-tags origin phase-2b2a-read-shadow:refs/heads/phase-2b2a-read-shadow
git -C "$repository" cat-file -e "$target_sha^{commit}"
[[ "$(git -C "$repository" rev-parse "$target_sha^{commit}")" == "$target_sha" ]]
git -C "$repository" merge-base --is-ancestor "$target_sha" refs/heads/phase-2b2a-read-shadow

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
previous_sha=""
if [[ -L "$deploy_root/current" ]]; then
  previous_sha="$(git -C "$deploy_root/current" rev-parse HEAD)"
fi
printf 'Previous staging SHA: %s\n' "${previous_sha:-none}"
tar -C "$runtime_state" -czf "$deploy_root/backups/runtime-$timestamp.tgz" .
PGDATABASE="$expected_database_url" pg_dump --format=custom --file="$deploy_root/backups/database/$timestamp.dump"

release="$deploy_root/releases/$target_sha"
if [[ ! -d "$release" ]]; then
  git -C "$repository" worktree add --detach "$release" "$target_sha"
fi
[[ "$(git -C "$release" rev-parse HEAD)" == "$target_sha" ]]
python3 -m venv "$release/backend/.venv"
"$release/backend/.venv/bin/pip" install -r "$release/backend/requirements.txt"

export DATABASE_URL="$expected_database_url" LAYMAN_ENV_FILE="$env_file"
current_revision="$(cd "$release/backend" && .venv/bin/python -m alembic current)"
heads="$(cd "$release/backend" && .venv/bin/python -m alembic heads)"
[[ "$heads" == 0010_position_ownership* ]]
printf 'Alembic before deployment: %s\n' "${current_revision:-unversioned}"
(cd "$release/backend" && .venv/bin/python -m alembic upgrade head)

unit="$(sudo -n systemctl cat "$service_name")"
[[ "$unit" == *"$deploy_root/current/backend"* ]]
[[ "$unit" == *"$env_file"* ]]
[[ "$unit" == *"$deploy_root/bin/deploy_staging.sh --preflight $env_file"* ]]
ln -sfn "$release" "$deploy_root/current.new"
mv -Tf "$deploy_root/current.new" "$deploy_root/current"

rollback() {
  [[ -n "$previous_sha" && -d "$deploy_root/releases/$previous_sha" ]] || return 0
  ln -sfn "$deploy_root/releases/$previous_sha" "$deploy_root/current.new"
  mv -Tf "$deploy_root/current.new" "$deploy_root/current"
  sudo -n systemctl restart "$service_name"
  curl --fail --silent --show-error --retry 15 --retry-delay 2 "$health_url" >/dev/null
  echo "Rolled staging back to $previous_sha."
}
trap rollback ERR
sudo -n systemctl restart "$service_name"
curl --fail --silent --show-error --retry 15 --retry-delay 2 "$health_url" >/dev/null

cd "$deploy_root/current/backend"
[[ "$(git rev-parse HEAD)" == "$target_sha" ]]
[[ "$(.venv/bin/python -m alembic current)" == 0010_position_ownership* ]]
[[ "$(.venv/bin/python -m alembic heads)" == 0010_position_ownership* ]]
.venv/bin/python -m scripts.position_read_shadow_pg_verify
.venv/bin/python -m scripts.position_shadow_parity
trap - ERR
printf 'Deployed staging SHA: %s\nPrevious staging SHA: %s\n' "$target_sha" "${previous_sha:-none}"
