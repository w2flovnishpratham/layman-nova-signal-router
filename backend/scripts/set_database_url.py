#!/usr/bin/env python
"""Update DATABASE_URL in the ignored backend/.env without printing it."""
from __future__ import annotations

import argparse
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = BACKEND_DIR / ".env"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database_url")
    args = parser.parse_args()

    value = args.database_url.strip()
    if not value.startswith(("postgresql://", "postgres://", "postgresql+psycopg://")):
        raise SystemExit("DATABASE_URL must be a PostgreSQL connection string.")
    if not ENV_PATH.exists():
        raise SystemExit("backend/.env is missing; configure local auth first.")

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    updated = False
    output: list[str] = []
    for line in lines:
        if line.startswith("DATABASE_URL="):
            output.append(f"DATABASE_URL={value}")
            updated = True
        else:
            output.append(line)
    if not updated:
        output.append(f"DATABASE_URL={value}")
    ENV_PATH.write_text("\n".join(output) + "\n", encoding="utf-8")
    print("Updated backend/.env DATABASE_URL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
