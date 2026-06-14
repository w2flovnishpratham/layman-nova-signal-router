#!/usr/bin/env bash
#
# Stage 6C: EMERGENCY — disable live trading everywhere.
# Sets the Hostinger live flags back to the safe defaults, restarts the API and
# live-pilot worker, and disables real orders on both executors (over SSH if SSH
# targets are provided, otherwise prints the exact commands to run on each droplet).
#
# Safe defaults enforced:
#   Hostinger:  ENABLE_LIVE_ORDERS=false  LIVE_ORDER_DRY_RUN_ONLY=true
#   Executors:  EXECUTOR_REAL_ORDERS_ENABLED=false
#
# Usage (on Hostinger, as root):
#   sudo bash disable_live_everywhere.sh
# Optional remote executor disable:
#   EXECUTOR_001_SSH=root@64.225.87.19 EXECUTOR_002_SSH=root@152.42.157.165 \
#     sudo bash disable_live_everywhere.sh
set -euo pipefail

HOSTINGER_ENV_FILE="${HOSTINGER_ENV_FILE:-/etc/layman/layman.env}"
HOSTINGER_SERVICE="${HOSTINGER_SERVICE:-layman-nova-signal-router.service}"
LIVE_WORKER_SERVICE="${LIVE_WORKER_SERVICE:-layman-live-worker.service}"
EXECUTOR_ENV_FILE="${EXECUTOR_ENV_FILE:-/etc/layman-executor/executor.env}"
EXECUTOR_SERVICE="${EXECUTOR_SERVICE:-layman-executor.service}"

set_env() {
  local key="$1" value="$2" file="$3" owner="$4"
  local tmp
  tmp="$(mktemp "${file}.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { replaced = 0 }
    index($0, key "=") == 1 { if (!replaced) { print key "=" value; replaced = 1 } ; next }
    { print }
    END { if (!replaced) print key "=" value }
  ' "$file" > "$tmp"
  install -o "$owner" -g "$owner" -m 600 "$tmp" "$file"
  rm -f "$tmp"
}

echo "== Disabling live everywhere =="

# 1. Hostinger flags -> safe defaults.
if [[ -f "$HOSTINGER_ENV_FILE" ]]; then
  set_env ENABLE_LIVE_ORDERS false "$HOSTINGER_ENV_FILE" layman
  set_env LIVE_ORDER_DRY_RUN_ONLY true "$HOSTINGER_ENV_FILE" layman
  echo "Hostinger: ENABLE_LIVE_ORDERS=false, LIVE_ORDER_DRY_RUN_ONLY=true"
  systemctl restart "$HOSTINGER_SERVICE" || echo "WARN: could not restart ${HOSTINGER_SERVICE}" >&2
  systemctl restart "$LIVE_WORKER_SERVICE" 2>/dev/null || echo "WARN: could not restart ${LIVE_WORKER_SERVICE}" >&2
else
  echo "WARN: Hostinger env file ${HOSTINGER_ENV_FILE} not found on this host." >&2
fi

# 2. Executors -> real orders disabled.
disable_remote() {
  local target="$1"
  [[ -z "$target" ]] && return 0
  echo "Disabling real orders on ${target} ..."
  ssh -o BatchMode=yes -o ConnectTimeout=15 "$target" \
    "sudo sed -i 's/^EXECUTOR_REAL_ORDERS_ENABLED=.*/EXECUTOR_REAL_ORDERS_ENABLED=false/' ${EXECUTOR_ENV_FILE} && sudo systemctl restart ${EXECUTOR_SERVICE}" \
    || echo "WARN: remote disable failed for ${target}; run the manual command below." >&2
}

disable_remote "${EXECUTOR_001_SSH:-}"
disable_remote "${EXECUTOR_002_SSH:-}"

if [[ -z "${EXECUTOR_001_SSH:-}${EXECUTOR_002_SSH:-}" ]]; then
  echo
  echo "Run on EACH executor droplet to disable real orders:"
  echo "  sudo sed -i 's/^EXECUTOR_REAL_ORDERS_ENABLED=.*/EXECUTOR_REAL_ORDERS_ENABLED=false/' ${EXECUTOR_ENV_FILE}"
  echo "  sudo systemctl restart ${EXECUTOR_SERVICE}"
fi

echo
echo "DONE: live disabled on Hostinger. Verify with deploy/pilot/validate_hostinger_main.sh"
