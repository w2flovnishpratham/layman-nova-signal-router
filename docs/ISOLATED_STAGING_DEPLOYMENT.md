# Isolated staging deployment

The `Deploy isolated staging` workflow is manual and deploys only a full commit SHA reachable from `phase-2b2a-read-shadow`. Configure the protected GitHub Environment `staging` with required reviewers before use.

Required environment secrets:

- `STAGING_SSH_HOST`
- `STAGING_SSH_PORT`
- `STAGING_SSH_USER`
- `STAGING_SSH_PRIVATE_KEY`
- `STAGING_SSH_HOST_KEY` (key type and base64 public key, without hostname)
- `STAGING_DEPLOY_PATH` (an isolated path containing `staging`)
- `STAGING_SERVICE_NAME` (a distinct `.service` name containing `staging`)
- `STAGING_DATABASE_URL`
- `STAGING_ENV_FILE_PATH` (outside the repository and containing `staging`)
- `STAGING_HEALTH_URL` (loopback `/api/health` endpoint on the staging port)

The deployment account needs read access to the protected environment file and repository, write access to the staging deploy/runtime/log paths, and narrowly scoped passwordless permission to restart and inspect only the staging systemd service. The host must provide Git, Python, `pg_dump`, `flock`, and `curl`.

The pre-provisioned service must use `${STAGING_DEPLOY_PATH}/current/backend` as its working directory, load `STAGING_ENV_FILE_PATH`, use a staging-only port, and keep its runtime state, logs, database, queue namespace, OAuth callback and endpoint separate from production. The environment file must include:

```env
APP_ENV=isolated_staging
ENABLE_LIVE_ORDERS=false
DHAN_MODE=MOCK
POSITION_DB_TYPED_WRITES_ENABLED=true
POSITION_DB_READ_SHADOW_ENABLED=true
POSITION_DB_READ_SHADOW_SAMPLE_RATE=1.0
STAGING_DATABASE_GUARD=isolated
RUNTIME_STATE_DIR=/absolute/staging/runtime_state
RUNTIME_LOG_DIR=/absolute/staging/runtime_logs
DATABASE_URL=<isolated PostgreSQL 16 database>
```

Adapt `deploy/nova-staging.service.example` only for staging-specific paths, account and port. Keep its `ExecStartPre` guard: the workflow installs that guard at `${STAGING_DEPLOY_PATH}/bin/deploy_staging.sh`, and every service start fails unless the isolation settings above remain active.

The workflow pins the SSH host key, runs the complete backend and frontend gates at the requested SHA, backs up staging state and PostgreSQL, installs an immutable release, migrates only the supplied staging database, restarts only the staging service, and runs the PostgreSQL verifier and parity report. A failed restart or health check restores the previous release symlink without downgrading the database. The functional emergency rollback remains `POSITION_DB_READ_SHADOW_ENABLED=false`; JSON remains execution authority.
