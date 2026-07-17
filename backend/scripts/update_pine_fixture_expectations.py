"""Review or explicitly update exact R1A Pine fixture expectations."""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
FIXTURES = BACKEND_DIR / "app" / "tests" / "pine_fixtures" / "r1a"
sys.path.insert(0, str(BACKEND_DIR))

from app.services.pine_semantic_preanalyzer import analyze_source  # noqa: E402


def _document(pine_path: Path) -> dict[str, object]:
    result = analyze_source(pine_path.read_text(encoding="utf-8"))
    return {
        "fixture_id": pine_path.name.split("_", 1)[0],
        "source_sha256": result.source_sha256,
        "analyzer_version": result.analyzer_version,
        "registry_id": result.registry_id,
        "registry_version": result.registry_version,
        "registry_sha256": result.registry_sha256,
        "matched_capabilities_exact": list(result.matched_capabilities),
        "effective_capability_level": result.effective_capability_level.value,
        "temporal_classes_exact": [item.value for item in result.temporal_classes],
        "blocker_codes_exact": list(result.blocker_codes),
        "disclosure_codes_exact": list(result.disclosure_codes),
        "admin_review_points_exact": list(result.admin_review_points),
        "confidence": result.confidence.value,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write",
        action="store_true",
        help="write reviewed expectation changes (default is dry-run)",
    )
    args = parser.parse_args()

    changes: list[tuple[Path, str, str]] = []
    for pine_path in sorted(FIXTURES.glob("*.pine")):
        expected_path = pine_path.with_suffix(".expected.json")
        current = expected_path.read_text(encoding="utf-8") if expected_path.exists() else ""
        proposed = json.dumps(_document(pine_path), indent=2, ensure_ascii=False) + "\n"
        if current != proposed:
            changes.append((expected_path, current, proposed))

    for path, current, proposed in changes:
        print(
            "".join(
                difflib.unified_diff(
                    current.splitlines(keepends=True),
                    proposed.splitlines(keepends=True),
                    fromfile=str(path),
                    tofile=str(path),
                )
            ),
            end="",
        )
    mode = "written" if args.write else "dry-run"
    print(f"{mode}: {len(changes)} expectation file(s) changed; {len(list(FIXTURES.glob('*.pine')))} fixture(s) reviewed")

    if args.write:
        for path, _, proposed in changes:
            path.write_text(proposed, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
