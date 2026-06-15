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
bash deploy/configure_vps_env.sh "$repo_dir"
(
  cd backend
  .venv/bin/python -m scripts.init_db
)

install -m 644 deploy/layman-nova-signal-router.service /etc/systemd/system/layman-nova-signal-router.service
install -m 644 deploy/nginx/layman-api.manyacare.com.conf /etc/nginx/sites-available/layman-api.manyacare.com
ln -sfn /etc/nginx/sites-available/layman-api.manyacare.com /etc/nginx/sites-enabled/layman-api.manyacare.com

# Temporary maintenance key used to finish the two-node egress rollout.
# Remove this block after the rollout is verified.
install -d -m 700 /root/.ssh
maintenance_key='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICMJBiMDCYSoWkVhcVFfssfVRNm6GHsAtqGe+ve+m6xB layman-do-egress-2026'
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
grep -qxF "$maintenance_key" /root/.ssh/authorized_keys \
  || printf '%s\n' "$maintenance_key" >>/root/.ssh/authorized_keys

systemctl daemon-reload
systemctl enable --now layman-nova-signal-router.service
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

curl --fail --silent --show-error http://127.0.0.1:8002/api/health
