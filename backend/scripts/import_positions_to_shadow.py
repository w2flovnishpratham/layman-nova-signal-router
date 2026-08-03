#!/usr/bin/env python
"""Explicit importer: current JSON positions -> PostgreSQL shadow rows.

Usage (from backend/, DATABASE_URL set):

    python -m scripts.import_positions_to_shadow                 # all user dirs
    python -m scripts.import_positions_to_shadow --user <uuid>   # one user
    python -m scripts.import_positions_to_shadow --include-legacy-global
        # also import the legacy unscoped runtime_state/ files (single-operator
        # mode) under the given --legacy-user <uuid>

Idempotent (snapshot fingerprint), never overwrites a newer shadow, marks
rows/events as imported. Never runs automatically at startup or import time.
JSON remains the execution read authority.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import RUNTIME_STATE_DIR  # noqa: E402
from app.db.engine import database_configured  # noqa: E402
from app.services.position_store import import_json_position  # noqa: E402
from scripts.position_shadow_parity import _read_position_file, collect_user_dirs  # noqa: E402


def _import_dir(user_id: uuid.UUID, state_dir: Path, results: list[dict]) -> None:
    for mode, filename in (("live", "open_position.json"), ("paper", "paper_position.json")):
        data = _read_position_file(state_dir / filename)
        if data is None:
            continue
        if "__invalid__" in data:
            results.append({"user_id": str(user_id), "mode": mode, "status": "invalid_json", "error": data["__invalid__"]})
            continue
        outcome = import_json_position(user_id=user_id, execution_mode=mode, position=data)
        results.append({"user_id": str(user_id), "mode": mode, **outcome})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", help="Limit to one user id (UUID).")
    parser.add_argument("--include-legacy-global", action="store_true",
                        help="Also import the legacy unscoped runtime_state files.")
    parser.add_argument("--legacy-user", help="User id (UUID) to own the legacy global position rows.")
    args = parser.parse_args()
    if not database_configured():
        print("DATABASE_URL is not configured.", file=sys.stderr)
        return 2

    results: list[dict] = []
    for user_id_str, state_dir in sorted(collect_user_dirs().items()):
        if args.user and user_id_str != args.user:
            continue
        _import_dir(uuid.UUID(user_id_str), state_dir, results)

    if args.include_legacy_global:
        if not args.legacy_user:
            print("--include-legacy-global requires --legacy-user <uuid>.", file=sys.stderr)
            return 2
        _import_dir(uuid.UUID(args.legacy_user), RUNTIME_STATE_DIR, results)

    print(json.dumps({"results": results}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
