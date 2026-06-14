#!/usr/bin/env bash
set -euo pipefail

repo_dir="${LAYMAN_REPO_DIR:-/opt/layman-nova-signal-router}"
service_name="layman-nova-signal-router.service"
paper_worker_service="layman-paper-worker.service"
env_file="/etc/layman/layman.env"
health_url="http://127.0.0.1:8002/api/health"
readiness_url="http://127.0.0.1:8002/api/readiness"

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo -n bash "$0" "$@"
fi
if [[ "$repo_dir" != "/opt/layman-nova-signal-router" ]]; then
  echo "Production repository path must be /opt/layman-nova-signal-router." >&2
  exit 1
fi
if [[ ! -d "$repo_dir/.git" ]]; then
  echo "Production checkout is missing: $repo_dir" >&2
  exit 1
fi

cd "$repo_dir"
git_config=(git -c "safe.directory=$repo_dir")

if [[ -n "$("${git_config[@]}" status --porcelain --untracked-files=all)" ]]; then
  echo "Refusing deployment from a dirty production checkout." >&2
  exit 1
fi

python3 scripts/check_repo_hygiene.py --worktree
"${git_config[@]}" pull --ff-only origin main

if [[ -n "$("${git_config[@]}" status --porcelain --untracked-files=all)" ]]; then
  echo "Production checkout became dirty after pull." >&2
  exit 1
fi

python3 scripts/check_repo_hygiene.py --worktree
python3 scripts/check_deployment_hardening.py
bash -n deploy/deploy_vps.sh
bash -n deploy/configure_vps_env.sh
bash -n deploy/backup_postgres.sh

if [[ ! -d backend/.venv ]]; then
  python3 -m venv backend/.venv
fi
backend/.venv/bin/python -m pip install --upgrade pip
backend/.venv/bin/python -m pip install -r backend/requirements.txt

bash deploy/configure_vps_env.sh "$repo_dir"

chown -R root:layman "$repo_dir"
chmod -R g+rX,o-rwx "$repo_dir"

(
  cd backend
  runuser -u layman -- env LAYMAN_ENV_FILE="$env_file" .venv/bin/alembic upgrade head
  runuser -u layman -- env LAYMAN_ENV_FILE="$env_file" .venv/bin/alembic check
)

install -m 644 deploy/layman-nova-signal-router.service /etc/systemd/system/layman-nova-signal-router.service
install -m 644 deploy/layman-paper-worker.service /etc/systemd/system/layman-paper-worker.service
install -m 644 deploy/layman-postgres-backup.service /etc/systemd/system/layman-postgres-backup.service
install -m 644 deploy/layman-postgres-backup.timer /etc/systemd/system/layman-postgres-backup.timer
install -m 755 deploy/backup_postgres.sh /usr/local/sbin/layman-backup-postgres
install -m 644 deploy/logrotate/layman-nova /etc/logrotate.d/layman-nova
install -m 644 deploy/nginx/layman-api.manyacare.com.conf /etc/nginx/sites-available/layman-api.manyacare.com
ln -sfn /etc/nginx/sites-available/layman-api.manyacare.com /etc/nginx/sites-enabled/layman-api.manyacare.com

nginx -t
logrotate --debug /etc/logrotate.d/layman-nova >/dev/null

systemctl daemon-reload
systemctl enable "$service_name"
systemctl enable "$paper_worker_service"
systemctl enable --now layman-postgres-backup.timer
systemctl restart "$service_name"
systemctl restart "$paper_worker_service"
systemctl reload nginx

healthy=false
for _ in $(seq 1 30); do
  if curl --fail --silent "$health_url" >/dev/null \
    && curl --fail --silent "$readiness_url" >/dev/null; then
    healthy=true
    break
  fi
  sleep 1
done

if [[ "$healthy" != "true" ]]; then
  systemctl status "$service_name" --no-pager -l || true
  journalctl -u "$service_name" -n 100 --no-pager || true
  exit 1
fi
if ! systemctl is-active --quiet "$paper_worker_service"; then
  systemctl status "$paper_worker_service" --no-pager -l || true
  journalctl -u "$paper_worker_service" -n 100 --no-pager || true
  exit 1
fi

curl --fail --silent --show-error "$health_url"
printf '\n'
curl --fail --silent --show-error "$readiness_url"
printf '\n'
