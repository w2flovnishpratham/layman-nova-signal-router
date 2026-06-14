#!/usr/bin/env bash
#
# Stage 6C: aggregate GO / NO-GO gate for the two-account dry-run pilot.
# Runs every validation and only prints GO when all pass. NO-GO is the default
# on any failure. This gate is for DRY-RUN readiness; live must stay disabled.
#
# A real order pilot still requires a separate, manual final approval after this
# gate is GO (see docs/FIRST_2_ACCOUNT_LIVE_PILOT_CHECKLIST.md).
#
# Pure-logic self test (no systemd/curl), used by automated checks:
#   PILOT_CHECK_ONLY=1 LIVE_ORDER_DRY_RUN_ONLY=true ENABLE_LIVE_ORDERS=false \
#     EXECUTOR_001_ACTUAL_IP=64.225.87.19 EXECUTOR_002_ACTUAL_IP=152.42.157.165 \
#     bash pilot_go_no_go_check.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/pilot/pilot_common.sh
source "$SCRIPT_DIR/pilot_common.sh"

echo "== Pilot GO / NO-GO check =="

# Always enforce the dry-run safety posture first. A real pilot must NOT be armed
# at the GO/NO-GO gate stage; dry-run must be on and live must be off.
assert_dry_run_safe_flags

if [[ "${PILOT_CHECK_ONLY:-0}" == "1" ]]; then
  # Pure logic only: flag safety (above) + executor IP match from provided values.
  assert_ip_match "EXECUTOR_001" "$EXECUTOR_001_EXPECTED_IP" "${EXECUTOR_001_ACTUAL_IP:-}"
  assert_ip_match "EXECUTOR_002" "$EXECUTOR_002_EXPECTED_IP" "${EXECUTOR_002_ACTUAL_IP:-}"
  echo
  if [[ "$_pilot_failures" -ne 0 ]]; then
    echo "GO/NO-GO: NO-GO (${_pilot_failures} failed). Do NOT enable real orders."
    exit 1
  fi
  echo "GO/NO-GO: GO for two-account DRY-RUN. Real order pilot still needs manual final approval."
  exit 0
fi

# Full orchestration: run each validation script; any failure -> NO-GO.
run_step() {
  local label="$1"; shift
  echo
  echo "--- ${label} ---"
  if "$@"; then
    pass "${label}"
  else
    fail "${label}"
  fi
}

run_step "Hostinger main" bash "$SCRIPT_DIR/validate_hostinger_main.sh"
run_step "Neon database" bash "$SCRIPT_DIR/validate_neon_database.sh"
run_step "Executors health + egress" bash "$SCRIPT_DIR/validate_executors.sh"
run_step "Dry-run signal" bash "$SCRIPT_DIR/validate_dry_run_signal.sh"

echo
if [[ "$_pilot_failures" -ne 0 ]]; then
  echo "GO/NO-GO: NO-GO (${_pilot_failures} failed). Do NOT enable real orders."
  exit 1
fi
echo "GO/NO-GO: GO for two-account DRY-RUN. Real order pilot still needs manual final approval."
exit 0
