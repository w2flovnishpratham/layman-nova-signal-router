#!/usr/bin/env bash
#
# Safely set values in /etc/layman-executor/executor.env (mode 0600).
# Run as root on the executor droplet. Idempotent.
#
# Usage:
#   sudo bash configure_executor_env.sh EXECUTOR_001 64.225.87.19 <HOSTINGER_IP>
#
# The shared secret and DATABASE_URL are NOT set here; edit them by hand so they
# never land in shell history. This script refuses to enable real orders.
set -euo pipefail

executor_code="${1:?executor_code required, e.g. EXECUTOR_001}"
reserved_ip="${2:?reserved_ip required, e.g. 64.225.87.19}"
hostinger_ip="${3:?Hostinger public IP required}"
service_user="laymanexec"
env_dir="/etc/layman-executor"
env_file="$env_dir/executor.env"

if [[ "${EUID}" -ne 0 ]]; then
  echo "configure_executor_env.sh must run as root." >&2
  exit 1
fi

install -o "$service_user" -g "$service_user" -m 700 -d "$env_dir"
umask 077
if [[ ! -f "$env_file" ]]; then
  install -o "$service_user" -g "$service_user" -m 600 /dev/null "$env_file"
fi

set_env() {
  local key="$1"
  local value="$2"
  local temporary
  temporary="$(mktemp "$env_dir/executor.env.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { replaced = 0 }
    index($0, key "=") == 1 {
      if (!replaced) { print key "=" value; replaced = 1 }
      next
    }
    { print }
    END { if (!replaced) print key "=" value }
  ' "$env_file" > "$temporary"
  install -o "$service_user" -g "$service_user" -m 600 "$temporary" "$env_file"
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
set_env EXECUTOR_CODE "$executor_code"
set_env EXECUTOR_RESERVED_IP "$reserved_ip"
set_env EXECUTOR_ALLOWED_MAIN_HOSTINGER_IP "$hostinger_ip"
set_env EXECUTOR_BIND_HOST 127.0.0.1
set_env EXECUTOR_PORT 8010
set_env EXECUTOR_REQUEST_TIMEOUT_SECONDS 10
set_env EXECUTOR_TIMESTAMP_TOLERANCE_SECONDS 60
set_env EXECUTOR_EGRESS_CHECK_URL "https://api.ipify.org?format=json"
set_env DHAN_SEND_CLIENT_ID_HEADER true
# Real orders stay disabled. Enabling is a deliberate, manual pilot step.
set_env EXECUTOR_REAL_ORDERS_ENABLED false

ensure_env EXECUTOR_SHARED_SECRET REPLACE_WITH_32_PLUS_RANDOM_CHARS
ensure_env DATABASE_URL ""

chown "$service_user":"$service_user" "$env_file"
chmod 600 "$env_file"

if grep -q '^EXECUTOR_SHARED_SECRET=REPLACE' "$env_file"; then
  echo "Set a real EXECUTOR_SHARED_SECRET (openssl rand -hex 32) in $env_file." >&2
fi
if grep -Eq '^DATABASE_URL=$' "$env_file"; then
  echo "Set DATABASE_URL in $env_file for durable nonce replay protection." >&2
fi
echo "Executor env configured for $executor_code ($reserved_ip)."
