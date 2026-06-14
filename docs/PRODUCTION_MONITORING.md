# Production Monitoring

Minimum monitoring for the authenticated multi-user Paper beta should poll
readiness externally and collect systemd, nginx, PostgreSQL, and NOVA audit
events. Live trading remains disabled.

## Minimum Checks

```bash
curl --fail --silent https://layman-api.manyacare.com/health
curl --fail --silent https://layman-api.manyacare.com/api/readiness
sudo systemctl is-active layman-nova-signal-router
sudo systemctl is-active nginx
sudo journalctl -u layman-nova-signal-router -n 100 --no-pager
sudo nginx -t
df -h /var/lib/layman /var/backups/layman
```

Run these checks before and after every deployment. Use an external uptime
monitor for `/api/readiness`; alert on any non-200 response.

## Required Alerts

| Signal | Suggested trigger | Action |
| --- | --- | --- |
| Readiness failing | 2 consecutive checks | Inspect service, DB, migrations, vault, directories, and policy fields. |
| Database unavailable | Any readiness failure for database | Stop onboarding and restore connectivity before continuing. |
| Auth failures spike | Sustained increase above normal baseline | Check OAuth configuration, hostile traffic, and session expiry. |
| Webhook auth failures spike | More than 10/minute | Check relay/client configuration and possible replay attempts. |
| Unknown webhook secret spike | More than 5/minute | Investigate leaked/incorrect URLs and rotate affected secret. |
| Disk usage high | 80% warning, 90% critical | Check logs, backups, database growth, and rotation. |
| Order route blocked spike | Sudden increase | Confirm Paper policy and inspect malformed/risk-blocked signals. |
| Dhan API failures spike | More than 5% over 5 minutes | Pause user onboarding and inspect broker availability/tokens. |
| Live unexpectedly enabled | Any occurrence | Set `ENABLE_LIVE_ORDERS=false`, restart, and begin incident review. |
| Debug unexpectedly enabled | Any occurrence | Set `DEBUG_ENABLED=false`, restart, and review configuration access. |
| Executor/egress missing | Any future Live request | Keep Live blocked; Stage 6 routing is not deployed. |
| Backup failed | Any timer failure or no backup in 26 hours | Inspect timer logs and run a manual backup. |

## Useful Queries

```bash
sudo journalctl -u layman-nova-signal-router --since "15 minutes ago"
sudo journalctl -u nginx --since "15 minutes ago"
sudo journalctl -u layman-postgres-backup.service -n 100 --no-pager
sudo grep -R '"WEBHOOK_AUTH_FAILED"\|"WEBHOOK_UNKNOWN_SECRET"' /var/lib/layman/logs
sudo grep -R '"ORDER_ROUTE_BLOCKED"' /var/lib/layman/logs
```

Do not send raw environment files, database URLs, cookies, access tokens, or
webhook secrets to a monitoring vendor.
