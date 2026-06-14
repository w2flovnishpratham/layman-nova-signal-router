# Stage 4 Deployment Hardening Applied

Date: 2026-06-13

## 1. Summary

Stage 4 hardens the authenticated multi-user Paper beta deployment with a
non-root backend service, external production environment/runtime paths,
nginx security controls, safe readiness checks, log rotation, scheduled
PostgreSQL backups, CI security gates, explicit production approval, package
hygiene, monitoring guidance, and an operator runbook.

Live trading remains disabled. This stage did not implement Redis, pub-sub,
distributed trading state, a signing relay, executor nodes, or per-user
egress routing.

`ENABLE_LIVE_ORDERS=false` remains the default in code, environment templates,
VPS configuration, documentation, tests, and readiness policy.

## 2. Files Changed

This table lists Stage 4-specific changes. Existing uncommitted Stage 0-3
changes remain in the same clean checkout.

| File | Change | Reason |
| --- | --- | --- |
| `backend/app/config.py` | Added external production env-file validation, `0600` enforcement, and loopback bind default. | Reject repo-local secrets and unsafe production defaults. |
| `backend/app/main.py` | Added debug boot guard, environment-file guard, minimal health, and readiness route. | Fail closed and expose safe probes. |
| `backend/app/middleware/user_scope.py` | Added `/api/readiness` to the public non-secret probe allowlist. | Permit infrastructure health checks without a user session. |
| `backend/app/services/readiness.py` | Added database, Alembic, vault, directory, auth, Live, worker, and debug checks. | Return 503 when Paper-beta dependencies or policies are unsafe. |
| `backend/app/services/dhan_client.py` | Documented the intentional outbound IPv4 wildcard bind for Bandit. | Keep SAST strict without misclassifying an outbound source bind. |
| `backend/app/routers/safety.py` | Clarified later Live-routing capability status. | Keep operator safety language accurate after Stage 4. |
| `backend/app/tests/test_stage_4_deployment_hardening.py` | Added readiness, safe-response, 503, env-path, permission, debug, and Live-block tests. | Protect Stage 4 backend behavior. |
| `backend/.env.example` | Switched to loopback/port 8002, removed personal email, documented external production env. | Prevent copying secrets into the checkout. |
| `backend/.env.live.example` | Marked controlled testing only and removed personal email. | Prevent template misuse. |
| `deploy/layman-nova-signal-router.service` | Replaced root service with hardened `layman` service and restricted writes. | Limit backend compromise impact. |
| `deploy/nginx/layman-api.manyacare.com.conf` | Added headers, CSP, 429 rate limits, body cap, and separate websocket handling. | Harden the public API edge without breaking upgrades. |
| `deploy/configure_vps_env.sh` | Creates `layman`, external env/runtime/backup directories, safe defaults, and permissions. | Keep secrets and mutable state outside code. |
| `deploy/deploy_vps.sh` | Refuses dirty state, runs hygiene/policy/migration checks, installs host controls, and requires readiness. | Prevent unsafe or incomplete production releases. |
| `scripts/deploy_vps.sh` | Replaced the divergent legacy deploy flow with a canonical wrapper. | Keep one production deployment path. |
| `deploy/enable_layman_api_tls.sh` | Removed hardcoded VPS IP and requires an operator-supplied expected IP. | Avoid stale infrastructure identity. |
| `deploy/install_restricted_ci_key.sh` | Supports a restricted non-root deploy user and forced canonical command. | Reduce SSH deployment privileges. |
| `deploy/logrotate/layman-nova` | Added daily compressed rotation with 30 retained files and `0600` creation. | Bound log growth and protect log contents. |
| `deploy/backup_postgres.sh` | Added non-verbose compressed `pg_dump`, checksums, safe permissions, and retention. | Provide recoverable database backups outside the repository. |
| `deploy/layman-postgres-backup.service` | Added hardened non-root backup job. | Run backups with restricted access. |
| `deploy/layman-postgres-backup.timer` | Added persistent daily backup schedule. | Automate backup execution. |
| `deploy/deploy-excludes.txt` | Added explicit sensitive/generated deployment exclusions. | Document package boundaries for any optional sync process. |
| `.github/workflows/layman-backend-ci-deploy.yml` | Split security jobs, added SAST/audits/migrations, and made production deploy dispatch+approval only. | Gate releases and remove hardcoded VPS identity. |
| `scripts/check_deployment_hardening.py` | Added static validation of systemd, nginx, backup, rotation, package, and CI policy. | Make infrastructure controls testable in CI. |
| `scripts/check_repo_hygiene.py` | Added physical real-env-file detection for strict worktree scans. | Reject production secrets inside the checkout. |
| `docs/API_CONTRACT.md` | Documented minimal health and non-secret readiness behavior. | Define probe contracts. |
| `docs/PRODUCTION_BACKUP_AND_RESTORE.md` | Added backup, off-host copy, restore drill, and production restore procedure. | Make database recovery executable. |
| `docs/PRODUCTION_MONITORING.md` | Added minimum checks, alerts, and operator commands. | Establish Paper-beta monitoring basics. |
| `docs/PRODUCTION_RUNBOOK.md` | Added setup, deploy, verify, rollback, restart, rotation, incident, and onboarding procedures. | Remove operator guesswork. |
| `docs/DEPLOY_BACKEND_VPS.md` | Replaced obsolete repo-local deployment instructions with the canonical runbook. | Prevent unsafe legacy deployment. |
| `docs/PRODUCTION_CHECKLIST.md` | Replaced Live enablement checklist with Paper-beta acceptance checks. | Match the current launch approval. |
| `docs/DHAN_V2_COMPLIANCE_AUDIT.md` | Marked the older audit historical and removed its Live enablement step. | Prevent conflicting operational guidance. |

## 3. Systemd Hardening

- Backend process runs as `User=layman` and `Group=layman`.
- Code is read from `/opt/layman-nova-signal-router/backend`.
- Environment is loaded from `/etc/layman/layman.env`.
- Runtime paths are `/var/lib/layman/state` and `/var/lib/layman/logs`.
- Systemd also provisions restricted runtime, state, and log directories.
- Explicit writes are limited to `/var/lib/layman`, `/var/log/layman`, and
  `/run/layman`.
- Hardening includes `NoNewPrivileges`, private temp/devices,
  `ProtectSystem=strict`, `ProtectHome`, kernel/control-group protection,
  restricted address families, no capabilities, personality locking, and
  write/execute memory denial.
- Uvicorn is fixed to one web worker on loopback. Authenticated trading workers
  remain disabled because JSON-backed trading state is not distributed-safe.

## 4. Nginx Hardening

The API TLS server now sends:

- HSTS for one year with subdomains;
- `X-Content-Type-Options: nosniff`;
- `X-Frame-Options: DENY`;
- `Referrer-Policy: no-referrer`;
- a restrictive permissions policy;
- API-only CSP: no default content, frames, or base URI.

Rate-limit zones apply conservative limits to auth, webhook, and general API
traffic and return HTTP 429. Websocket traffic has a separate location with
HTTP/1.1 upgrade headers and no request-rate rule that would terminate a live
connection.

TLS certificates remain managed by Certbot. The TLS helper now validates DNS
against an operator-supplied expected IP instead of a hardcoded address.

## 5. Readiness and Health

`GET /health` and `GET /api/health` are lightweight liveness probes returning
only `{"status":"ok"}`.

`GET /api/readiness` checks:

- database connectivity;
- database Alembic revision equals repository head;
- Fernet credential vault configuration;
- state and log directories exist and are writable;
- production authentication is required;
- Live orders are disabled;
- worker role is `web` and trading workers are disabled;
- debug is disabled.

The endpoint returns HTTP 503 when a required check fails. It exposes only
categorical status. It does not expose paths, URLs, database configuration,
environment values, secrets, tokens, cookies, or user data.

Production boot additionally rejects a repo-local/root env path, a missing
external env file, non-`0600` permissions on Linux, repo-local/root runtime
paths, enabled debug, or unsafe Live prerequisites.

## 6. Logs and Backups

Logrotate covers `/var/lib/layman/logs/*.jsonl` and
`/var/log/layman/*.log`, rotates daily, keeps 30 rotations, compresses, ignores
missing/empty logs, and creates new files as `0600 layman:layman`.

`deploy/backup_postgres.sh`:

- reads `DATABASE_URL` from the process or `/etc/layman/layman.env`;
- never prints the password or URL;
- writes custom compressed PostgreSQL archives;
- stores them in `/var/backups/layman/postgres`;
- uses `0700` directory and `0600` files;
- creates SHA-256 checksums;
- retains the latest 14 backups by default;
- exits non-zero on configuration, tool, or dump failure.

A non-root systemd service/timer schedules daily backups. The restore document
requires an isolated restore drill and recommends an encrypted off-host copy.

## 7. CI/CD Security

Automatic push and pull-request checks now include:

- Bandit medium-or-higher severity/confidence SAST;
- pip-audit for backend requirements;
- repository hygiene;
- Alembic upgrade and schema-drift check;
- full backend tests and compileall;
- npm production dependency audit;
- frontend API/UI security checks, lint, and production build;
- deployment hardening checks and shell syntax checks.

Production deployment does not run on ordinary pushes. It requires:

1. explicit workflow dispatch with `deploy_production=true`;
2. a tested `main` revision;
3. all security jobs passing;
4. approval through the protected GitHub `production` environment.

VPS host, port, user, application path, SSH host key, and private key use
GitHub variables/secrets. No production IP or root username is hardcoded in
the workflow.

## 8. Deployment Hygiene

The canonical deploy script:

- refuses a dirty or untracked production checkout;
- never stashes or force-overwrites local state;
- runs tracked-file and physical-worktree hygiene checks before and after a
  fast-forward-only pull;
- runs deployment-policy and shell checks before restart;
- uses a Git checkout rather than copying arbitrary local folders;
- excludes or rejects `.git`, node modules, frontend build output, runtime
  state/logs, OAuth JSON, real env files, SQLite/DB files, caches, and Python
  bytecode from optional packages;
- keeps environment, runtime data, logs, and backups outside the repository.

## 9. Monitoring and Runbook

`docs/PRODUCTION_MONITORING.md` defines minimum checks and alert conditions for
readiness, database availability, auth/webhook failures, unknown secrets, disk
usage, blocked routes, Dhan failures, unexpected Live/debug enablement,
missing future egress, and failed backups.

`docs/PRODUCTION_RUNBOOK.md` covers:

- first-time host setup;
- approved deployment and verification;
- restart and rollback;
- nginx/service/readiness/log inspection;
- secret rotation;
- forced Live disablement;
- emergency service stop;
- backup/restore links;
- Paper-beta user onboarding;
- prohibited operations.

## 10. Tests Added

`backend/app/tests/test_stage_4_deployment_hardening.py` covers:

- minimal non-secret health;
- public readiness allowlist;
- readiness HTTP 503 behavior;
- healthy categorical readiness response;
- database, migration, vault, and runtime failure behavior;
- repo-local and filesystem-root env rejection;
- Linux env-file `0600` enforcement;
- production debug rejection;
- continued Live/signing/egress blocking.

`scripts/check_deployment_hardening.py` validates:

- non-root hardened systemd policy;
- nginx headers, limits, 429 behavior, and websocket directives;
- deploy hygiene/migration/readiness gates;
- external env/runtime defaults;
- backup and logrotate controls;
- sensitive package exclusions;
- CI SAST, dependency, migration, test, and approval gates;
- absence of hardcoded production IPv4 identity.

## 11. Verification Results

Run on 2026-06-13:

| Command | Result |
| --- | --- |
| `cd backend && python -m pytest app/tests -q` | Passed: `245 passed, 1 skipped in 80.37s`. The skip is the Windows run of a POSIX permission test. |
| Stage 4 focused tests | Passed: `11 passed, 1 skipped in 2.24s`. |
| `python -m alembic upgrade head` | Passed. |
| `python -m alembic check` | Passed: no new upgrade operations detected. |
| Python source compilation | Passed: `99 Python files compiled`. |
| `python scripts/check_repo_hygiene.py` | Passed. |
| `python scripts/check_deployment_hardening.py` | Passed. |
| Required deploy shell `bash -n` checks | Passed for deploy, env configuration, backup, TLS, and restricted-key scripts. |
| `bandit -r app -ll -ii` | Passed: no issues identified; one documented `B104` suppression for the intentional outbound IPv4 source bind. |
| `pip-audit -r requirements.txt` | Passed: no known vulnerabilities found. |
| `npm audit --omit=dev --audit-level=high` | Passed: zero vulnerabilities. |
| `npm run test:security` | Passed: frontend API and UI safety checks. |
| `npm run lint` | Passed. |
| `npm run build` | Passed: Vite 8.0.16, 2,172 modules, built in 6.87s. |
| `git diff --check` | Passed; Windows LF-to-CRLF notices only. |

`python -m compileall app` was attempted but could not write several inherited
Windows-owned `__pycache__` files in the temporary checkout. The source-only
compile check successfully compiled all 99 Python files. CI runs compileall in
a clean Linux checkout where those inherited permissions do not exist.

The following require the real Linux VPS and were not claimed as locally
verified:

- `systemd-analyze verify` and actual non-root service startup;
- `nginx -t`, TLS header inspection, controlled 429 tests, and websocket
  upgrade testing;
- readiness against the real PostgreSQL database and production vault;
- backup timer execution, off-host copy, and full restore drill;
- external alert delivery.

## 12. Remaining Risks

- Public Live launch is not approved.
- Single trusted operator Live remains blocked until a signing relay and
  executor/per-user egress routing are deployed and verified.
- Stage 5 pub-sub and state/worker scaling are still required.
- Stage 6 Live executor/IP routing is still required.
- Trading runtime state remains partly JSON-backed, so the backend remains
  fixed to one web worker with authenticated trading workers disabled.
- Nginx rate limits are per IP and local to one edge host; distributed limits
  require later shared infrastructure.
- Backups, restore duration, alerting, systemd restrictions, and nginx limits
  must be tested on the real VPS before accepting the Paper beta.
- Local VPS backups require a protected off-host copy to survive total host
  loss.

## 13. Go / No-Go Status

| Launch target | Decision | Conditions |
| --- | --- | --- |
| Multi-user Paper beta | Conditional GO | Apply Stage 4 on the real VPS, pass readiness/nginx/systemd/backup/restore/monitoring checks, keep one web worker, and keep Live disabled. |
| Single trusted operator Live | NO-GO | Signing relay and verified executor/per-user egress routing are missing. |
| Public Live launch | NO-GO | Live routing, distributed state, worker coordination, and production validation are incomplete. |
| 100-user production launch | NO-GO | Pub-sub, shared state, worker scaling, load/chaos tests, and operational proof are incomplete. |

Next recommended stage: **Stage 5A - Strategy Subscription + Paper Signal Fanout**
