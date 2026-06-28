#!/usr/bin/env python
"""Assign and optionally verify a legacy manual egress proxy for a user."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import crud  # noqa: E402
from app.db.engine import database_configured, session_scope  # noqa: E402
from app.services.strategy_fanout import set_user_egress, verify_user_egress  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--public-ip", required=True)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify the proxy observed public IP after assignment.",
    )
    args = parser.parse_args()

    proxy_url = (os.environ.get("EGRESS_PROXY_URL") or "").strip()
    if not proxy_url:
        print("ERROR: set EGRESS_PROXY_URL to the backend-only proxy URL.")
        return 1
    if not database_configured():
        print("ERROR: DATABASE_URL is not configured.")
        return 1

    with session_scope() as db:
        user = crud.get_user_by_email(db, args.email)
        if user is None:
            print("ERROR: user not found. The user must sign in with Google once first.")
            return 1
        user_id = user.id

    status = set_user_egress(
        user_id,
        public_ip=args.public_ip,
        proxy_url=proxy_url,
        active=True,
    )
    print(
        f"OK: assigned {status['public_ip']} to {args.email}; "
        f"backend proxy configured={status['has_proxy']}."
    )
    if not args.verify:
        return 0

    verification = verify_user_egress(user_id)
    if not verification.get("ok"):
        print(f"ERROR: egress verification failed: {verification}")
        return 2
    print(f"OK: Nova Static IP egress verified as {verification['observed_ip']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
