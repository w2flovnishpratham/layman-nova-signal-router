#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_REF="${1:-origin/main}"
APP_DIR="${NOVA_APP_DIR:-/opt/nova-signal-router}"
SERVICE_NAME="${NOVA_SYSTEMD_SERVICE:-nova-signal-router}"
HEALTH_URL="${NOVA_HEALTH_URL:-http://127.0.0.1:8000/api/health}"
BUILD_FRONTEND="${NOVA_BUILD_FRONTEND:-false}"

cd "$APP_DIR"

echo "==> Fetching $TARGET_REF"
git fetch --prune origin
git checkout --force "$TARGET_REF"

echo "==> Installing backend dependencies"
cd "$APP_DIR/backend"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m compileall -q app

mkdir -p runtime_state runtime_logs data
chmod 700 runtime_state

if [ "$BUILD_FRONTEND" = "true" ]; then
  echo "==> Building frontend"
  cd "$APP_DIR/frontend"
  npm ci
  npm run build
fi

echo "==> Restarting systemd service: $SERVICE_NAME"
sudo -n systemctl restart "$SERVICE_NAME"
sudo -n systemctl is-active --quiet "$SERVICE_NAME"

echo "==> Checking health: $HEALTH_URL"
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "$HEALTH_URL" >/dev/null; then
    echo "Deploy complete."
    exit 0
  fi
  sleep 2
done

echo "Health check failed after restart." >&2
sudo -n systemctl status "$SERVICE_NAME" --no-pager || true
exit 1
