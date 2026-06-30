from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]

RAZORPAY_AND_AWS_SECRET_NAMES = (
    "PAYMENT_PROVIDER",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "RAZORPAY_PLAN_PREMIUM_MONTHLY",
    "AWS_PROXY_SLOTS_ENABLED",
    "AWS_PROXY_HOST",
    "AWS_PROXY_SHARED_PASSWORD",
    "AWS_PROXY_SLOT_1_PASSWORD",
    "AWS_PROXY_SLOT_2_PASSWORD",
    "AWS_PROXY_SLOT_3_PASSWORD",
    "AWS_PROXY_SLOT_4_PASSWORD",
    "AWS_PROXY_SLOT_5_PASSWORD",
)

ARMED_LIVE_REQUIRED_SECRET_NAMES = (
    "PAYMENT_PROVIDER",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "RAZORPAY_PLAN_PREMIUM_MONTHLY",
    "AWS_PROXY_SHARED_PASSWORD",
)


def _workflow_text() -> str:
    return (REPO_ROOT / ".github" / "workflows" / "layman-backend-ci-deploy.yml").read_text(
        encoding="utf-8"
    )


def _deploy_script_text() -> str:
    return (REPO_ROOT / "deploy" / "configure_vps_env.sh").read_text(encoding="utf-8")


def test_workflow_declares_production_secret_passthrough_names():
    workflow = _workflow_text()

    for name in RAZORPAY_AND_AWS_SECRET_NAMES:
        assert f"{name}: ${{{{ secrets.{name} }}}}" in workflow
        assert name in workflow.split("for optional_name in \\", 1)[1]

    assert 'printf \'%s=%s\\n\' "$optional_name" "${!optional_name}"' in workflow


def test_configure_vps_env_imports_razorpay_and_aws_secret_names():
    script = _deploy_script_text()
    optional_block = script.split("for optional_key in \\", 1)[1].split("do", 1)[0]

    for name in RAZORPAY_AND_AWS_SECRET_NAMES:
        assert name in optional_block

    assert 'set_env "$optional_key" "$value"' in script


def test_production_deploy_requires_shared_webhook_secret_and_enforces_hmac():
    workflow = _workflow_text()
    script = _deploy_script_text()

    assert "STRATEGY_WEBHOOK_SECRET: ${{ secrets.STRATEGY_WEBHOOK_SECRET }}" in workflow
    assert "DATABASE_URL GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET STRATEGY_WEBHOOK_SECRET" in workflow
    assert "DATABASE_URL GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET STRATEGY_WEBHOOK_SECRET" in script
    assert "set_env WEBHOOK_HMAC_REQUIRED true" in script


def test_configure_vps_env_targets_systemd_layman_env_file_when_configured():
    script = _deploy_script_text()

    assert "systemctl show layman-nova-signal-router.service -p Environment" in script
    assert "LAYMAN_ENV_FILE=*)" in script
    assert 'env_file="${LAYMAN_ENV_FILE:-${service_env_file:-$repo_dir/backend/.env}}"' in script
    assert 'mkdir -p "$(dirname "$env_file")"' in script


def test_configure_vps_env_armed_live_requires_razorpay_and_aws_secrets():
    script = _deploy_script_text()
    live_required_block = script.split("for live_required_key in \\", 1)[1].split("do", 1)[0]

    for name in ARMED_LIVE_REQUIRED_SECRET_NAMES:
        assert name in live_required_block

    assert 'Live trading deployment is armed but incomplete: ${live_required_key}' in script
    assert "Live trading deployment requires PAYMENT_PROVIDER=razorpay." in script


def test_configure_vps_env_armed_live_enables_aws_slots_without_secret_values():
    script = _deploy_script_text()

    assert "set_env AWS_PROXY_SLOTS_ENABLED true" in script
    assert "set_env AWS_PROXY_HOST 13.203.58.220" in script
    assert "set_env ENABLE_LIVE_ORDERS true" in script
    assert "set_env EXECUTION_NODE_ROUTING_ENABLED true" in script
    assert "set_env WEBHOOK_TRADING_ENABLED true" in script
    assert "razorpay-secret" not in script
    assert "webhook-secret" not in script
    assert "aws-secret" not in script


def test_configure_vps_env_syncs_runtime_webhook_state_for_explicit_deploy_modes():
    script = _deploy_script_text()

    assert "set_webhook_runtime_state()" in script
    assert 'RUNTIME_APP_STATE_FILE="$repo_dir/backend/runtime_state/app_state.json"' in script
    assert 'data["engine_started"] = enabled' in script
    assert 'data["webhook_trading_enabled"] = enabled' in script
    assert "set_webhook_runtime_state true" in script
    assert "set_webhook_runtime_state false" in script


def test_backend_settings_load_systemd_configured_layman_env_file(tmp_path):
    env_file = tmp_path / "layman.env"
    env_file.write_text("RAZORPAY_PLAN_PREMIUM_MONTHLY=plan_from_layman_env\n", encoding="utf-8")
    env = {
        **os.environ,
        "LAYMAN_ENV_FILE": str(env_file),
        "PYTHONPATH": str(REPO_ROOT / "backend"),
    }

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.config import settings; print(settings.RAZORPAY_PLAN_PREMIUM_MONTHLY)",
        ],
        cwd=REPO_ROOT / "backend",
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "plan_from_layman_env"


def test_failed_vps_health_check_prints_service_journal():
    deploy_script = (REPO_ROOT / "deploy" / "deploy_vps.sh").read_text(encoding="utf-8")

    assert "journalctl -u layman-nova-signal-router.service -n 160 --no-pager -l" in deploy_script
