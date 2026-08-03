#!/usr/bin/env bash
set -euo pipefail

domain="engine.novatradesolution.com"

resolved_ip="$(getent ahostsv4 "$domain" | awk 'NR == 1 { print $1 }')"
if [[ "$resolved_ip" != "187.127.153.128" ]]; then
  echo "$domain must resolve to 187.127.153.128 before TLS can be enabled." >&2
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
