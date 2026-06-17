#!/usr/bin/env bash
set -euo pipefail

# Idempotent VPS firewall setup. This intentionally configures the OS firewall
# on the VPS; it cannot repair a provider-side Hostinger panel firewall.

export DEBIAN_FRONTEND=noninteractive

if ! command -v ufw >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ufw
fi

ufw default deny incoming
ufw default allow outgoing

# Keep SSH open before enabling UFW. Hostinger deployment currently uses port 22.
ufw allow OpenSSH
ufw allow 22/tcp

# Public backend ingress is via nginx.
ufw allow 80/tcp
ufw allow 443/tcp

# The FastAPI app binds to 127.0.0.1 and should not be public.
ufw deny 8000/tcp || true
ufw deny 8001/tcp || true
ufw deny 8002/tcp || true

ufw --force enable
ufw status verbose
