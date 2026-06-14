#!/usr/bin/env bash
#
# Stage 6C: end-to-end dry-run signal validation.
# Confirms the safe posture, posts one SUPERTREND_FLIP relay alert, and verifies
# exactly two dry-run live-order jobs were created and NO real Dhan order placed.
#
# The relay token is read from RELAY_TOKEN and is never printed.
#
# Usage:
#   RELAY_TOKEN=*** bash validate_dry_run_signal.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/pilot/pilot_common.sh
source "$SCRIPT_DIR/pilot_common.sh"

BACKEND_DIR="${BACKEND_DIR:-/opt/layman-nova-signal-router/backend}"
PYTHON_BIN="${PYTHON_BIN:-$BACKEND_DIR/.venv/bin/python}"
STRATEGY_CODE="${STRATEGY_CODE:-SUPERTREND_FLIP}"
SINCE_SECONDS="${SINCE_SECONDS:-300}"

echo "== Dry-run signal validation =="

# 1. Safe posture: live disabled, dry-run-only true.
assert_dry_run_safe_flags

# 2. Post the relay alert (token never echoed).
if [[ -z "${RELAY_TOKEN:-}" ]]; then
  fail "RELAY_TOKEN is not set; cannot post the relay alert"
else
  signal_id="DRYRUN-$(date +%s)"
  http_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 \
    -X POST "${HOSTINGER_BASE_URL}/relay/tradingview/${STRATEGY_CODE}" \
    -H "X-Nova-Relay-Token: ${RELAY_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"signal_id\":\"${signal_id}\",\"action\":\"BUY\",\"symbol\":\"NIFTY\",\"option_type\":\"CE\",\"strike\":23000,\"expiry\":\"2026-06-18\",\"timeframe\":\"5m\",\"price\":100.5,\"source\":\"tradingview\"}" \
    || echo "000")"
  if [[ "$http_code" == "200" || "$http_code" == "202" ]]; then
    pass "relay accepted the dry-run alert (HTTP ${http_code})"
  else
    fail "relay rejected the dry-run alert (HTTP ${http_code})"
  fi
fi

# 3. Verify two dry-run jobs and no real order, via the app DB (no secrets printed).
if [[ -x "$PYTHON_BIN" ]]; then
  cd "$BACKEND_DIR"
  if SINCE_SECONDS="$SINCE_SECONDS" "$PYTHON_BIN" - <<'PY'
import os
import sys
from datetime import timedelta
from sqlmodel import select
from app.auth.db import session_scope
from app.auth.models import LiveOrderJob, utc_now_dt

since = utc_now_dt() - timedelta(seconds=int(os.environ.get("SINCE_SECONDS", "300")))
with session_scope() as session:
    jobs = list(session.exec(select(LiveOrderJob).where(LiveOrderJob.created_at >= since)).all())

dry = [j for j in jobs if j.dry_run]
real_placed = [j for j in jobs if j.status in ("sent", "confirmed")]
print(f"  - recent live_order_jobs={len(jobs)} dry_run={len(dry)} real_placed={len(real_placed)}")
if real_placed:
    print("  - a real order placement was detected during dry-run validation")
    sys.exit(3)
sys.exit(0 if len(dry) >= 2 else 4)
PY
  then
    pass "two dry-run jobs created and no real order placed"
  else
    rc=$?
    if [[ "$rc" -eq 3 ]]; then
      fail "a real order was placed during dry-run validation"
    else
      fail "did not observe two dry-run jobs (check fanout and approvals)"
    fi
  fi
else
  fail "backend python venv not found at ${PYTHON_BIN}; cannot confirm jobs"
fi

finish
