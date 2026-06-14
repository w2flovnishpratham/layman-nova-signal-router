#!/usr/bin/env bash
#
# Stage 6C: validate the Hostinger main app + workers.
# Checks: service active, /api/readiness ready (live disabled), paper worker and
# live-pilot worker active. Fails non-zero on any unsafe/unhealthy state.
#
# Usage: bash validate_hostinger_main.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/pilot/pilot_common.sh
source "$SCRIPT_DIR/pilot_common.sh"

echo "== Hostinger main validation =="

# 1. Main API service active.
if systemctl is-active --quiet "$HOSTINGER_SERVICE"; then
  pass "service ${HOSTINGER_SERVICE} is active"
else
  fail "service ${HOSTINGER_SERVICE} is not active"
fi

# 2. Paper worker active.
if systemctl is-active --quiet "$PAPER_WORKER_SERVICE"; then
  pass "paper worker ${PAPER_WORKER_SERVICE} is active"
else
  fail "paper worker ${PAPER_WORKER_SERVICE} is not active"
fi

# 3. Live-pilot worker active.
if systemctl is-active --quiet "$LIVE_WORKER_SERVICE"; then
  pass "live-pilot worker ${LIVE_WORKER_SERVICE} is active"
else
  fail "live-pilot worker ${LIVE_WORKER_SERVICE} is not active"
fi

# 4. Readiness endpoint reports ready (this also asserts live is disabled,
#    migrations current, vault ok, auth/debug/worker policy safe).
readiness_json="$(curl -fsS --max-time 10 "${HOSTINGER_BASE_URL}/api/readiness" || true)"
if [[ -z "$readiness_json" ]]; then
  fail "readiness endpoint did not respond"
else
  status="$(printf '%s' "$readiness_json" | sed -n 's/.*"status":"\([^"]*\)".*/\1/p')"
  live_policy="$(printf '%s' "$readiness_json" | sed -n 's/.*"live_policy":"\([^"]*\)".*/\1/p')"
  migrations="$(printf '%s' "$readiness_json" | sed -n 's/.*"migrations":"\([^"]*\)".*/\1/p')"
  if [[ "$status" == "ready" ]]; then
    pass "readiness status is ready"
  else
    fail "readiness status is ${status:-unknown}"
  fi
  if [[ "$live_policy" == "disabled" ]]; then
    pass "live_policy is disabled"
  else
    fail "live_policy is ${live_policy:-unknown} (expected disabled during dry-run validation)"
  fi
  if [[ "$migrations" == "ok" ]]; then
    pass "database migrations are current"
  else
    fail "database migrations are ${migrations:-unknown}"
  fi
fi

# 5. Dry-run safety flags.
assert_dry_run_safe_flags

finish
