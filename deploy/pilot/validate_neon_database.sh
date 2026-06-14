#!/usr/bin/env bash
#
# Stage 6C: validate the Neon PostgreSQL database is reachable and migrated to head.
# Uses the backend venv + Alembic. Never prints DATABASE_URL or any secret.
#
# Usage: bash validate_neon_database.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/pilot/pilot_common.sh
source "$SCRIPT_DIR/pilot_common.sh"

BACKEND_DIR="${BACKEND_DIR:-/opt/layman-nova-signal-router/backend}"
PYTHON_BIN="${PYTHON_BIN:-$BACKEND_DIR/.venv/bin/python}"
ALEMBIC_BIN="${ALEMBIC_BIN:-$BACKEND_DIR/.venv/bin/alembic}"

echo "== Neon database validation =="

if [[ ! -x "$PYTHON_BIN" ]]; then
  fail "backend python venv not found at ${PYTHON_BIN}"
  finish
fi

cd "$BACKEND_DIR"

# 1. Connectivity + migration head, via the app's own readiness helper.
#    The helper returns booleans only; no connection string is printed.
if "$PYTHON_BIN" - <<'PY'
import sys
from app.services.readiness import database_ready, migrations_ready
try:
    db = bool(database_ready())
    mig = bool(migrations_ready())
except Exception as exc:  # pragma: no cover - reported as FAIL
    print(f"  - exception during database checks: {type(exc).__name__}")
    sys.exit(2)
print(f"  - database_ready={db} migrations_ready={mig}")
sys.exit(0 if (db and mig) else 3)
PY
then
  pass "database reachable and migrations at head"
else
  rc=$?
  if [[ "$rc" -eq 2 ]]; then
    fail "database connectivity/migration check raised an exception"
  else
    fail "database is unreachable or migrations are not at head (run: alembic upgrade head)"
  fi
fi

# 2. Alembic check: no pending model/schema drift.
if "$ALEMBIC_BIN" check >/dev/null 2>&1; then
  pass "alembic check: no new upgrade operations detected"
else
  fail "alembic check reported pending operations or failed"
fi

finish
