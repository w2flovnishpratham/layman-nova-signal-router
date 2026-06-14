#!/usr/bin/env bash
#
# Check that the local executor service is healthy and reports the configured
# executor code. Run on the executor droplet.
#
# Usage:
#   bash check_executor_health.sh [base_url]
# Defaults to the local bind host/port from the environment file.
set -euo pipefail

base_url="${1:-}"
bind_host="${EXECUTOR_BIND_HOST:-127.0.0.1}"
port="${EXECUTOR_PORT:-8010}"
if [[ -z "$base_url" ]]; then
  base_url="http://${bind_host}:${port}"
fi

expected_code="${EXECUTOR_CODE:-}"
response="$(curl -fsS --max-time 10 "${base_url}/health")"
echo "health response: ${response}"

status="$(printf '%s' "$response" | sed -n 's/.*"status":"\([^"]*\)".*/\1/p')"
code="$(printf '%s' "$response" | sed -n 's/.*"executor_code":"\([^"]*\)".*/\1/p')"

if [[ "$status" != "ok" ]]; then
  echo "FAIL: executor status is not ok" >&2
  exit 1
fi
if [[ -n "$expected_code" && "$code" != "$expected_code" ]]; then
  echo "FAIL: executor_code ${code} does not match expected ${expected_code}" >&2
  exit 1
fi
echo "PASS: executor healthy (${code:-unknown})"
