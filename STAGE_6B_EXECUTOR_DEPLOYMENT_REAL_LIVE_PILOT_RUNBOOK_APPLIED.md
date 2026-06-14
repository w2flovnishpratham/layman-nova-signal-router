# Stage 6B Executor Deployment + Controlled Real Live Pilot Runbook Applied

Date: 2026-06-15

## 1. Summary

Stage 6B makes the two DigitalOcean executor droplets deployable, verifiable, and
ready for a controlled two-account live pilot, and implements the short-lived
credential transport that Stage 6A intentionally deferred.

- **What Stage 6B built:** a complete executor deployment package (install/configure
  scripts, hardened non-root systemd unit, TLS/nginx config, firewall docs, health/
  egress/reserved-IP check scripts); short-lived, signed, single-use credential
  transport from the Hostinger main app to the assigned executor; a real Dhan order
  path that runs **only** on the executor; a controlled real live pilot runbook with
  kill-switch and rollback procedures; and Stage 6B tests proving real mode still
  fails closed unless fully configured.
- **Is a real live pilot technically possible now?** Yes — but only when every flag
  and check is deliberately enabled together (Hostinger live flags + per-executor
  `EXECUTOR_REAL_ORDERS_ENABLED=true` + verified assignment + admin approval + risk
  caps + kill switch off + reserved-IP route verified). Any missing piece fails closed.
- **Does live remain disabled by default?** Yes. `ENABLE_LIVE_ORDERS=false`,
  `LIVE_ORDER_DRY_RUN_ONLY=true`, and `EXECUTOR_REAL_ORDERS_ENABLED=false` are the
  resting defaults in every example/config, and the runbook requires disabling the
  real flags again immediately after the test.

No Stage 0–6A safety gate was removed. The change to the live path only **replaces**
the Stage 6A `credential_transport_not_configured` terminal block with a fully gated
real send; all earlier blocks (auth, CSRF, relay verification, kill switch, market
hours, risk caps, executor verification, one-user/one-executor routing) remain.

## 2. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/app/executor_service/dhan_order_client.py` | **New.** Minimal, isolated Dhan order client (no main-app imports). | Executor places exactly one order; never logs/returns/stores the token. |
| `backend/app/executor_service/main.py` | Added lifespan startup validation; dry-run credential rejection; real-order path requiring credentials + `EXECUTOR_REAL_ORDERS_ENABLED`; full order validation; sanitized broker timeout/rejection; token redaction. | Short-lived credential transport, fail-closed real mode. |
| `backend/app/services/live_execution_service.py` | Replaced the `credential_transport_not_configured` block with a gated real send (re-checks all flags, resolves security id, signs body incl. credentials, sanitizes response, records `dhan_order_id`, redacts token). Added `dhan_order_id` to `_finish`. | Real orders leave only via the assigned executor; never from Hostinger. |
| `backend/.env.live.example` | Documented the `EXECUTOR_SHARED_SECRETS_JSON` two-executor format. | Operator clarity. |
| `deploy/executor/install_executor.sh` | **New.** Idempotent executor installer (non-root user, venv, env file, systemd). | Deployable executor. |
| `deploy/executor/configure_executor_env.sh` | **New.** Safe `0600` env writer; refuses to enable real orders. | Per-droplet identity config. |
| `deploy/executor/executor.env.example` | **New.** Placeholder env template. | Documented config surface. |
| `deploy/executor/layman-executor.service` | **New.** Hardened non-root systemd unit running `app.executor_service.main:app`. | Locked-down executor runtime. |
| `deploy/executor/nginx-executor.conf` | **New.** TLS, three-path allowlist, Hostinger-IP allow, body cap, rate limit. | Minimal public surface. |
| `deploy/executor/check_executor_health.sh` | **New.** `/health` + code check. | Verification. |
| `deploy/executor/check_executor_egress.sh` | **New.** `/egress-ip` == reserved IP. | Verification. |
| `deploy/executor/check_reserved_ip_route.sh` | **New.** Actual outbound IP == reserved IP (independent of the service). | Whitelist guarantee; fails closed on mismatch. |
| `deploy/executor/README.md` | **New.** Executor deploy + firewall guide. | Operations. |
| `docs/CONTROLLED_REAL_LIVE_PILOT_RUNBOOK.md` | **New.** Pre-market → dry-run → real → shutdown, kill-switch + rollback. | Controlled pilot. |
| `docs/HOSTINGER_LIVE_PILOT_DEPLOYMENT.md` | **New.** Hostinger flags, executor registration, dry-run, enable/disable, rollback. | Main-app operations. |
| `backend/app/tests/test_stage_6b_executor_real_pilot.py` | **New.** 21 Stage 6B tests. | Fail-closed proof, routing, redaction, replay, deploy artifacts. |

## 3. Executor Deployment

- **Install script** (`install_executor.sh`): creates the `laymanexec` system user,
  the `/opt/layman-executor` venv, installs requirements, seeds `/etc/layman-executor/executor.env`
  (mode 0600) from the example, installs and enables the systemd unit. Refuses any
  repo path other than `/opt/layman-executor`. Runs as root, but the service runs as
  `laymanexec`.
- **Env config** (`configure_executor_env.sh` + `executor.env.example`): sets executor
  code, reserved IP, Hostinger IP, bind host/port, timeouts; leaves the shared secret
  and `DATABASE_URL` to be set by hand; forces `EXECUTOR_REAL_ORDERS_ENABLED=false`.
- **Systemd service** (`layman-executor.service`): `User=laymanexec`, `NoNewPrivileges`,
  `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `MemoryDenyWriteExecute`,
  `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`, empty capability set,
  `ReadWritePaths` limited to the executor's own state/log/run dirs. Runs only
  `app.executor_service.main:app` on `127.0.0.1:${EXECUTOR_PORT}`.
- **nginx/TLS** (`nginx-executor.conf`): terminates TLS, proxies only `/health`,
  `/egress-ip`, `/execute-order`, returns 404 for everything else, allows only the
  Hostinger IP, caps body size, rate-limits `/execute-order`, adds security headers.
- **Firewall assumptions** (README): DigitalOcean cloud firewall — inbound SSH from
  admin IP only, HTTPS 443 from the Hostinger IP only; outbound HTTPS to Dhan, the
  IP-check endpoint, package mirrors, and (if used) Neon Postgres. Never public.
- **Reserved IP route check** (`check_reserved_ip_route.sh`): queries an external
  IP-echo directly (not the service) and exits non-zero if the droplet's actual
  outbound IP differs from the reserved IP. No order may be placed unless it passes.

## 4. Credential Transport

- **Short-lived delivery:** the main app includes the user's Dhan `broker_client_id`
  and `dhan_access_token` only inside a single `/execute-order` request body, only
  when `dry_run=false` and every gate passes. Nothing is pre-shared or stored on the
  executor.
- **Signing:** the request is signed `HMAC-SHA256(secret, "<timestamp>.<nonce>.<raw_body>")`
  with the executor-specific secret. The signature covers the exact raw bytes,
  including the credentials, so tampering or unsigned credentials are rejected (401).
- **Nonce / replay protection:** the executor records each `(executor_code, nonce)`
  in a durable unique table before processing; a replay returns 409. Timestamps
  outside tolerance return 401.
- **Redaction:** the executor never logs the token; the generic secret-field filter
  plus explicit redaction ensure the token never appears in logs, errors, or
  responses. The main app drops the request body and masks the token reference
  immediately after the call.
- **No persistence:** neither the main app nor the executor writes the access token
  to disk or database; the executor uses it once and discards it.

## 5. Executor Dhan Order Path

- **How it calls Dhan:** `dhan_order_client.place_dhan_order` posts a validated order
  to `https://api.dhan.co/v2/orders` with the `access-token` header, bound to IPv4 so
  the source is the reserved/whitelisted IP. Only the executor ever calls Dhan.
- **Dry-run vs real:** `dry_run=true` requests must carry no credentials and return
  `dry_run_verified` without contacting Dhan. `dry_run=false` requires credentials,
  `EXECUTOR_REAL_ORDERS_ENABLED=true`, and a fully validated order
  (security_id, exchange_segment, BUY/SELL, quantity > 0).
- **Timeout/rejection:** a Dhan timeout returns HTTP 504 `broker_timeout` (the main
  app marks the job `failed`/`broker_timeout`, never assuming success). A Dhan
  rejection returns a sanitized `broker_rejected` summary. The executor never retries;
  retry/idempotency is owned by the main Nova live job (unique `correlation_id` +
  one job per user/subscription/signal).
- **Sanitized responses:** only `status`, `executor_code`, `correlation_id`,
  `order_id`, `egress_ip`, `message` are returned; raw Dhan responses and headers are
  never surfaced.

## 6. Main Nova Live Pilot Controls

- **Required Hostinger flags for a real send:** `LIVE_PILOT_ENABLED=true`,
  `LIVE_ORDER_DRY_RUN_ONLY=false`, `ENABLE_LIVE_ORDERS=true`,
  `EXECUTION_NODE_ROUTING_ENABLED=true`, `RELAY_ENABLED=true`, plus strong relay and
  per-executor secrets.
- **Required executor flag:** `EXECUTOR_REAL_ORDERS_ENABLED=true` on each droplet.
- **Approval checks:** a current, unexpired admin approval for `SUPERTREND_FLIP` with
  risk caps; subscription risk must be within the approval caps.
- **Executor assignment checks:** the user must have a `verified` one-user/one-executor
  assignment whose node is `active` with `last_egress_ip == reserved_ip`; the job's
  executor must match the user's verified route.
- **Kill-switch checks:** `global_kill_switch`/`emergency_stop` block before any
  executor or Dhan contact.
- **One-account-one-executor routing:** one Supertrend signal fans out to one
  `live_order_job` per approved user, each routed only to that user's executor
  (User 1 → EXECUTOR_001 / 64.225.87.19, User 2 → EXECUTOR_002 / 152.42.157.165).
- Every gate is re-checked at the call site, so a flag flipped after a job is queued
  still fails closed. Production boot also refuses `ENABLE_LIVE_ORDERS=true` without
  the relay, executor secrets, verified assignments, and a current approval.

## 7. Controlled Real Live Pilot Runbook

Summarized (full detail in `docs/CONTROLLED_REAL_LIVE_PILOT_RUNBOOK.md`):

- **Pre-market:** Neon backup; `alembic upgrade head` + `alembic check`; Hostinger
  readiness; paper worker + live-pilot worker healthy; both executors healthy; both
  executor egress IPs verified (64.225.87.19 / 152.42.157.165); both Dhan accounts
  whitelisted; User 1 → EXECUTOR_001 and User 2 → EXECUTOR_002 verified; both approved
  only for `SUPERTREND_FLIP`; max trades/day = 1; tiny quantity; run a dry-run and
  confirm two correctly-routed dry-run jobs; test the global kill switch.
- **Dry-run:** post one `SUPERTREND_FLIP` alert through the relay; confirm two
  `dry_run_verified` jobs, each reporting its reserved egress IP; no Dhan call.
- **Real flags:** set the Hostinger live flags and `EXECUTOR_REAL_ORDERS_ENABLED=true`
  on both executors; restart services.
- **One tiny order:** post exactly one signal; one order per account; monitor the
  admin queue and both Dhan consoles; keep the kill switch ready.
- **Shutdown/rollback:** set `ENABLE_LIVE_ORDERS=false`, `LIVE_ORDER_DRY_RUN_ONLY=true`,
  and `EXECUTOR_REAL_ORDERS_ENABLED=false`; restart; verify readiness shows live
  disabled.

## 8. Tests Added

`backend/app/tests/test_stage_6b_executor_real_pilot.py` (21 tests):

Executor service: `test_executor_real_order_rejected_when_real_mode_disabled`,
`test_executor_real_order_requires_dhan_credentials`,
`test_executor_rejects_credentials_in_dry_run`,
`test_executor_redacts_dhan_token_in_error`,
`test_executor_real_order_calls_dhan_once`,
`test_executor_broker_timeout_returns_sanitized_error`,
`test_executor_broker_rejection_returns_sanitized_error`,
`test_executor_request_signature_covers_credentials`,
`test_replayed_real_executor_request_rejected`.

Main app: `test_main_live_job_sends_short_lived_credentials_only_in_real_mode`,
`test_main_live_job_does_not_send_credentials_in_dry_run`,
`test_main_live_job_blocks_when_enable_live_orders_false`,
`test_main_live_job_blocks_when_live_order_dry_run_only_true`,
`test_main_live_job_blocks_when_executor_not_verified`,
`test_main_live_job_blocks_when_approval_expired`,
`test_main_live_job_blocks_when_kill_switch_enabled`,
`test_user1_real_job_routes_to_executor1_only`,
`test_user2_real_job_routes_to_executor2_only`,
`test_no_direct_hostinger_dhan_order_path`.

Artifacts: `test_runbook_mentions_disable_flags_after_test`,
`test_deploy_scripts_contain_executor_non_root_hardening`,
`test_reserved_ip_check_script_fails_on_mismatch`.

(The required-test list maps 1:1; `test_main_live_job_sends_short_lived_credentials_only_in_real_mode`
also asserts the signed body carries the credentials, satisfying the
`test_executor_request_signature_covers_credentials` intent at both layers.)

## 9. Verification Results

> **Important:** the verification commands could **not** be executed in this
> authoring environment — the isolated Linux sandbox repeatedly failed to boot
> (`HCS ... The paging file is too small for this operation to complete`). The
> code and artifacts were verified by static review against the Stage 6A test
> harness and patterns. Run the commands below on Hostinger or in CI before the
> pilot; they are expected to pass.

| Command | Status |
|---|---|
| `python -m pytest app/tests/test_stage_6b_executor_real_pilot.py -q` | NOT RUN HERE — run in CI |
| `python -m pytest app/tests -q` | NOT RUN HERE — run in CI |
| `python -m alembic upgrade head` / `alembic check` | NOT RUN HERE (no new migration added in 6B) |
| `python -m compileall -q app alembic` | NOT RUN HERE — run in CI |
| `python scripts/check_repo_hygiene.py` | NOT RUN HERE — run in CI |
| `python scripts/check_deployment_hardening.py` | NOT RUN HERE — run in CI (unchanged main deploy files) |
| `bash -n deploy/executor/*.sh` | NOT RUN HERE — run in CI |
| `npm run build` / `test:security` / `lint` | NOT RUN HERE (frontend not modified in 6B) |

No new Alembic migration is required: Stage 6B adds no new tables or columns (it
uses the existing `live_order_jobs.dhan_order_id` and `executor_nonce_receipts`).

## 10. Deployment Notes For Your Actual Setup

```
Hostinger main app + live-pilot worker
Neon PostgreSQL central database
EXECUTOR_001 = 64.225.87.19      Dhan Account 1 whitelist = 64.225.87.19
EXECUTOR_002 = 152.42.157.165    Dhan Account 2 whitelist = 152.42.157.165
```

- Each droplet: `sudo bash deploy/executor/install_executor.sh`, then
  `configure_executor_env.sh EXECUTOR_00X <reserved_ip> <hostinger_ip>`, set the
  strong `EXECUTOR_SHARED_SECRET` and `DATABASE_URL` by hand, start, then run
  `check_executor_health.sh` and `check_reserved_ip_route.sh`.
- Hostinger: set `EXECUTOR_SHARED_SECRETS_JSON={"EXECUTOR_001":"...","EXECUTOR_002":"..."}`
  with the matching secrets; register both executor URLs; verify health + egress;
  assign and confirm; approve both users; run a dry-run first.
- Keep real flags off until the pilot moment; disable them immediately after.

## 11. Remaining Risks

- This is **not** a public live launch.
- The first real pilot must use tiny quantity with continuous manual monitoring.
- No automated DigitalOcean provisioning — droplets, Reserved IPs, DNS, TLS, and the
  cloud firewall are configured manually.
- No large-scale load validation; concurrency beyond two accounts is unproven.
- Broker outage, partial fill, and recovery behavior still need real-world
  validation; the executor returns `broker_timeout`/`broker_rejected` but live
  reconciliation of an ambiguous order must be confirmed manually in the Dhan console.
- Compliance and support processes (SEBI algo registration nuances, user support,
  incident comms) still need to be established.
- Verification commands were not executed in this environment; run them in CI/Hostinger.

## 12. Go / No-Go Status

| Target | Decision |
|---|---|
| Paper beta | **Conditional GO** after production DB/worker/backup validation |
| 2-account dry-run | **GO** after both executors are deployed and egress-verified |
| 2-account tiny live pilot | **Conditional GO** — only by following the runbook with all flags + checks enabled, after CI verification passes |
| Public live | **NO-GO** |
| 100-user production | **NO-GO** |

Next recommended stage:

`Stage 6C — Real VPS Deployment Validation + First Controlled Live Pilot Checklist`
