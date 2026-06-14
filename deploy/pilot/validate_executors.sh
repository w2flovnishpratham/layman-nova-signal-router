#!/usr/bin/env bash
#
# Stage 6C: validate both executor droplets — health AND egress IP.
# Fails non-zero unless:
#   EXECUTOR_001 /health code == EXECUTOR_001 and /egress-ip == 64.225.87.19
#   EXECUTOR_002 /health code == EXECUTOR_002 and /egress-ip == 152.42.157.165
#
# Usage:
#   EXECUTOR_001_URL=https://exec-001.example.com \
#   EXECUTOR_002_URL=https://exec-002.example.com \
#   bash validate_executors.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/pilot/pilot_common.sh
source "$SCRIPT_DIR/pilot_common.sh"

echo "== Executor validation =="

check_one() {
  local code="$1" url="$2" expected_ip="$3"
  if [[ -z "$url" ]]; then
    fail "${code}: URL not provided (set ${code}_URL)"
    return
  fi
  local health egress reported_code reported_ip
  health="$(curl -fsS --max-time 10 "${url}/health" || true)"
  reported_code="$(printf '%s' "$health" | sed -n 's/.*"executor_code":"\([^"]*\)".*/\1/p')"
  if [[ "$(printf '%s' "$health" | sed -n 's/.*"status":"\([^"]*\)".*/\1/p')" == "ok" \
        && "$reported_code" == "$code" ]]; then
    pass "${code}: health ok"
  else
    fail "${code}: health check failed (reported code='${reported_code:-none}')"
  fi
  egress="$(curl -fsS --max-time 10 "${url}/egress-ip" || true)"
  reported_ip="$(printf '%s' "$egress" | sed -n 's/.*"egress_ip":"\([^"]*\)".*/\1/p')"
  assert_ip_match "$code" "$expected_ip" "$reported_ip"
}

check_one "EXECUTOR_001" "$EXECUTOR_001_URL" "$EXECUTOR_001_EXPECTED_IP"
check_one "EXECUTOR_002" "$EXECUTOR_002_URL" "$EXECUTOR_002_EXPECTED_IP"

finish
