from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def require(text: str, token: str, message: str, failures: list[str]) -> None:
    if token not in text:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    service = read("deploy/layman-nova-signal-router.service")
    paper_worker_service = read("deploy/layman-paper-worker.service")
    nginx = read("deploy/nginx/layman-api.manyacare.com.conf")
    deploy = read("deploy/deploy_vps.sh")
    configure = read("deploy/configure_vps_env.sh")
    backup = read("deploy/backup_postgres.sh")
    logrotate = read("deploy/logrotate/layman-nova")
    excludes = read("deploy/deploy-excludes.txt")
    workflow = read(".github/workflows/layman-backend-ci-deploy.yml")

    for token in (
        "User=layman",
        "Group=layman",
        "EnvironmentFile=/etc/layman/layman.env",
        "WorkingDirectory=/opt/layman-nova-signal-router/backend",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX",
        "LockPersonality=true",
        "MemoryDenyWriteExecute=true",
        "ReadWritePaths=/var/lib/layman /var/log/layman /run/layman",
    ):
        require(service, token, f"systemd unit missing {token}", failures)
    if re.search(r"^User=root$", service, flags=re.MULTILINE):
        failures.append("backend systemd unit must not run as root")
    for token in (
        "User=layman",
        "Group=layman",
        "EnvironmentFile=/etc/layman/layman.env",
        'Environment="WORKER_ROLE=paper-worker"',
        'Environment="ENABLE_PAPER_WORKERS=true"',
        'Environment="ENABLE_LIVE_ORDERS=false"',
        'Environment="PAPER_QUEUE_INLINE_LOCAL=false"',
        "ExecStart=/opt/layman-nova-signal-router/backend/.venv/bin/python -m app.services.worker_runtime",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "MemoryDenyWriteExecute=true",
    ):
        require(paper_worker_service, token, f"paper worker systemd unit missing {token}", failures)
    if re.search(r"^User=root$", paper_worker_service, flags=re.MULTILINE):
        failures.append("paper worker systemd unit must not run as root")

    for token in (
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Content-Security-Policy",
        "zone=auth_limit",
        "zone=webhook_limit",
        "zone=api_limit",
        "limit_req_status 429",
        "location /ws/",
        "proxy_set_header Upgrade $http_upgrade",
    ):
        require(nginx, token, f"nginx configuration missing {token}", failures)

    for token in (
        "python3 scripts/check_repo_hygiene.py --worktree",
        "python3 scripts/check_deployment_hardening.py",
        "alembic upgrade head",
        "alembic check",
        "/api/readiness",
        "layman-paper-worker.service",
        'systemctl is-active --quiet "$paper_worker_service"',
    ):
        require(deploy, token, f"deploy script missing gate {token}", failures)
    if "git stash" in deploy or "checkout --force" in deploy:
        failures.append("deploy script must refuse dirty state instead of hiding or overwriting it")

    for token in (
        'env_file="$env_dir/layman.env"',
        "ENABLE_LIVE_ORDERS false",
        "ENABLE_PAPER_WORKERS false",
        "PAPER_QUEUE_INLINE_LOCAL false",
        'RUNTIME_STATE_DIR "$runtime_root/state"',
        'RUNTIME_LOG_DIR "$runtime_root/logs"',
        'chmod 600 "$env_file"',
    ):
        require(configure, token, f"environment setup missing {token}", failures)

    for token in ("pg_dump", "umask 077", "/var/backups/layman/postgres", "--format=custom"):
        require(backup, token, f"backup script missing {token}", failures)
    if re.search(
        r"(?:echo|printf).*(?:\$\{?database_url\}?|\$\{?DATABASE_URL\}?)",
        backup,
        flags=re.IGNORECASE,
    ):
        failures.append("backup script may print the database URL")

    for token in ("daily", "rotate 30", "compress", "missingok", "notifempty", "create 0600 layman layman"):
        require(logrotate, token, f"logrotate configuration missing {token}", failures)

    for token in (
        ".git/",
        "frontend/node_modules/",
        "frontend/dist/",
        "backend/runtime_state/",
        "backend/runtime_logs/",
        "client_secret*.json",
        ".env",
        "*.sqlite3",
        "*.db",
        "__pycache__/",
        ".pytest_cache/",
    ):
        require(excludes, token, f"deployment exclusion list missing {token}", failures)

    for token in (
        "bandit -r app -ll -ii",
        "pip-audit -r requirements.txt",
        "npm audit --omit=dev --audit-level=high",
        "alembic upgrade head",
        "alembic check",
        "python -m pytest app/tests -q",
        "python scripts/check_deployment_hardening.py",
        "postgres:17-alpine",
        "postgresql+psycopg://layman_ci:",
        "environment: production",
        "inputs.deploy_production == true",
        "${{ vars.VPS_HOST }}",
        "${{ vars.VPS_USER }}",
        "${{ secrets.VPS_HOST_KEY }}",
    ):
        require(workflow, token, f"CI workflow missing {token}", failures)
    if re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", workflow):
        failures.append("CI workflow contains a hardcoded IPv4 address")
    if re.search(r"^\s*User root\s*$", workflow, flags=re.MULTILINE):
        failures.append("CI workflow contains a hardcoded root SSH user")

    if failures:
        print("Deployment hardening check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("Deployment hardening check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
