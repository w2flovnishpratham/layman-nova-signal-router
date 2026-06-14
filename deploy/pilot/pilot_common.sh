#!/usr/bin/env bash
#
# Shared constants and helpers for the Stage 6C pilot validation scripts.
# Source this from the other deploy/pilot/*.sh scripts.
#
# Rules: never print secrets; PASS/FAIL is explicit; non-zero exit on unsafe state.

# Expected executor -> Reserved IP mapping (the Dhan whitelisted IPs).
EXECUTOR_001_EXPECTED_IP="64.225.87.19"
EXECUTOR_002_EXPECTED_IP="152.42.157.165"

# Defaults (override via environment). These are non-secret service identifiers.
HOSTINGER_SERVICE="${HOSTINGER_SERVICE:-layman-nova-signal-router.service}"
PAPER_WORKER_SERVICE="${PAPER_WORKER_SERVICE:-layman-paper-worker.service}"
LIVE_WORKER_SERVICE="${LIVE_WORKER_SERVICE:-layman-live-worker.service}"
HOSTINGER_ENV_FILE="${HOSTINGER_ENV_FILE:-/etc/layman/layman.env}"
HOSTINGER_BASE_URL="${HOSTINGER_BASE_URL:-https://layman-api.manyacare.com}"

# Executor public base URLs (override per deployment).
EXECUTOR_001_URL="${EXECUTOR_001_URL:-}"
EXECUTOR_002_URL="${EXECUTOR_002_URL:-}"

_pilot_failures=0

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*" >&2; _pilot_failures=$((_pilot_failures + 1)); }
info() { echo "  - $*"; }

finish() {
  echo
  if [[ "$_pilot_failures" -ne 0 ]]; then
    echo "RESULT: FAIL (${_pilot_failures} check(s) failed)"
    exit 1
  fi
  echo "RESULT: PASS"
  exit 0
}

# Read a single non-secret flag value from the Hostinger env file without
# printing any other line. Returns empty if the file or key is absent.
read_env_flag() {
  local key="$1"
  local file="${2:-$HOSTINGER_ENV_FILE}"
  if [[ -f "$file" ]]; then
    sed -n "s/^${key}=//p" "$file" | head -n1 | tr -d '[:space:]'
  fi
}

# Compare an actual IP to an expected IP. Records PASS/FAIL via the counter and
# always returns 0 so `set -e` callers continue to the final summary.
assert_ip_match() {
  local label="$1" expected="$2" actual="$3"
  if [[ -z "$actual" ]]; then
    fail "${label}: could not determine actual egress IP"
    return 0
  fi
  if [[ "$actual" != "$expected" ]]; then
    fail "${label}: egress IP ${actual} != expected reserved IP ${expected}"
    return 0
  fi
  pass "${label}: egress IP matches reserved IP ${expected}"
  return 0
}

# Assert the dry-run safety posture: live disabled, dry-run-only true.
# Reads from the environment first, then the Hostinger env file.
assert_dry_run_safe_flags() {
  local live dry
  live="${ENABLE_LIVE_ORDERS:-$(read_env_flag ENABLE_LIVE_ORDERS)}"
  dry="${LIVE_ORDER_DRY_RUN_ONLY:-$(read_env_flag LIVE_ORDER_DRY_RUN_ONLY)}"
  if [[ "${live,,}" == "true" ]]; then
    fail "ENABLE_LIVE_ORDERS is true (must be false during dry-run validation)"
  else
    pass "ENABLE_LIVE_ORDERS is not enabled"
  fi
  if [[ "${dry,,}" != "true" ]]; then
    fail "LIVE_ORDER_DRY_RUN_ONLY is not true (dry-run safety required before real pilot)"
  else
    pass "LIVE_ORDER_DRY_RUN_ONLY is true"
  fi
}
