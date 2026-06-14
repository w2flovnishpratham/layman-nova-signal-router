from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ENV_FILES = {
    "backend/.env.example",
    "backend/.env.live.example",
    "backend/.env.local.example",
    "frontend/.env.example",
}


def forbidden_reason(relative_path: str) -> str | None:
    normalized = relative_path.replace("\\", "/").lstrip("./")
    lowered = normalized.lower()
    name = Path(normalized).name.lower()
    parts = set(lowered.split("/"))

    if fnmatch.fnmatch(name, "client_secret*.json"):
        return "OAuth client JSON must not be included"
    if name == ".env" or (name.startswith(".env.") and normalized not in ALLOWED_ENV_FILES):
        return "real environment files must not be tracked"
    if name.endswith((".sqlite3", ".db")) or ".sqlite3-" in name or ".db-" in name:
        return "runtime databases must not be included"
    if "runtime_state" in parts or "runtime_logs" in parts:
        return "runtime state/log files must not be included"
    if lowered.startswith("frontend/node_modules/") or lowered.startswith("frontend/dist/"):
        return "frontend generated artifacts must not be included"
    if "__pycache__" in parts or name.endswith((".pyc", ".pyo")):
        return "Python generated artifacts must not be included"
    return None


def tracked_files() -> list[str]:
    command = [
        "git",
        "-c",
        f"safe.directory={ROOT.as_posix()}",
        "ls-files",
    ]
    result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def sensitive_worktree_files() -> list[str]:
    candidates: list[Path] = []
    candidates.extend(ROOT.glob("**/client_secret*.json"))
    candidates.extend(ROOT.glob("**/*.sqlite3"))
    candidates.extend(ROOT.glob("**/*.db"))
    for path in ROOT.glob("**/.env*"):
        if path.is_file():
            relative = path.relative_to(ROOT).as_posix()
            if relative not in ALLOWED_ENV_FILES:
                candidates.append(path)
    for directory in (ROOT / "backend/runtime_state", ROOT / "backend/runtime_logs"):
        if directory.exists():
            candidates.extend(path for path in directory.rglob("*") if path.is_file())
    return sorted({path.relative_to(ROOT).as_posix() for path in candidates})


def main() -> int:
    parser = argparse.ArgumentParser(description="Reject secrets and runtime artifacts from the repository/package.")
    parser.add_argument(
        "--worktree",
        action="store_true",
        help="Also report ignored sensitive files physically present in the working folder.",
    )
    args = parser.parse_args()

    failures = [
        f"{path}: {reason}"
        for path in tracked_files()
        if (reason := forbidden_reason(path)) is not None
    ]
    if args.worktree:
        failures.extend(f"{path}: sensitive file exists in working folder" for path in sensitive_worktree_files())

    if failures:
        print("Repository hygiene check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("Repository hygiene check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
