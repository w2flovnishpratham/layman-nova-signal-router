#!/usr/bin/env bash
#
# Verify that this droplet's ACTUAL outbound IP equals its configured DigitalOcean
# Reserved IP. This is the SEBI/Dhan whitelist guarantee: a real order must leave
# from the user's whitelisted IP. Run on the executor droplet.
#
# It does NOT trust the executor service; it queries an external IP-echo service
# directly so a misconfigured reserved-IP route is caught independently.
#
# Usage:
#   bash check_reserved_ip_route.sh [reserved_ip]
#
# Expected reserved IPs:
#   EXECUTOR_001 -> 64.225.87.19
#   EXECUTOR_002 -> 152.42.157.165
#
# Testing/override: set EXECUTOR_ACTUAL_IP_OVERRIDE to bypass the network lookup.
set -euo pipefail

reserved_ip="${1:-${EXECUTOR_RESERVED_IP:-}}"
check_url="${EXECUTOR_EGRESS_CHECK_URL:-https://api.ipify.org?format=json}"

if [[ -z "$reserved_ip" ]]; then
  echo "FAIL: reserved IP not provided (arg 1 or EXECUTOR_RESERVED_IP)" >&2
  exit 1
fi

if [[ -n "${EXECUTOR_ACTUAL_IP_OVERRIDE:-}" ]]; then
  actual_ip="${EXECUTOR_ACTUAL_IP_OVERRIDE}"
else
  raw="$(curl -fsS --max-time 10 "$check_url")"
  # Accept either {"ip":"..."} or a bare address.
  actual_ip="$(printf '%s' "$raw" | sed -n 's/.*"ip":[ ]*"\([^"]*\)".*/\1/p')"
  if [[ -z "$actual_ip" ]]; then
    actual_ip="$(printf '%s' "$raw" | tr -d '[:space:]')"
  fi
fi

echo "configured reserved IP: ${reserved_ip}"
echo "actual outbound IP:     ${actual_ip}"

if [[ -z "$actual_ip" ]]; then
  echo "FAIL: could not determine the actual outbound IP" >&2
  exit 1
fi
if [[ "$actual_ip" != "$reserved_ip" ]]; then
  echo "FAIL: actual outbound IP does not match the reserved IP — DO NOT place orders" >&2
  exit 1
fi
echo "PASS: outbound IP matches the reserved IP"
