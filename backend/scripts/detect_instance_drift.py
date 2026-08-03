#!/usr/bin/env python
"""Report (and optionally repair) strategy-instance/subscription drift.

Usage (from backend/, DATABASE_URL set):

    python -m scripts.detect_instance_drift            # report only (default)
    python -m scripts.detect_instance_drift --repair   # explicit, audited repair

During the transition the strategy_subscriptions row is execution-authoritative,
so repair copies subscription values onto the instance and creates missing
instances. Dangling instance links (missing subscription) are report-only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.engine import database_configured  # noqa: E402
from app.services.strategy_instance_service import detect_drift  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repair", action="store_true", help="Apply audited repairs (subscription wins).")
    args = parser.parse_args()

    if not database_configured():
        print("DATABASE_URL is not configured.", file=sys.stderr)
        return 2

    result = detect_drift(repair=args.repair)
    print(json.dumps(result, indent=2, default=str))
    return 1 if result["findings"] and not args.repair else 0


if __name__ == "__main__":
    raise SystemExit(main())
