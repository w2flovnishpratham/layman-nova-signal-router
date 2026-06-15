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

  ENV_FILE="$env_file" ENV_KEY="$key" ENV_VALUE="$value" "$python_bin" - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["ENV_FILE"])
key = os.environ["ENV_KEY"]
value = os.environ["ENV_VALUE"]
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
replacement = f"{key}={value}"
updated = False
result = []
for line in lines:
    if line.startswith(f"{key}="):
        if not updated:
            result.append(replacement)
            updated = True
        continue
    result.append(line)
if not updated:
    result.append(replacement)
path.write_text("\n".join(result) + "\n", encoding="utf-8")
PY
}

set_env APP_ENV production
set_env BACKEND_HOST 127.0.0.1
set_env BACKEND_PORT 8002
set_env BACKEND_PUBLIC_BASE_URL https://layman-api.manyacare.com
set_env FRONTEND_ORIGIN https://layman.manyacare.com
set_env FRONTEND_URL https://layman.manyacare.com
set_env DHAN_MODE REAL
set_env PAPER_MODE_ENABLED true
set_env REQUIRE_MARKET_HOURS true
set_env DEBUG_ENABLED false
set_env MARKET_CLOSED_DEBUG false
set_env FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED false
set_env AUTH_REQUIRED true
set_env GOOGLE_REDIRECT_URI https://layman-api.manyacare.com/api/auth/google/callback
set_env ADMIN_EMAILS ""
set_env SESSION_COOKIE_SECURE true
set_env SESSION_COOKIE_SAMESITE lax
set_env SESSION_COOKIE_DOMAIN ""
set_env STRATEGY_JOB_WORKER_ENABLED true

deploy_secrets_file="${LAYMAN_DEPLOY_SECRETS_FILE:-}"
if [[ -z "$deploy_secrets_file" || ! -f "$deploy_secrets_file" ]]; then
  echo "Production deployment secrets file is missing." >&2
  exit 1
fi

for required_key in DATABASE_URL GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET; do
  value="$(grep -m1 "^${required_key}=" "$deploy_secrets_file" | cut -d= -f2- || true)"
  if [[ -z "$value" ]]; then
    echo "Production deployment secret is missing: ${required_key}" >&2
    exit 1
  fi
  set_env "$required_key" "$value"
done

live_trading_armed="$(
  grep -m1 '^LIVE_TRADING_ARMED=' "$deploy_secrets_file" | cut -d= -f2- || true
)"
if [[ "$live_trading_armed" == "true" ]]; then
  set_env ENABLE_LIVE_ORDERS true
  set_env DHAN_READ_ONLY_REAL_DATA false
  set_env EXECUTION_NODE_ROUTING_ENABLED true
  set_env WEBHOOK_TRADING_ENABLED true
else
  set_env ENABLE_LIVE_ORDERS false
  set_env DHAN_READ_ONLY_REAL_DATA true
  set_env EXECUTION_NODE_ROUTING_ENABLED false
  set_env WEBHOOK_TRADING_ENABLED false
fi

if ! grep -Eq '^SESSION_TOKEN_SECRET=.{32,}$' "$env_file"; then
  set_env SESSION_TOKEN_SECRET "$(openssl rand -hex 32)"
fi

if ! grep -Eq '^APP_SECRET_KEY=.{32,}$' "$env_file"; then
  set_env APP_SECRET_KEY "$(openssl rand -hex 48)"
fi

if ! grep -Eq '^TOKEN_ENCRYPTION_KEY=.+$' "$env_file"; then
  set_env TOKEN_ENCRYPTION_KEY "$("$python_bin" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
fi

if ! grep -Eq '^CREDENTIAL_ENCRYPTION_KEY=.+$' "$env_file"; then
  token_encryption_key="$(grep -m1 '^TOKEN_ENCRYPTION_KEY=' "$env_file" | cut -d= -f2-)"
  set_env CREDENTIAL_ENCRYPTION_KEY "$token_encryption_key"
fi

chmod 600 "$env_file"
rm -f "$deploy_secrets_file"
