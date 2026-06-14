# Authenticated Multi-User Paper Beta Checklist

## Required Before Deploy

- [ ] CI backend security, dependency, migration, and test gates pass.
- [ ] CI frontend audit, security, lint, and build gates pass.
- [ ] Deployment-policy checks and shell syntax checks pass.
- [ ] GitHub `production` environment requires reviewer approval.
- [ ] Repository is `/opt/layman-nova-signal-router` and clean.
- [ ] Backend systemd service runs as `layman`, not root.
- [ ] `/etc/layman/layman.env` is owned by `layman` with mode `0600`.
- [ ] Runtime state/logs are outside the repository.
- [ ] PostgreSQL backup timer is enabled.
- [ ] Nginx TLS, headers, rate limits, and websocket upgrade validate.

## Required Policy

- [ ] `APP_ENV=production`
- [ ] `AUTH_REQUIRED=true`
- [ ] `ENABLE_LIVE_ORDERS=false`
- [ ] `EXECUTION_NODE_ROUTING_ENABLED=false`
- [ ] `WORKER_ROLE=web`
- [ ] `ENABLE_TRADING_WORKERS=false`
- [ ] `DEBUG_ENABLED=false`
- [ ] `WEBHOOK_HMAC_REQUIRED=true`
- [ ] `WEBHOOK_ALLOW_LEGACY_AUTH_LOCAL=false`

## Post-Deploy

- [ ] `/health` returns 200.
- [ ] `/api/readiness` returns 200 and reports every dependency/policy ready.
- [ ] Security headers appear in `curl -I`.
- [ ] Websocket setup flow connects.
- [ ] Excessive auth/webhook requests return 429 during a controlled test.
- [ ] Logs contain no access tokens, cookies, database URLs, or webhook secrets.
- [ ] Manual PostgreSQL backup completes and checksum verifies.
- [ ] Restore drill has a recorded successful result.
- [ ] External readiness and backup-failure alerts are active.

## Launch Decision

- Multi-user Paper beta: conditional GO after every item above passes on the
  real VPS.
- Single-operator Live: NO-GO.
- Public Live: NO-GO.
- 100-user production: NO-GO.

See [PRODUCTION_RUNBOOK.md](PRODUCTION_RUNBOOK.md).
