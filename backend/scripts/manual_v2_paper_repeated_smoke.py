#!/usr/bin/env python
"""Run repeated isolated v2 paper scenario matrix smoke checks.

This is ops-only verification tooling. It does not use the configured staging
DB by default and does not require real Dhan credentials.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.manual_v2_paper_scenario_matrix import (  # noqa: E402
    SENSITIVE_KEYS,
    ScenarioMatrixConfig,
    run_scenario_matrix,
)


class RepeatedSmokeError(RuntimeError):
    """Raised when repeated v2 paper smoke cannot complete safely."""


@dataclass(frozen=True)
class RepeatedSmokeConfig:
    iterations: int = 3
    work_dir: Path | None = None
    keep_temp: bool = False


def run_repeated_smoke(
    config: RepeatedSmokeConfig,
    *,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    if config.iterations < 1:
        raise RepeatedSmokeError("--iterations must be at least 1.")

    root = config.work_dir or Path(tempfile.mkdtemp(prefix="phase2e7-v2-paper-repeated-"))
    root.mkdir(parents=True, exist_ok=True)
    iteration_results: list[dict[str, Any]] = []

    for index in range(1, config.iterations + 1):
        iteration_root = root / f"iteration-{index}"
        try:
            summary = run_scenario_matrix(
                ScenarioMatrixConfig(work_dir=iteration_root, keep_temp=config.keep_temp),
                out=lambda _line: None,
            )
            iteration_results.append(
                {
                    "iteration": index,
                    "ok": bool(summary.get("overall_ok")),
                    "fresh_isolated_db": bool(summary.get("fresh_isolated_db")),
                    "runtime_isolated": bool(summary.get("runtime_isolated")),
                    "scenarios": summary.get("scenarios", {}),
                    "sensitive_field_count": _sensitive_field_count(summary),
                }
            )
        except Exception as exc:
            iteration_results.append(
                {
                    "iteration": index,
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": _safe_error(exc),
                }
            )
            break

    successful = sum(1 for item in iteration_results if item.get("ok") is True)
    final = _sanitize(
        {
            "ok": successful == config.iterations,
            "iterations_requested": config.iterations,
            "iterations_completed": len(iteration_results),
            "successful_iterations": successful,
            "fresh_isolated_db_all": all(item.get("fresh_isolated_db") is True for item in iteration_results),
            "runtime_isolated_all": all(item.get("runtime_isolated") is True for item in iteration_results),
            "sensitive_field_count": _sensitive_field_count(iteration_results),
            "results": iteration_results,
        }
    )
    out(json.dumps({"event": "repeated_v2_paper_smoke_summary", "data": final}, sort_keys=True))
    if not final["ok"]:
        raise RepeatedSmokeError("Repeated v2 paper smoke failed.")
    if not config.keep_temp:
        shutil.rmtree(root, ignore_errors=True)
    return final


def parse_args(argv: list[str] | None = None) -> RepeatedSmokeConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args(argv)
    return RepeatedSmokeConfig(
        iterations=args.iterations,
        work_dir=args.work_dir,
        keep_temp=bool(args.keep_temp),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        run_repeated_smoke(parse_args(argv))
    except RepeatedSmokeError as exc:
        print(json.dumps({"ok": False, "error": _safe_error(exc)}, sort_keys=True))
        return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__, "error": _safe_error(exc)}, sort_keys=True))
        return 1
    return 0


def _safe_error(exc: Exception) -> str:
    if _sensitive_field_count({"error": str(exc)}):
        return "Repeated smoke failed."
    return str(exc)


def _sanitize(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return _sanitize(asdict(value))
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                continue
            sanitized[str(key)] = _sanitize(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def _sensitive_field_count(value: Any) -> int:
    if isinstance(value, dict):
        count = 0
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                count += 1
                continue
            count += _sensitive_field_count(item)
        return count
    if isinstance(value, list):
        return sum(_sensitive_field_count(item) for item in value)
    if isinstance(value, tuple):
        return sum(_sensitive_field_count(item) for item in value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
