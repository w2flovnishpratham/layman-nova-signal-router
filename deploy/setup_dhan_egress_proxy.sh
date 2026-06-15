#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <control-plane-ip> <proxy-port>"
  exit 1
fi

CONTROL_PLANE_IP="$1"
PROXY_PORT="$2"
CREDENTIALS_FILE="/root/.config/layman-egress-proxy.env"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y tinyproxy ufw unattended-upgrades openssl

if [[ -f "${CREDENTIALS_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${CREDENTIALS_FILE}"
else
  PROXY_USER="layman_$(openssl rand -hex 4)"
  PROXY_PASSWORD="$(openssl rand -hex 32)"
  install -d -m 700 "$(dirname "${CREDENTIALS_FILE}")"
  umask 077
  {
    printf 'PROXY_USER=%q\n' "${PROXY_USER}"
    printf 'PROXY_PASSWORD=%q\n' "${PROXY_PASSWORD}"
    printf 'PROXY_PORT=%q\n' "${PROXY_PORT}"
  } >"${CREDENTIALS_FILE}"
fi

if [[ ! -f /etc/tinyproxy/tinyproxy.conf.bak ]]; then
  cp /etc/tinyproxy/tinyproxy.conf /etc/tinyproxy/tinyproxy.conf.bak
fi
cat >/etc/tinyproxy/tinyproxy.conf <<EOF
User tinyproxy
Group tinyproxy
Port ${PROXY_PORT}
Listen 0.0.0.0
Timeout 30
DefaultErrorFile "/usr/share/tinyproxy/default.html"
StatFile "/usr/share/tinyproxy/stats.html"
LogFile "/var/log/tinyproxy/tinyproxy.log"
LogLevel Warning
PidFile "/run/tinyproxy/tinyproxy.pid"
MaxClients 20
StartServers 2
MinSpareServers 2
MaxSpareServers 5
Allow ${CONTROL_PLANE_IP}
BasicAuth ${PROXY_USER} ${PROXY_PASSWORD}
DisableViaHeader Yes
ConnectPort 443
EOF

systemctl enable --now tinyproxy
systemctl restart tinyproxy

ufw allow OpenSSH
ufw allow from "${CONTROL_PLANE_IP}" to any port "${PROXY_PORT}" proto tcp
ufw deny "${PROXY_PORT}/tcp"
ufw --force enable

systemctl is-active --quiet tinyproxy
echo "tinyproxy is active on port ${PROXY_PORT}; only ${CONTROL_PLANE_IP} is allowed."
echo "Credentials are stored in ${CREDENTIALS_FILE} with root-only permissions."
