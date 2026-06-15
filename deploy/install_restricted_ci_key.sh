#!/usr/bin/env bash
set -euo pipefail

public_key_file="${1:?Usage: install_restricted_ci_key.sh /path/to/public-key}"
authorized_keys="/root/.ssh/authorized_keys"

install -d -m 700 /root/.ssh
touch "$authorized_keys"

tmp_file="$(mktemp /root/.ssh/authorized_keys.XXXXXX)"
grep -v 'github-actions-layman' "$authorized_keys" > "$tmp_file" || true
printf 'command="bash /root/layman-nova-signal-router/deploy/deploy_vps.sh",restrict %s\n' \
  "$(cat "$public_key_file")" >> "$tmp_file"

chmod 600 "$tmp_file"
mv "$tmp_file" "$authorized_keys"
