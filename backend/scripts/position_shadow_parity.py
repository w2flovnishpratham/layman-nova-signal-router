#!/usr/bin/env python
"""Report-only parity check: JSON position files vs PostgreSQL shadow rows.

Usage (from backend/, DATABASE_URL set):

    python -m scripts.position_shadow_parity                 # every user dir
    python -m scripts.position_shadow_parity --user <uuid>   # one user

JSON is the execution read authority during Phase 2A; this tool only reports.
There is deliberately no repair mode. Exit code 1 when discrepancies exist.
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
from app.services.position_store import compare_user_positions  # noqa: E402


def _read_position_file(path: Path) -> dict | None:
    """None = file missing; {'__invalid__': ...} = unreadable JSON."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("position file is not a JSON object")
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        return {"__invalid__": str(exc)}


def collect_user_dirs() -> dict[str, Path]:
    users_root = RUNTIME_STATE_DIR / "users"
    dirs: dict[str, Path] = {}
    if users_root.exists():
        for child in users_root.iterdir():
            if child.is_dir():
                try:
                    uuid.UUID(child.name)
                except ValueError:
                    continue
                dirs[child.name] = child
    return dirs


def run(user_filter: str | None) -> dict:
    findings: list[dict] = []
    checked = 0
    for user_id_str, state_dir in sorted(collect_user_dirs().items()):
        if user_filter and user_id_str != user_filter:
            continue
        checked += 1
        json_live = _read_position_file(state_dir / "open_position.json")
        json_paper = _read_position_file(state_dir / "paper_position.json")
        for mode, data in (("live", json_live), ("paper", json_paper)):
            if isinstance(data, dict) and "__invalid__" in data:
                findings.append({
                    "type": "invalid_json_position_file",
                    "user_id": user_id_str,
                    "mode": mode,
                    "error": data["__invalid__"],
                })
        json_live = None if (json_live and "__invalid__" in json_live) else json_live
        json_paper = None if (json_paper and "__invalid__" in json_paper) else json_paper
        findings.extend(
            compare_user_positions(uuid.UUID(user_id_str), json_live=json_live, json_paper=json_paper)
        )
    typed_types = {"missing_typed_event", "typed_event_state_lag", "unexpected_generic_event", "duplicate_typed_event"}
    from app.services.position_read_shadow import health as read_shadow_health
    return {
        "users_checked": checked,
        "write_parity": {"findings": [item for item in findings if item.get("type") not in typed_types]},
        "read_shadow_parity": read_shadow_health(),
        "typed_event_parity": {"findings": [item for item in findings if item.get("type") in typed_types]},
        "findings": findings,  # backward-compatible aggregate; use sections above for new tooling
        "mode": "report-only",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", help="Limit to one user id (UUID).")
    args = parser.parse_args()
    if not database_configured():
        print("DATABASE_URL is not configured.", file=sys.stderr)
        return 2
    result = run(args.user)
    print(json.dumps(result, indent=2, default=str))
    return 1 if result["write_parity"]["findings"] or result["typed_event_parity"]["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
