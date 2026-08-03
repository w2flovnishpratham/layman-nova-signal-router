#!/usr/bin/env bash
set -euo pipefail

repo_dir="${1:-/root/layman-nova-signal-router}"
python_bin="$repo_dir/backend/.venv/bin/python"

service_env_file=""
if command -v systemctl >/dev/null 2>&1; then
  service_environment="$(systemctl show layman-nova-signal-router.service -p Environment --value --no-pager 2>/dev/null || true)"
  for environment_entry in $service_environment; do
    case "$environment_entry" in
      LAYMAN_ENV_FILE=*)
        service_env_file="${environment_entry#LAYMAN_ENV_FILE=}"
        ;;
    esac
  done
fi

env_file="${LAYMAN_ENV_FILE:-${service_env_file:-$repo_dir/backend/.env}}"

umask 077
mkdir -p "$(dirname "$env_file")"
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

set_webhook_runtime_state() {
  local enabled="$1"

  RUNTIME_APP_STATE_FILE="$repo_dir/backend/runtime_state/app_state.json" RUNTIME_WEBHOOK_TRADING_ENABLED="$enabled" "$python_bin" - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["RUNTIME_APP_STATE_FILE"])
enabled = os.environ["RUNTIME_WEBHOOK_TRADING_ENABLED"].strip().lower() == "true"
path.parent.mkdir(parents=True, exist_ok=True)
try:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except json.JSONDecodeError:
    data = {}
data["engine_started"] = enabled
data["webhook_trading_enabled"] = enabled
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
tmp.replace(path)
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
set_env WEBHOOK_HMAC_REQUIRED true
set_env SESSION_COOKIE_SECURE true
set_env SESSION_COOKIE_SAMESITE lax
set_env SESSION_COOKIE_DOMAIN ""
set_env STRATEGY_JOB_WORKER_ENABLED true

deploy_secrets_file="${LAYMAN_DEPLOY_SECRETS_FILE:-}"
if [[ -z "$deploy_secrets_file" || ! -f "$deploy_secrets_file" ]]; then
  echo "Production deployment secrets file is missing." >&2
  exit 1
fi

for required_key in DATABASE_URL GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET STRATEGY_WEBHOOK_SECRET; do
  value="$(grep -m1 "^${required_key}=" "$deploy_secrets_file" | cut -d= -f2- || true)"
  if [[ -z "$value" ]]; then
    echo "Production deployment secret is missing: ${required_key}" >&2
    exit 1
  fi
  set_env "$required_key" "$value"
done

for optional_key in \
  PAYMENT_PROVIDER \
  RAZORPAY_KEY_ID \
  RAZORPAY_KEY_SECRET \
  RAZORPAY_WEBHOOK_SECRET \
  RAZORPAY_PLAN_PREMIUM_MONTHLY \
  RAZORPAY_PLAN_PAPER_PREMIUM \
  AWS_PROXY_SLOTS_ENABLED \
  AWS_PROXY_HOST \
  AWS_PROXY_SHARED_PASSWORD \
  AWS_PROXY_SLOT_1_PASSWORD \
  AWS_PROXY_SLOT_2_PASSWORD \
  AWS_PROXY_SLOT_3_PASSWORD \
  AWS_PROXY_SLOT_4_PASSWORD \
  AWS_PROXY_SLOT_5_PASSWORD \
  DHAN_SHARED_DATA_ENABLED \
  DHAN_SHARED_CLIENT_ID \
  DHAN_SHARED_PIN \
  DHAN_SHARED_TOTP_SECRET
do
  value="$(grep -m1 "^${optional_key}=" "$deploy_secrets_file" | cut -d= -f2- || true)"
  if [[ -n "$value" ]]; then
    set_env "$optional_key" "$value"
  fi
done

shared_data_enabled="$(grep -m1 '^DHAN_SHARED_DATA_ENABLED=' "$env_file" | cut -d= -f2- || true)"
shared_data_enabled_lc="$(printf '%s' "$shared_data_enabled" | tr '[:upper:]' '[:lower:]')"
if [[ "$shared_data_enabled_lc" == "true" || "$shared_data_enabled_lc" == "1" || "$shared_data_enabled_lc" == "yes" ]]; then
  for shared_required_key in DHAN_SHARED_CLIENT_ID DHAN_SHARED_PIN DHAN_SHARED_TOTP_SECRET; do
    value="$(grep -m1 "^${shared_required_key}=" "$env_file" | cut -d= -f2- || true)"
    if [[ -z "$value" ]]; then
      echo "Shared paper market-data setup is enabled but incomplete: ${shared_required_key}" >&2
      exit 1
    fi
  done
fi

live_trading_armed="$(
  grep -m1 '^LIVE_TRADING_ARMED=' "$deploy_secrets_file" | cut -d= -f2- || true
)"
case "$live_trading_armed" in
  true|armed)
    for live_required_key in \
      PAYMENT_PROVIDER \
      RAZORPAY_KEY_ID \
      RAZORPAY_KEY_SECRET \
      RAZORPAY_WEBHOOK_SECRET \
      RAZORPAY_PLAN_PREMIUM_MONTHLY \
      AWS_PROXY_SHARED_PASSWORD
    do
      value="$(grep -m1 "^${live_required_key}=" "$env_file" | cut -d= -f2- || true)"
      if [[ -z "$value" ]]; then
        echo "Live trading deployment is armed but incomplete: ${live_required_key}" >&2
        exit 1
      fi
    done
    payment_provider="$(grep -m1 '^PAYMENT_PROVIDER=' "$env_file" | cut -d= -f2- || true)"
    payment_provider_lc="$(printf '%s' "$payment_provider" | tr '[:upper:]' '[:lower:]')"
    if [[ "$payment_provider_lc" != "razorpay" ]]; then
      echo "Live trading deployment requires PAYMENT_PROVIDER=razorpay." >&2
      exit 1
    fi
    set_env AWS_PROXY_SLOTS_ENABLED true
    if ! grep -Eq '^AWS_PROXY_HOST=.+$' "$env_file"; then
      set_env AWS_PROXY_HOST 13.203.58.220
    fi
    set_env ENABLE_LIVE_ORDERS true
    set_env DHAN_READ_ONLY_REAL_DATA false
    set_env EXECUTION_NODE_ROUTING_ENABLED true
    set_env WEBHOOK_TRADING_ENABLED true
    set_webhook_runtime_state true
    ;;
  false|safe)
    set_env ENABLE_LIVE_ORDERS false
    set_env DHAN_READ_ONLY_REAL_DATA true
    set_env EXECUTION_NODE_ROUTING_ENABLED false
    set_env WEBHOOK_TRADING_ENABLED false
    set_webhook_runtime_state false
    ;;
  ""|preserve)
    if ! grep -Eq '^ENABLE_LIVE_ORDERS=' "$env_file"; then
      set_env ENABLE_LIVE_ORDERS false
      set_env DHAN_READ_ONLY_REAL_DATA true
      set_env EXECUTION_NODE_ROUTING_ENABLED false
      set_env WEBHOOK_TRADING_ENABLED false
      set_webhook_runtime_state false
    else
      echo "Preserving existing live trading gate values in $env_file." >&2
    fi
    ;;
  *)
    echo "Invalid LIVE_TRADING_ARMED value: $live_trading_armed" >&2
    exit 1
    ;;
esac

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

# Sole stdout line: the resolved env file path, so callers (deploy_vps.sh)
# can point LAYMAN_ENV_FILE at the same file this script just wrote instead
# of re-deriving the resolution logic (or silently falling back to
# backend/.env, which is a stale unrelated local-Postgres config on this box).
printf '%s\n' "$env_file"
