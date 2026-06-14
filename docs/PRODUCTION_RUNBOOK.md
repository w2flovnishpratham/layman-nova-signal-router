# Production Runbook

Scope: authenticated multi-user Paper beta only. `ENABLE_LIVE_ORDERS=false`
is mandatory.

## Host Prerequisites

- Ubuntu LTS host with nginx, PostgreSQL client tools, Python 3.12+, Git,
  logrotate, curl, and systemd.
- Repository at `/opt/layman-nova-signal-router`.
- Dedicated service account `layman`.
- Production environment at `/etc/layman/layman.env`, mode `0600`.
- Runtime state at `/var/lib/layman/state`.
- Runtime logs at `/var/lib/layman/logs`.
- PostgreSQL reachable through `DATABASE_URL`.
- GitHub `production` environment configured with required reviewers.

For a non-root CI deploy user, allow only the canonical deploy command:

```sudoers
deploy ALL=(root) NOPASSWD: /usr/bin/bash /opt/layman-nova-signal-router/deploy/deploy_vps.sh
```

Install the restricted CI public key with:

```bash
sudo bash deploy/install_restricted_ci_key.sh /path/to/github-actions.pub deploy
```

## First-Time Setup

```bash
sudo mkdir -p /opt/layman-nova-signal-router
sudo git clone YOUR_REPOSITORY_URL /opt/layman-nova-signal-router
cd /opt/layman-nova-signal-router
sudo python3 -m venv backend/.venv
sudo backend/.venv/bin/pip install -r backend/requirements.txt
sudo bash deploy/configure_vps_env.sh /opt/layman-nova-signal-router
sudoedit /etc/layman/layman.env
```

Set PostgreSQL, Google OAuth, explicit `ADMIN_EMAILS`, session secret, and
Fernet key. Confirm:

```env
APP_ENV=production
AUTH_REQUIRED=true
ENABLE_LIVE_ORDERS=false
EXECUTION_NODE_ROUTING_ENABLED=false
WORKER_ROLE=web
ENABLE_TRADING_WORKERS=false
DEBUG_ENABLED=false
RUNTIME_STATE_DIR=/var/lib/layman/state
RUNTIME_LOG_DIR=/var/lib/layman/logs
```

Never add Dhan access tokens or webhook secrets to the environment file.
Users enter them through the authenticated application.

## Deploy

Automatic tests run on pushes and pull requests. Production deploy requires:

1. A successful main-branch revision.
2. Manual workflow dispatch with `deploy_production=true`.
3. Approval through the protected GitHub `production` environment.

Manual equivalent:

```bash
sudo bash /opt/layman-nova-signal-router/deploy/deploy_vps.sh
```

The script refuses a dirty checkout, scans tracked and physical sensitive
files, runs deployment-policy checks, applies migrations, validates nginx and
logrotate, restarts one web worker, and requires `/api/readiness` to pass.

## Verify

```bash
sudo systemctl status layman-nova-signal-router --no-pager
sudo systemctl status nginx --no-pager
sudo nginx -t
curl -I https://layman-api.manyacare.com/health
curl --fail https://layman-api.manyacare.com/api/readiness
sudo journalctl -u layman-nova-signal-router -n 100 --no-pager
```

Readiness must report:

- database and migrations `ok`;
- vault and runtime directories `ok`;
- auth and worker policy `ok`;
- Live policy `disabled`;
- debug `disabled`.

## Restart

```bash
sudo systemctl restart layman-nova-signal-router
curl --fail https://layman-api.manyacare.com/api/readiness
```

## Rollback

1. Identify the previous tested commit.
2. Stop onboarding and keep Live disabled.
3. Check whether the release includes a database migration.
4. Restore code:

   ```bash
   cd /opt/layman-nova-signal-router
   sudo git fetch origin
   sudo git checkout --detach PREVIOUS_TESTED_COMMIT
   sudo backend/.venv/bin/pip install -r backend/requirements.txt
   sudo systemctl restart layman-nova-signal-router
   ```

5. Verify health/readiness and logs.
6. Do not downgrade the database unless the migration has a reviewed downgrade
   path and a fresh backup exists.
7. Return the checkout to `main` before the next standard deployment.

## Environment Secret Rotation

```bash
sudo systemctl stop layman-nova-signal-router
sudoedit /etc/layman/layman.env
sudo chown layman:layman /etc/layman/layman.env
sudo chmod 600 /etc/layman/layman.env
sudo systemctl start layman-nova-signal-router
curl --fail https://layman-api.manyacare.com/api/readiness
```

Rotating `SESSION_TOKEN_SECRET` invalidates sessions. Rotating
`TOKEN_ENCRYPTION_KEY` without re-encrypting stored credentials makes the
vault unreadable.

## Disable Live Trading

Live is already disabled. During any incident, enforce it again:

```bash
sudo sed -i 's/^ENABLE_LIVE_ORDERS=.*/ENABLE_LIVE_ORDERS=false/' /etc/layman/layman.env
sudo systemctl restart layman-nova-signal-router
curl --fail https://layman-api.manyacare.com/api/readiness
```

## Emergency Kill Switch

Use the authenticated UI's confirmed stop/square-off control for tracked
Paper state. If the app is unhealthy, stop the service:

```bash
sudo systemctl stop layman-nova-signal-router
```

This stage is not approved for real broker orders. If real orders ever exist
despite policy, use the Dhan portal directly and start an incident review.

## Paper Beta User Onboarding

1. Add the exact email to `ADMIN_EMAILS`.
2. Restart and verify readiness.
3. Ask the user to sign in with Google.
4. Confirm the persistent banner states Paper and no real orders.
5. Connect Dhan only if real market-data access is required.
6. Configure risk limits and a webhook secret.
7. Confirm the Live checklist remains blocked.
8. Review per-user audit/log behavior without exposing another user's data.

## Backups and Monitoring

- [Backup and restore](PRODUCTION_BACKUP_AND_RESTORE.md)
- [Production monitoring](PRODUCTION_MONITORING.md)

## Do Not

- Do not set `ENABLE_LIVE_ORDERS=true`.
- Do not run the backend as root.
- Do not put `.env`, runtime state, logs, databases, OAuth JSON, or backups in
  the repository.
- Do not bypass migrations, readiness, CI scans, or GitHub approval.
- Do not run multiple web/trading workers against JSON-backed trading state.
- Do not deploy Redis, pub-sub, signing relay, or executor routing as part of
  this stage.
