#!/usr/bin/env bash
set -euo pipefail

repo_dir="${LAYMAN_REPO_DIR:-/opt/layman-nova-signal-router}"
exec bash "$repo_dir/deploy/deploy_vps.sh" "$@"
