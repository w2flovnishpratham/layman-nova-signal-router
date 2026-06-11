#!/usr/bin/env bash
set -euo pipefail

repo_dir="${1:-/root/layman-nova-signal-router}"
env_file="$repo_dir/backend/.env"
python_bin="$repo_dir/backend/.venv/bin/python"

umask 077
touch "$env_file"

set_env() {
  local key="$1"
  local value="$2"

  if grep -q "^${key}=" "$env_file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$env_file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$env_file"
  fi
}

ensure_env() {
  local key="$1"
  local value="$2"

  if ! grep -q "^${key}=" "$env_file"; then
    printf '%s=%s\n' "$key" "$value" >> "$env_file"
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
set_env UNIQUE_EGRESS_PER_USER_REQUIRED true
set_env EXECUTION_NODE_ROUTING_ENABLED false
set_env WEBHOOK_TRADING_ENABLED false
set_env WEBHOOK_HMAC_REQUIRED true
set_env REQUIRE_MARKET_HOURS true
set_env DEBUG_ENABLED false
set_env MARKET_CLOSED_DEBUG false
set_env FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED false

set_env AUTH_REQUIRED true
ensure_env DATABASE_URL ""
ensure_env AUTH_DATABASE_URL ""
ensure_env ADMIN_EMAILS w2f.lovnish@gmail.com
ensure_env GOOGLE_REDIRECT_URI https://layman-api.manyacare.com/api/auth/google/callback

if ! grep -Eq '^SESSION_TOKEN_SECRET=.{32,}$' "$env_file"; then
  set_env SESSION_TOKEN_SECRET "$(openssl rand -hex 32)"
fi

if ! grep -Eq '^TOKEN_ENCRYPTION_KEY=.+$' "$env_file"; then
  set_env TOKEN_ENCRYPTION_KEY "$("$python_bin" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
fi

chmod 600 "$env_file"
