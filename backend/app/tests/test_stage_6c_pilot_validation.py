from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
PILOT_DIR = REPO_ROOT / "deploy" / "pilot"
DOCS_DIR = REPO_ROOT / "docs"

VALIDATION_SCRIPTS = [
    "validate_hostinger_main.sh",
    "validate_neon_database.sh",
    "validate_executors.sh",
    "validate_dry_run_signal.sh",
    "disable_live_everywhere.sh",
    "pilot_go_no_go_check.sh",
    "pilot_common.sh",
]

EXEC_1_IP = "64.225.87.19"
EXEC_2_IP = "152.42.157.165"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_all_pilot_scripts_exist():
    for name in VALIDATION_SCRIPTS:
        assert (PILOT_DIR / name).is_file(), f"missing pilot script {name}"


def test_validation_scripts_contain_both_executor_ips():
    # The expected mapping lives in pilot_common.sh and is used by the validators.
    common = _read(PILOT_DIR / "pilot_common.sh")
    assert EXEC_1_IP in common
    assert EXEC_2_IP in common
    go = _read(PILOT_DIR / "pilot_go_no_go_check.sh")
    # go/no-go sources the expected IPs from common and asserts both.
    assert "EXECUTOR_001_EXPECTED_IP" in go
    assert "EXECUTOR_002_EXPECTED_IP" in go


def test_disable_script_turns_live_flags_off():
    disable = _read(PILOT_DIR / "disable_live_everywhere.sh")
    assert "ENABLE_LIVE_ORDERS false" in disable
    assert "LIVE_ORDER_DRY_RUN_ONLY true" in disable
    assert "EXECUTOR_REAL_ORDERS_ENABLED=false" in disable


def test_scripts_do_not_echo_secrets():
    # No script should echo/print known secret-bearing variables.
    secret_tokens = ["RELAY_TOKEN", "EXECUTOR_SHARED_SECRET", "DATABASE_URL", "TOKEN_ENCRYPTION_KEY", "SESSION_TOKEN_SECRET"]
    for name in VALIDATION_SCRIPTS:
        text = _read(PILOT_DIR / name)
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.match(r"^(echo|printf)\b", stripped):
                for token in secret_tokens:
                    assert token not in line, f"{name} may echo secret {token}: {line}"


def _run_go_no_go(env_overrides):
    script = PILOT_DIR / "pilot_go_no_go_check.sh"
    env = {**os.environ, "PILOT_CHECK_ONLY": "1", **env_overrides}
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)


def _bash_available() -> bool:
    return shutil.which("bash") is not None and os.name != "nt"


def test_go_no_go_fails_when_dry_run_flag_false_too_early():
    if not _bash_available():
        pytest.skip("bash unavailable")
    result = _run_go_no_go(
        {
            "LIVE_ORDER_DRY_RUN_ONLY": "false",
            "ENABLE_LIVE_ORDERS": "false",
            "EXECUTOR_001_ACTUAL_IP": EXEC_1_IP,
            "EXECUTOR_002_ACTUAL_IP": EXEC_2_IP,
        }
    )
    assert result.returncode != 0
    assert "FAIL" in (result.stdout + result.stderr)


def test_go_no_go_fails_on_executor_ip_mismatch():
    if not _bash_available():
        pytest.skip("bash unavailable")
    result = _run_go_no_go(
        {
            "LIVE_ORDER_DRY_RUN_ONLY": "true",
            "ENABLE_LIVE_ORDERS": "false",
            "EXECUTOR_001_ACTUAL_IP": "10.0.0.9",
            "EXECUTOR_002_ACTUAL_IP": EXEC_2_IP,
        }
    )
    assert result.returncode != 0


def test_go_no_go_passes_when_safe_and_ips_match():
    if not _bash_available():
        pytest.skip("bash unavailable")
    result = _run_go_no_go(
        {
            "LIVE_ORDER_DRY_RUN_ONLY": "true",
            "ENABLE_LIVE_ORDERS": "false",
            "EXECUTOR_001_ACTUAL_IP": EXEC_1_IP,
            "EXECUTOR_002_ACTUAL_IP": EXEC_2_IP,
        }
    )
    assert result.returncode == 0
    assert "GO" in result.stdout


def test_go_no_go_fails_when_live_enabled_early():
    if not _bash_available():
        pytest.skip("bash unavailable")
    result = _run_go_no_go(
        {
            "LIVE_ORDER_DRY_RUN_ONLY": "true",
            "ENABLE_LIVE_ORDERS": "true",
            "EXECUTOR_001_ACTUAL_IP": EXEC_1_IP,
            "EXECUTOR_002_ACTUAL_IP": EXEC_2_IP,
        }
    )
    assert result.returncode != 0


# --------------------------------------------------------------------------- #
# Runbook / checklist content                                                 #
# --------------------------------------------------------------------------- #
def test_pilot_checklist_says_not_public_launch():
    text = _read(DOCS_DIR / "FIRST_2_ACCOUNT_LIVE_PILOT_CHECKLIST.md").lower()
    assert "not a public live launch" in text or "not a public launch" in text


def test_pilot_checklist_says_one_tiny_order_only():
    text = _read(DOCS_DIR / "FIRST_2_ACCOUNT_LIVE_PILOT_CHECKLIST.md").lower()
    assert "one tiny order" in text


def test_pilot_checklist_says_disable_flags_immediately_after_test():
    text = _read(DOCS_DIR / "FIRST_2_ACCOUNT_LIVE_PILOT_CHECKLIST.md")
    assert "Disable immediately after the test" in text or "disable" in text.lower()
    assert "ENABLE_LIVE_ORDERS=false" in text
    assert "EXECUTOR_REAL_ORDERS_ENABLED=false" in text


def test_validation_doc_and_emergency_runbook_exist():
    assert (DOCS_DIR / "STAGE_6C_REAL_VPS_DEPLOYMENT_VALIDATION.md").is_file()
    assert (DOCS_DIR / "EMERGENCY_LIVE_DISABLE_RUNBOOK.md").is_file()
    emergency = _read(DOCS_DIR / "EMERGENCY_LIVE_DISABLE_RUNBOOK.md")
    assert "disable_live_everywhere.sh" in emergency
