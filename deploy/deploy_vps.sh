#!/usr/bin/env bash
set -euo pipefail

repo_dir="${LAYMAN_REPO_DIR:-/root/layman-nova-signal-router}"

cd "$repo_dir"

if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  git stash push --include-untracked -m "pre-deploy dirty worktree $(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi

git pull --ff-only origin main

backend/.venv/bin/pip install -r backend/requirements.txt
bash deploy/configure_vps_env.sh "$repo_dir"

install -m 644 deploy/layman-nova-signal-router.service /etc/systemd/system/layman-nova-signal-router.service
install -m 644 deploy/nginx/layman-api.manyacare.com.conf /etc/nginx/sites-available/layman-api.manyacare.com
ln -sfn /etc/nginx/sites-available/layman-api.manyacare.com /etc/nginx/sites-enabled/layman-api.manyacare.com

systemctl daemon-reload
systemctl enable layman-nova-signal-router.service
systemctl restart layman-nova-signal-router.service

nginx -t
systemctl reload nginx

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
  exit 1
fi

setup_status="$(curl --fail --silent http://127.0.0.1:8002/api/setup/status)"
engine_mode="$(
  printf '%s' "$setup_status" |
    backend/.venv/bin/python -c 'import json, sys; print(json.load(sys.stdin).get("engine_mode") or "")'
)"
webhook_secret_set="$(
  printf '%s' "$setup_status" |
    backend/.venv/bin/python -c 'import json, sys; print("true" if json.load(sys.stdin).get("webhook_secret_set") else "false")'
)"

if [[ -z "$engine_mode" ]]; then
  curl --fail --silent --show-error \
    -H "Content-Type: application/json" \
    --data '{"engine_mode":"paper","paper_starting_balance":100000}' \
    http://127.0.0.1:8002/api/setup/mode >/dev/null
fi

if [[ "$webhook_secret_set" != "true" ]]; then
  webhook_secret="$(openssl rand -hex 32)"
  curl --fail --silent --show-error \
    -H "Content-Type: application/json" \
    --data "{\"webhook_secret\":\"$webhook_secret\"}" \
    http://127.0.0.1:8002/api/setup/webhook-secret >/dev/null
  unset webhook_secret
fi

curl --fail --silent --show-error http://127.0.0.1:8002/api/health
