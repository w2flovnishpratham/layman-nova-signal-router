# Stage 6C Real VPS Deployment Validation + First Controlled Live Pilot Checklist Applied

Date: 2026-06-15

## 1. Summary

- **What Stage 6C added:** an operator-facing validation toolkit — six `deploy/pilot/`
  scripts plus three runbooks — to verify the real deployment (Hostinger main app,
  Neon PostgreSQL, Executor 001 = 64.225.87.19, Executor 002 = 152.42.157.165, and
  the matching Dhan whitelists) and to gate a first controlled two-account pilot.
  No product features, no DigitalOcean automation, no payments, and no safety gate
  was weakened.
- **Is dry-run validation operationally ready?** Yes. The scripts check Hostinger
  service + workers, Neon migration head, both executors' health and egress IPs,
  and an end-to-end dry-run signal that must create two dry-run jobs and place no
  Dhan order. `pilot_go_no_go_check.sh` aggregates these into a single GO/NO-GO and
  fails closed if live is armed too early or an executor IP mismatches.
- **Does the real pilot still require manual final approval?** Yes. A GO from the
  gate authorizes only the two-account **dry-run**. Enabling real orders is a
  separate, deliberate, supervised step in
  `docs/FIRST_2_ACCOUNT_LIVE_PILOT_CHECKLIST.md`, with one tiny order per account
  and immediate disable afterward. Live remains disabled by default everywhere.

## 2. Files Changed

| File | Change | Reason |
|---|---|---|
| `deploy/pilot/pilot_common.sh` | **New.** Shared constants (expected executor IPs), PASS/FAIL helpers, dry-run flag + IP assertions; never prints secrets. | DRY, consistent safe checks. |
| `deploy/pilot/validate_hostinger_main.sh` | **New.** Service + paper/live workers active; `/api/readiness` ready; live disabled; migrations current; dry-run flags safe. | Hostinger validation. |
| `deploy/pilot/validate_neon_database.sh` | **New.** Neon reachable + Alembic at head + `alembic check`, via the app readiness helpers (no connection string printed). | DB validation. |
| `deploy/pilot/validate_executors.sh` | **New.** Both executors `/health` (code) and `/egress-ip` == expected Reserved IP. | Executor health + egress. |
| `deploy/pilot/validate_dry_run_signal.sh` | **New.** Safe flags; posts one relay alert (token never printed); confirms two dry-run jobs and no real order. | Relay dry-run validation. |
| `deploy/pilot/disable_live_everywhere.sh` | **New.** Forces live off on Hostinger + real orders off on both executors; restarts services. | Emergency shutdown. |
| `deploy/pilot/pilot_go_no_go_check.sh` | **New.** Aggregate GO/NO-GO; pure-logic self-test mode; fails on early live arm or IP mismatch. | Single gate. |
| `docs/STAGE_6C_REAL_VPS_DEPLOYMENT_VALIDATION.md` | **New.** Validation guide with exact commands. | Operator reference. |
| `docs/FIRST_2_ACCOUNT_LIVE_PILOT_CHECKLIST.md` | **New.** Pre-flight, enable, one-order, disable, post-trade audit. | Controlled pilot. |
| `docs/EMERGENCY_LIVE_DISABLE_RUNBOOK.md` | **New.** Fastest stop, kill switch, manual fallback, verify, reconcile. | Incident response. |
| `backend/app/tests/test_stage_6c_pilot_validation.py` | **New.** Static + behavioral checks over scripts and runbooks. | Proof of the rules. |

## 3. Validation Scripts

- **`pilot_common.sh`** — sourced by the others. Holds the expected mapping
  (`EXECUTOR_001_EXPECTED_IP=64.225.87.19`, `EXECUTOR_002_EXPECTED_IP=152.42.157.165`),
  `pass`/`fail`/`finish` (non-zero exit on any failure), `read_env_flag` (prints only
  the requested non-secret flag), `assert_ip_match`, and `assert_dry_run_safe_flags`
  (live off + dry-run on).
- **`validate_hostinger_main.sh`** — `systemctl is-active` for the API, paper worker,
  and live-pilot worker; `curl /api/readiness` must be `ready` with `live_policy:
  disabled` and `migrations: ok`; dry-run flags safe.
- **`validate_neon_database.sh`** — runs the app's `database_ready()` + `migrations_ready()`
  and `alembic check` from the backend venv; reports booleans only, never the URL.
- **`validate_executors.sh`** — for each executor URL, checks `/health` (status ok +
  matching code) and `/egress-ip` equals the expected Reserved IP; fails if either
  IP differs.
- **`validate_dry_run_signal.sh`** — asserts safe flags, posts one `SUPERTREND_FLIP`
  relay alert using `RELAY_TOKEN` (never echoed), then queries `live_order_jobs` to
  confirm ≥2 dry-run jobs and zero `sent`/`confirmed` (real) placements.
- **`disable_live_everywhere.sh`** — sets `ENABLE_LIVE_ORDERS=false` +
  `LIVE_ORDER_DRY_RUN_ONLY=true` on Hostinger, restarts API + live worker, and
  disables `EXECUTOR_REAL_ORDERS_ENABLED` on both executors (SSH if provided, else
  prints the exact commands).
- **`pilot_go_no_go_check.sh`** — enforces the dry-run posture first, then either
  runs a pure-logic self-test (`PILOT_CHECK_ONLY=1`, used by automated checks) or
  orchestrates all four validators. Prints GO only when everything passes; NO-GO and
  non-zero otherwise.

## 4. Dry-run Pilot Process

1. `bash deploy/pilot/validate_hostinger_main.sh` → PASS.
2. `bash deploy/pilot/validate_neon_database.sh` → PASS.
3. `EXECUTOR_001_URL=... EXECUTOR_002_URL=... bash deploy/pilot/validate_executors.sh` → both PASS, egress == 64.225.87.19 / 152.42.157.165.
4. Confirm Dhan whitelists, user→executor assignments (User 1→001, User 2→002), `SUPERTREND_FLIP`-only approvals, max trades/day = 1, tiny quantity.
5. `RELAY_TOKEN=*** bash deploy/pilot/validate_dry_run_signal.sh` → two dry-run jobs, no Dhan order.
6. `... bash deploy/pilot/pilot_go_no_go_check.sh` → **GO** for dry-run.

## 5. Real Tiny Live Pilot Process

Only after a dry-run GO and manual final approval (full detail in
`docs/FIRST_2_ACCOUNT_LIVE_PILOT_CHECKLIST.md`):

1. Hostinger: set `LIVE_ORDER_DRY_RUN_ONLY=false`, `ENABLE_LIVE_ORDERS=true`; restart API + live worker.
2. Each executor: set `EXECUTOR_REAL_ORDERS_ENABLED=true`; restart.
3. Post exactly one `SUPERTREND_FLIP` signal → one order per account, each routed to its own executor and leaving from its reserved IP.
4. Immediately disable: Hostinger `ENABLE_LIVE_ORDERS=false` + `LIVE_ORDER_DRY_RUN_ONLY=true`; executors `EXECUTOR_REAL_ORDERS_ENABLED=false`; run `disable_live_everywhere.sh`; verify readiness shows live disabled.
5. Post-trade audit (Dhan order book, `live_order_jobs`, logs, P&L reconciliation).

## 6. Emergency Shutdown

`sudo bash deploy/pilot/disable_live_everywhere.sh` forces live off on Hostinger and
real orders off on both executors, then restarts services. The global kill switch
(`global_kill_switch`/`emergency_stop`) blocks every live job before any executor or
Dhan call. Manual fallback and reconciliation steps are in
`docs/EMERGENCY_LIVE_DISABLE_RUNBOOK.md`.

## 7. Tests Added

`backend/app/tests/test_stage_6c_pilot_validation.py`:

- `test_all_pilot_scripts_exist`
- `test_validation_scripts_contain_both_executor_ips`
- `test_disable_script_turns_live_flags_off`
- `test_scripts_do_not_echo_secrets`
- `test_go_no_go_fails_when_dry_run_flag_false_too_early`
- `test_go_no_go_fails_on_executor_ip_mismatch`
- `test_go_no_go_passes_when_safe_and_ips_match`
- `test_go_no_go_fails_when_live_enabled_early`
- `test_pilot_checklist_says_not_public_launch`
- `test_pilot_checklist_says_one_tiny_order_only`
- `test_pilot_checklist_says_disable_flags_immediately_after_test`
- `test_validation_doc_and_emergency_runbook_exist`

(The bash-executing tests skip automatically where bash is unavailable.)

## 8. Verification Results

> **Important:** the verification commands could **not** be executed in this
> authoring environment — the isolated Linux sandbox repeatedly failed to boot
> (`HCS ... The paging file is too small for this operation to complete`). The
> scripts and tests were verified by static review against the Stage 6A/6B code and
> the existing test harness. Run the commands below in CI or on the VPS before the
> pilot; they are expected to pass.

| Command | Status |
|---|---|
| `python -m pytest app/tests/test_stage_6c_pilot_validation.py -q` | NOT RUN HERE — run in CI |
| `python -m pytest app/tests -q` | NOT RUN HERE — run in CI (no backend code changed in 6C) |
| `bash -n deploy/pilot/*.sh` | NOT RUN HERE — run in CI |
| `PILOT_CHECK_ONLY=1 ... bash deploy/pilot/pilot_go_no_go_check.sh` | NOT RUN HERE — run in CI |
| `bash deploy/pilot/validate_*.sh` (live infra) | Run on Hostinger during the pilot |

No new Alembic migration is required (Stage 6C adds scripts/docs/tests only).

## 9. Remaining Risks

- This is **not** public live launch.
- This is **not** 100-user scale; only two supervised accounts.
- The first real trade must be watched manually, with the Dhan console open for
  both accounts and the kill switch ready.
- Broker outage / partial fill / ambiguous-order behavior still needs real-world
  observation; the executor reports `broker_timeout`/`broker_rejected` but a timed-out
  order must be reconciled manually in the Dhan console.
- Compliance and support processes are not complete.
- Verification commands were not executed in this environment; run them in CI/VPS.

## 10. Go / No-Go Status

| Target | Decision |
|---|---|
| Paper beta | **Conditional GO** after production DB/worker/backup validation |
| 2-account dry-run | **GO** once `pilot_go_no_go_check.sh` returns GO on the real VPS |
| 2-account tiny live pilot | **Conditional GO** — only after a dry-run GO **and** manual final approval, following the checklist with all flags + checks enabled |
| Public live | **NO-GO** |
| 100-user production | **NO-GO** |

Next recommended stage:

`Stage 6D — Post-Pilot Review + Broker Outage / Partial-Fill Hardening`
