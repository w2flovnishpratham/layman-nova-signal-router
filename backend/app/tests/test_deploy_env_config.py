from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]

RAZORPAY_AND_AWS_SECRET_NAMES = (
    "PAYMENT_PROVIDER",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "RAZORPAY_PLAN_LIVE_MONTHLY",
    "RAZORPAY_PLAN_STATIC_IP_MONTHLY",
    "RAZORPAY_PLAN_STRATEGY_MONTHLY",
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
