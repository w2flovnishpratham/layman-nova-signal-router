#!/usr/bin/env bash
set -euo pipefail

repo_dir="${LAYMAN_REPO_DIR:-/root/layman-nova-signal-router}"
deploy_secrets_file="${LAYMAN_DEPLOY_SECRETS_FILE:-}"

if [[ "${SSH_ORIGINAL_COMMAND:-}" == "deploy-with-secrets-v1" ]]; then
  deploy_secrets_file="$(mktemp /root/layman-production-secrets.XXXXXX)"
  chmod 600 "$deploy_secrets_file"
  cat > "$deploy_secrets_file"
  export LAYMAN_DEPLOY_SECRETS_FILE="$deploy_secrets_file"
  trap 'rm -f "$deploy_secrets_file"' EXIT
fi

if [[ -z "$deploy_secrets_file" || ! -s "$deploy_secrets_file" ]]; then
  echo "Production deployment secrets were not provided." >&2
  exit 1
fi

cd "$repo_dir"

if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  git stash push --include-untracked -m "pre-deploy dirty worktree $(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi

git pull --ff-only origin main

backend/.venv/bin/pip install -r backend/requirements.txt
env_file="$(bash deploy/configure_vps_env.sh "$repo_dir" | tail -n1)"

# Migrations run DDL, which Neon's pooled endpoint (the "-pooler-" host used
# for normal app traffic) does not reliably support -- a fresh connection
# from the pool can come back with no search_path set, so CREATE TABLE fails
# with "no schema has been selected to create in" even though the table
# already exists and every other query works fine. Use the direct endpoint
# for this one step only; the running app keeps using the pooled URL.
migration_database_url="$(grep -m1 '^DATABASE_URL=' "$env_file" | cut -d= -f2- | sed 's/-pooler//')"
(
  cd backend
  LAYMAN_ENV_FILE="$env_file" DATABASE_URL="$migration_database_url" \
    .venv/bin/python -m alembic upgrade head
)

install -m 644 deploy/layman-nova-signal-router.service /etc/systemd/system/layman-nova-signal-router.service
install -m 644 deploy/layman-nova-signal-intake.service /etc/systemd/system/layman-nova-signal-intake.service
install -m 644 deploy/nginx/engine-api.novatradesolution.com.conf /etc/nginx/sites-available/engine-api.novatradesolution.com
install -m 755 deploy/configure_hostinger_firewall.sh /usr/local/sbin/layman-configure-hostinger-firewall
install -m 644 deploy/layman-premarket-healthcheck.service /etc/systemd/system/layman-premarket-healthcheck.service
install -m 644 deploy/layman-premarket-healthcheck.timer /etc/systemd/system/layman-premarket-healthcheck.timer
ln -sfn /etc/nginx/sites-available/engine-api.novatradesolution.com /etc/nginx/sites-enabled/engine-api.novatradesolution.com

# The intake worker must read the exact same env file the engine resolved --
# LAYMAN_ENV_FILE lives in a hand-managed drop-in on the engine unit, so mirror
# whatever configure_vps_env.sh just resolved rather than hardcoding a second
# copy of the path that could silently drift.
install -d -m 755 /etc/systemd/system/layman-nova-signal-intake.service.d
printf '[Service]\nEnvironment="LAYMAN_ENV_FILE=%s"\n' "$env_file" \
  > /etc/systemd/system/layman-nova-signal-intake.service.d/override.conf
chmod 644 /etc/systemd/system/layman-nova-signal-intake.service.d/override.conf

/usr/local/sbin/layman-configure-hostinger-firewall
systemctl daemon-reload
systemctl enable --now layman-premarket-healthcheck.timer
systemctl enable --now layman-nova-signal-router.service
systemctl restart layman-nova-signal-router.service
systemctl enable --now layman-nova-signal-intake.service
systemctl restart layman-nova-signal-intake.service

nginx -t
systemctl reload nginx

# Health-gate both processes. The engine is checked first and hard-fails the
# deploy: nothing executes without it. The intake worker is checked second and
# only warns, because nginx lists the engine as a backup upstream for the
# intake routes -- a dead intake worker degrades concurrency, it does not drop
# webhooks.
healthy=false
for _ in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:8002/api/health >/dev/null; then
    healthy=true
    break
  fi
  sleep 1
done

if [[ "$healthy" != "true" ]]; then
  systemctl status layman-nova-signal-router.service --no-pager -l
  journalctl -u layman-nova-signal-router.service -n 160 --no-pager -l
  exit 1
fi

intake_healthy=false
for _ in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:8003/api/health >/dev/null; then
    intake_healthy=true
    break
  fi
  sleep 1
done

if [[ "$intake_healthy" != "true" ]]; then
  echo "WARNING: webhook intake worker (:8003) did not become healthy." >&2
  echo "Webhook routes fall back to the engine worker via nginx; concurrency isolation is lost until this is fixed." >&2
  systemctl status layman-nova-signal-intake.service --no-pager -l || true
  journalctl -u layman-nova-signal-intake.service -n 80 --no-pager -l || true
fi

curl --fail --silent --show-error http://127.0.0.1:8002/api/health
