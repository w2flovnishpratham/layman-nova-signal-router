from __future__ import annotations

import os
import re
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
    "RAZORPAY_PLAN_PAPER_PREMIUM",
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


def _nginx_text() -> str:
    return (REPO_ROOT / "deploy" / "nginx" / "engine-api.novatradesolution.com.conf").read_text(
        encoding="utf-8"
    )


def test_private_webhook_deploy_logging_is_disabled():
    nginx = _nginx_text()
    exact_location = nginx.split("location = /api/webhooks/private {", 1)[1].split("}", 1)[0]
    assert "access_log off;" in exact_location
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in exact_location
    assert "/api/webhooks/private/{" not in nginx
    assert "credential=" not in nginx
    assert "$request_body" not in nginx

    for service_file in (
        "layman-nova-signal-router.service",
        "layman-nova-signal-intake.service",
        "nova-staging.service.example",
    ):
        service = (REPO_ROOT / "deploy" / service_file).read_text(encoding="utf-8")
        assert "--no-access-log" in service


def test_only_enqueue_only_webhooks_route_to_the_intake_worker():
    """The intake worker runs with BACKGROUND_WORKER_RUNNER_ENABLED=false and is
    a second process. Only endpoints that enqueue a StrategyExecutionJob and
    return may go there. Anything that calls route_signal() inline (manual
    orders, /api/webhook/tradingview) relies on paper_portfolio's
    process-local threading.Lock and must stay on the single engine worker."""
    nginx = _nginx_text()

    for intake_route in (
        "location ^~ /api/webhook/strategy/ {",
        "location ^~ /api/webhook/user/ {",
        "location = /api/webhooks/private {",
    ):
        block = nginx.split(intake_route, 1)[1].split("}", 1)[0]
        assert "proxy_pass http://nova_intake;" in block, intake_route

    # tradingview executes inline -- it must never get its own location block
    # and must fall through to the engine via `location /`. Checked against the
    # actual location directives, since the comments legitimately name it.
    location_directives = re.findall(r"^\s*location\s+(.+?)\s*\{", nginx, re.MULTILINE)
    assert location_directives, "expected nginx location blocks"
    assert not [route for route in location_directives if "tradingview" in route]

    catch_all = nginx.rsplit("location / {", 1)[1]
    assert "proxy_pass http://nova_engine;" in catch_all

    # A dead intake worker must degrade to the engine, not drop webhooks.
    intake_upstream = nginx.split("upstream nova_intake {", 1)[1].split("}", 1)[0]
    assert "server 127.0.0.1:8102;" in intake_upstream
    assert "server 127.0.0.1:8002 backup;" in intake_upstream


def test_intake_worker_never_runs_singleton_background_workers():
    """Dhan WS, option monitor, EOD square-off, ghost watcher and the strategy
    job worker must run in exactly one process. A systemd Environment= entry
    outranks the same key in LAYMAN_ENV_FILE (pydantic-settings reads env vars
    ahead of env_file), so this holds regardless of the env file's value."""
    intake = (REPO_ROOT / "deploy" / "layman-nova-signal-intake.service").read_text(
        encoding="utf-8"
    )
    assert 'Environment="BACKGROUND_WORKER_RUNNER_ENABLED=false"' in intake
    assert "--port 8102" in intake

    engine = (REPO_ROOT / "deploy" / "layman-nova-signal-router.service").read_text(
        encoding="utf-8"
    )
    assert "BACKGROUND_WORKER_RUNNER_ENABLED" not in engine  # defaults to true
    assert "--port 8002" in engine


def test_deploy_installs_and_health_gates_both_workers():
    deploy_script = (REPO_ROOT / "deploy" / "deploy_vps.sh").read_text(encoding="utf-8")

    assert "install -m 644 deploy/layman-nova-signal-intake.service" in deploy_script
    assert "systemctl restart layman-nova-signal-intake.service" in deploy_script
    # Both processes must read the same env file; the intake drop-in mirrors the
    # engine's resolved LAYMAN_ENV_FILE rather than hardcoding a second copy.
    assert "layman-nova-signal-intake.service.d/override.conf" in deploy_script
    assert 'Environment="LAYMAN_ENV_FILE=%s"' in deploy_script
    assert "http://127.0.0.1:8102/api/health" in deploy_script


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
