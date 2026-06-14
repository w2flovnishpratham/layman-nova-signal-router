#!/usr/bin/env bash
set -euo pipefail

domain="${LAYMAN_API_DOMAIN:-layman-api.manyacare.com}"
expected_ip="${LAYMAN_EXPECTED_IP:?Set LAYMAN_EXPECTED_IP to the VPS public IPv4 address.}"

resolved_ip="$(getent ahostsv4 "$domain" | awk 'NR == 1 { print $1 }')"
if [[ "$resolved_ip" != "$expected_ip" ]]; then
  echo "$domain must resolve to the configured VPS IP before TLS can be enabled." >&2
  exit 1
fi

certbot --nginx \
  --domain "$domain" \
  --non-interactive \
  --agree-tos \
  --redirect \
  --keep-until-expiring

nginx -t
systemctl reload nginx
curl --fail --silent --show-error "https://$domain/api/health"
