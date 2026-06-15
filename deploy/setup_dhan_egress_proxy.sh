#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 <control-plane-ip> <proxy-port> <proxy-user> <proxy-password>"
  exit 1
fi

CONTROL_PLANE_IP="$1"
PROXY_PORT="$2"
PROXY_USER="$3"
PROXY_PASSWORD="$4"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y tinyproxy ufw unattended-upgrades

cp /etc/tinyproxy/tinyproxy.conf /etc/tinyproxy/tinyproxy.conf.bak
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
ufw --force enable

echo "tinyproxy is active on port ${PROXY_PORT}; only ${CONTROL_PLANE_IP} is allowed."
