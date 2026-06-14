#!/usr/bin/env bash
set -euo pipefail

repo_dir="${1:-/opt/layman-nova-signal-router}"
env_dir="/etc/layman"
env_file="$env_dir/layman.env"
runtime_root="/var/lib/layman"
backup_root="/var/backups/layman/postgres"
python_bin="$repo_dir/backend/.venv/bin/python"

if [[ "${EUID}" -ne 0 ]]; then
  echo "configure_vps_env.sh must run as root." >&2
  exit 1
fi
if [[ "$repo_dir" != "/opt/layman-nova-signal-router" ]]; then
  echo "Production repository path must be /opt/layman-nova-signal-router." >&2
  exit 1
fi
if [[ ! -x "$python_bin" ]]; then
  echo "Backend virtual environment is missing: $python_bin" >&2
  exit 1
fi

if ! id -u layman >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/layman --shell /usr/sbin/nologin --user-group layman
fi

install -o layman -g layman -m 700 -d \
  "$env_dir" \
  "$runtime_root" \
  "$runtime_root/state" \
  "$runtime_root/logs" \
  /var/log/layman \
  "$backup_root"

umask 077
if [[ ! -f "$env_file" ]]; then
  install -o layman -g layman -m 600 /dev/null "$env_file"
fi

set_env() {
  local key="$1"
  local value="$2"
  local temporary
  temporary="$(mktemp "$env_dir/layman.env.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { replaced = 0 }
    index($0, key "=") == 1 {
      if (!replaced) {
        print key "=" value
        replaced = 1
      }
      next
    }
    { print }
    END {
      if (!replaced) print key "=" value
    }
  ' "$env_file" > "$temporary"
  install -o layman -g layman -m 600 "$temporary" "$env_file"
  rm -f "$temporary"
}

ensure_env() {
  local key="$1"
  local value="$2"
  if ! grep -q "^${key}=" "$env_file"; then
    set_env "$key" "$value"
  fi
}

set_env APP_ENV production
set_env BACKEND_HOST 127.0.0.1
set_env BACKEND_PORT 8002
set_env BACKEND_PUBLIC_BASE_URL https://layman-api.manyacare.com
set_env FRONTEND_ORIGIN https://layman.manyacare.com
set_env DHAN_MODE REAL
set_env PAPER_MODE_ENABLED true
set_env ENABLE_LIVE_ORDERS false
set_env RELAY_ENABLED false
set_env RELAY_ALLOWED_STRATEGIES SUPERTREND_FLIP
set_env LIVE_PILOT_ENABLED false
set_env LIVE_PILOT_ALLOWED_STRATEGIES SUPERTREND_FLIP
set_env LIVE_ORDER_DRY_RUN_ONLY true
set_env ENABLE_LIVE_PILOT_WORKERS false
set_env EXECUTOR_SHARED_SECRETS_JSON '{}'
set_env EXECUTOR_REQUEST_TIMEOUT_SECONDS 10
set_env EXECUTOR_TIMESTAMP_TOLERANCE_SECONDS 60
set_env EXECUTOR_REAL_ORDERS_ENABLED false
set_env FREE_MAX_ACTIVE_PAPER_STRATEGIES 1
set_env UNIQUE_EGRESS_PER_USER_REQUIRED true
set_env EXECUTION_NODE_ROUTING_ENABLED false
set_env WEBHOOK_TRADING_ENABLED false
set_env WEBHOOK_HMAC_REQUIRED true
set_env WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS 60
set_env WEBHOOK_ALLOW_LEGACY_AUTH_LOCAL false
set_env REQUIRE_SIGNAL_ID_LIVE true
set_env WORKER_ROLE web
set_env ENABLE_PAPER_WORKERS false
set_env PAPER_WORKER_CONCURRENCY 1
set_env WORKER_JOB_LOCK_SECONDS 60
set_env WORKER_POLL_INTERVAL_SECONDS 2
set_env WORKER_HEARTBEAT_SECONDS 10
set_env WORKER_RETRY_BASE_SECONDS 2
set_env PAPER_QUEUE_INLINE_LOCAL false
set_env ENABLE_TRADING_WORKERS false
set_env TRADING_WORKER_DISTRIBUTED_LOCK_ENABLED false
set_env REQUIRE_MARKET_HOURS true
set_env REQUIRE_INSTRUMENT_MASTER_VALIDATION_LIVE true
set_env REQUIRE_FRESH_EXPIRY_LIVE true
set_env DEBUG_ENABLED false
set_env MARKET_CLOSED_DEBUG false
set_env FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED false
set_env RUNTIME_STATE_DIR "$runtime_root/state"
set_env RUNTIME_LOG_DIR "$runtime_root/logs"
set_env AUTH_REQUIRED true

ensure_env DATABASE_URL ""
ensure_env AUTH_DATABASE_URL ""
ensure_env ADMIN_EMAILS ""
ensure_env GOOGLE_CLIENT_ID ""
ensure_env GOOGLE_CLIENT_SECRET ""
ensure_env GOOGLE_REDIRECT_URI https://layman-api.manyacare.com/api/auth/google/callback
ensure_env RELAY_SHARED_SECRET ""

if ! grep -Eq '^SESSION_TOKEN_SECRET=.{32,}$' "$env_file"; then
  set_env SESSION_TOKEN_SECRET "$(openssl rand -hex 32)"
fi
if ! grep -Eq '^TOKEN_ENCRYPTION_KEY=.+$' "$env_file"; then
  set_env TOKEN_ENCRYPTION_KEY "$("$python_bin" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
fi

chown layman:layman "$env_file"
chmod 600 "$env_file"

if grep -Eq '^(DATABASE_URL|AUTH_DATABASE_URL)=$' "$env_file"; then
  echo "Configure a PostgreSQL DATABASE_URL in $env_file before starting the service." >&2
fi
if grep -Eq '^ADMIN_EMAILS=$' "$env_file"; then
  echo "Configure ADMIN_EMAILS in $env_file before starting the service." >&2
fi
