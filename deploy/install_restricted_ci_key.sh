#!/usr/bin/env bash
set -euo pipefail

public_key_file="${1:?Usage: install_restricted_ci_key.sh /path/to/public-key [deploy-user]}"
deploy_user="${2:-deploy}"
repo_dir="/opt/layman-nova-signal-router"
home_dir="$(getent passwd "$deploy_user" | cut -d: -f6)"
deploy_group="$(id -gn "$deploy_user" 2>/dev/null || true)"

if [[ -z "$home_dir" || -z "$deploy_group" ]]; then
  echo "Deploy user does not exist: $deploy_user" >&2
  exit 1
fi

ssh_dir="$home_dir/.ssh"
authorized_keys="$ssh_dir/authorized_keys"
install -o "$deploy_user" -g "$deploy_group" -m 700 -d "$ssh_dir"
touch "$authorized_keys"

tmp_file="$(mktemp "$ssh_dir/authorized_keys.XXXXXX")"
grep -v 'github-actions-layman' "$authorized_keys" > "$tmp_file" || true
printf 'command="sudo -n bash %s/deploy/deploy_vps.sh",restrict %s github-actions-layman\n' \
  "$repo_dir" "$(cat "$public_key_file")" >> "$tmp_file"

chown "$deploy_user:$deploy_group" "$tmp_file"
chmod 600 "$tmp_file"
mv "$tmp_file" "$authorized_keys"
