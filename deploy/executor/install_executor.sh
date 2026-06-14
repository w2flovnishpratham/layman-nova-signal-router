#!/usr/bin/env bash
#
# Install the Layman NOVA executor service on a DigitalOcean droplet.
# Idempotent. Run as root on the executor droplet only.
#
# Usage:
#   sudo bash install_executor.sh [repo_dir]
#
# This installs ONLY the executor service (app.executor_service.main:app).
# It does NOT install the main Nova API, the frontend, paper/live workers,
# database migrations, or any admin UI.
set -euo pipefail

repo_dir="${1:-/opt/layman-executor}"
service_user="laymanexec"
env_dir="/etc/layman-executor"
runtime_root="/var/lib/layman-executor"
log_root="/var/log/layman-executor"
run_root="/run/layman-executor"
backend_dir="$repo_dir/backend"
venv_dir="$backend_dir/.venv"
python_bin="$venv_dir/bin/python"
service_src="$backend_dir/deploy/executor/layman-executor.service"
service_dst="/etc/systemd/system/layman-executor.service"

if [[ "${EUID}" -ne 0 ]]; then
  echo "install_executor.sh must run as root." >&2
  exit 1
fi
if [[ "$repo_dir" != "/opt/layman-executor" ]]; then
  echo "Executor repository path must be /opt/layman-executor." >&2
  exit 1
fi
if [[ ! -d "$backend_dir" ]]; then
  echo "Backend directory is missing: $backend_dir" >&2
  exit 1
fi

# Dedicated non-root system user.
if ! id -u "$service_user" >/dev/null 2>&1; then
  useradd --system --home-dir "$runtime_root" --shell /usr/sbin/nologin --user-group "$service_user"
fi

install -o "$service_user" -g "$service_user" -m 700 -d \
  "$env_dir" \
  "$runtime_root" \
  "$log_root" \
  "$run_root"

# Python virtual environment (executor needs only fastapi, uvicorn, httpx,
# sqlmodel, psycopg, pydantic-settings — installed from the backend requirements).
if [[ ! -x "$python_bin" ]]; then
  python3 -m venv "$venv_dir"
fi
"$venv_dir/bin/pip" install --upgrade pip >/dev/null
"$venv_dir/bin/pip" install -r "$backend_dir/requirements.txt" >/dev/null
chown -R "$service_user":"$service_user" "$venv_dir"

if [[ ! -f "$env_dir/executor.env" ]]; then
  umask 077
  install -o "$service_user" -g "$service_user" -m 600 \
    "$backend_dir/deploy/executor/executor.env.example" "$env_dir/executor.env"
  echo "Created $env_dir/executor.env from the example. Edit it before starting." >&2
  echo "Run: sudo bash $backend_dir/deploy/executor/configure_executor_env.sh" >&2
fi
chown "$service_user":"$service_user" "$env_dir/executor.env"
chmod 600 "$env_dir/executor.env"

install -m 644 "$service_src" "$service_dst"
systemctl daemon-reload
systemctl enable layman-executor.service

echo "Executor installed. Configure $env_dir/executor.env, then:" >&2
echo "  sudo systemctl restart layman-executor.service" >&2
echo "  bash $backend_dir/deploy/executor/check_executor_health.sh" >&2
echo "  bash $backend_dir/deploy/executor/check_reserved_ip_route.sh" >&2
