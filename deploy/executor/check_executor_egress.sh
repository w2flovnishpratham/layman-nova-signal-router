#!/usr/bin/env bash
#
# Check the executor's /egress-ip endpoint and compare it to the configured
# reserved IP. Run on the executor droplet.
#
# Usage:
#   bash check_executor_egress.sh [base_url]
set -euo pipefail

base_url="${1:-}"
bind_host="${EXECUTOR_BIND_HOST:-127.0.0.1}"
port="${EXECUTOR_PORT:-8010}"
if [[ -z "$base_url" ]]; then
  base_url="http://${bind_host}:${port}"
fi

reserved_ip="${EXECUTOR_RESERVED_IP:-}"
if [[ -z "$reserved_ip" ]]; then
  echo "FAIL: EXECUTOR_RESERVED_IP is not set" >&2
  exit 1
fi

response="$(curl -fsS --max-time 10 "${base_url}/egress-ip")"
echo "egress-ip response: ${response}"
egress_ip="$(printf '%s' "$response" | sed -n 's/.*"egress_ip":"\([^"]*\)".*/\1/p')"

echo "configured reserved IP: ${reserved_ip}"
echo "reported egress IP:     ${egress_ip}"

if [[ "$egress_ip" != "$reserved_ip" ]]; then
  echo "FAIL: reported egress IP does not match the reserved IP" >&2
  exit 1
fi
echo "PASS: executor egress IP matches reserved IP"
