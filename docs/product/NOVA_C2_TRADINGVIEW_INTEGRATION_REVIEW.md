# NOVA C2 TradingView Integration — Independent Review

## Review identity

```text
Starting SHA: 97486a8837f49e90dcaa39527fdd3c9943aa3f5b
Reviewed SHA: 97486a8837f49e90dcaa39527fdd3c9943aa3f5b
Comparison baseline: 10555eb7427e6517f02a138fdb7568b6c1a77472
Implementation branch: phase-product-c2-tradingview-integration
Review branch: phase-product-c2-tradingview-integration-review
Verdict: BLOCKED_SECRET_SAFETY
```

The implementation is substantial and most control-plane boundaries are sound, but it is not ready for a real TradingView HOLD smoke test. A compiler-error summary can persist and return several explicitly prohibited secret classes. A second independent blocker allows an already eligible C2 instance to remain engine-selectable after the server feature flag is disabled. The focused test suite does not exercise either defect and lacks several required concurrency and mutation cases.

No runtime code or test logic was changed during this review. This report is the only repository change.

## Blocking findings

### B1 — Compiler-error sanitization leaks prohibited secret classes

Severity: blocking
Primary verdict: `BLOCKED_SECRET_SAFETY`

`backend/app/services/c2_tradingview_service.py` uses `_SECRET_ASSIGNMENT` and `_safe_text()` for both compiler errors and setup notes. The expression only redacts a narrow set of assignment-shaped values. The following direct probe used inert markers:

```text
Input:
Authorization: Bearer AUTH_MARKER TOTP=123456 DATABASE_URL=postgresql://user:pass@host/db path=C:\secret\vault token=TOKEN_MARKER password=PASSWORD_MARKER

Output:
Authorization: Bearer AUTH_MARKER TOTP=123456 DATABASE_URL=postgresql://user:pass@host/db path=C:\secret\vault token=[REDACTED] password=[REDACTED]
```

The Authorization bearer, TOTP, database credentials, and filesystem path survive. Only the token and password assignments are redacted.

This output is not ephemeral: `record_compile()` stores it in `tradingview_compile_evidence.compiler_error_summary`, and `_compile_public()` returns the stored value in HTTP responses. The existing test injects only `token=` and `password=`, so it passes while the broader required secret classes leak.

Smallest correction direction:

1. Replace the narrow assignment expression with a conservative sanitizer covering authorization/bearer values, TOTP/OTP forms, database URLs/DSNs, filesystem paths, cookies/sessions, credentials, API keys, and known broker/provider token forms.
2. Apply the same sanitizer to all persisted compiler notes.
3. Add route-level tests that assert the markers are absent from the HTTP response and persisted compile row, not merely from audit metadata.
4. Keep the 1,000-character bound and terminal compile semantics unchanged.

### B2 — Feature flag does not deactivate existing eligible C2 rows

Severity: blocking
Section verdict: `C2_FLAG_FAILS_CLOSED` not confirmed

An isolated real-service probe created a valid C1 approval, compile success, SELF installation, active credential, real private-webhook HOLD, and Paper-eligible instance. It then set `settings.C2_TRADINGVIEW_INSTALLATION_ENABLED = False`.

Observed:

```text
engine picker after flag-off: C2 instance selectable = true
PUT /api/engine/selection after flag-off: HTTP 200
```

The C2 write routes and private-webhook C2 handling check the flag. Engine eligibility does not. `_engine_eligibility()` calls C2 readiness for existing personal TradingView rows, while `_readiness()` contains no feature-flag gate. Consequently, a client cannot turn C2 on, but disabling the server flag does not revoke an already eligible row.

Smallest correction direction:

1. Make C2 readiness/engine eligibility return a stable `FEATURE_DISABLED` blocker when the server flag is false.
2. Add a regression test that creates an eligible C2 row, disables the flag, verifies it is non-selectable, and verifies engine selection returns a conflict.
3. Preserve C1 and legacy non-C2 behavior.

### B3 — Required dynamic evidence is incomplete

Severity: blocking review evidence
Section verdict: `C2_TEST_EVIDENCE_SUFFICIENT` not confirmed

The C2 backend suite has eight integration tests plus one migration test. It demonstrates the happy path, terminal compile behavior, basic authorization, credential lifecycle, HOLD idempotency, zero execution artifacts, and frontend behavior. It does not dynamically cover the complete required matrix:

- source, strategy-layer, validation-report, review, prompt-hash, and transport-hash mutations at both compile and installation boundaries;
- concurrent installation requests;
- concurrent credential generation and the one-active-credential invariant;
- each Paper-eligibility gate removed independently;
- flag-off behavior with existing eligible database rows;
- all required secret-marker classes through the actual HTTP and persistence path.

Database uniqueness constraints protect duplicate installation and credential state, but concurrent losing requests may expose an uncontrolled integrity error rather than the intended stable conflict. That behavior needs dynamic proof.

## Changed-file and scope audit

All 27 implementation files were classified. No file was omitted.

| Classification | Files |
| --- | --- |
| CONFIGURATION | `backend/.env.example`, `backend/app/config.py`, `backend/app/main.py` |
| MIGRATION | `backend/alembic/versions/20260719_0016_c2_tv_installation.py` |
| DATABASE_MODEL | `backend/app/db/models.py` |
| C2_ROUTE | `backend/app/routers/c2_tradingview.py` |
| C2_SCHEMA | `backend/app/schemas/c2_tradingview.py` |
| C2_SERVICE | `backend/app/services/c2_tradingview_service.py` |
| EXISTING_WEBHOOK_INTEGRATION | `backend/app/routers/private_webhook.py`, `backend/app/services/private_webhook_service.py` |
| EXISTING_INSTANCE_INTEGRATION | `backend/app/routers/strategy_instances.py`, `backend/app/services/strategy_instance_service.py`, `backend/app/services/tradingview_setup_service.py` |
| FRONTEND_ADMIN_UI | `frontend/src/strategies/AdminPineConversion.tsx`, `frontend/src/strategies/AdminPineConversion.test.tsx`, `frontend/src/strategies/C2AdminPanel.tsx`, `frontend/src/strategies/C2AdminPanel.test.tsx` |
| FRONTEND_OWNER_UI | `frontend/src/api.ts`, `frontend/src/strategies/C2MyStrategies.tsx`, `frontend/src/strategies/C2MyStrategies.test.tsx`, `frontend/src/strategies/PersonalStrategiesPage.tsx`, `frontend/src/strategies/PersonalStrategiesPage.test.tsx` |
| TEST | `backend/app/tests/test_c2_migration.py`, `backend/app/tests/test_c2_tradingview_integration.py`, `backend/app/tests/test_migration_metadata.py`, `backend/app/tests/test_r1b_persistence_schema.py` |
| DOCUMENTATION | `docs/product/NOVA_C2_TRADINGVIEW_INTEGRATION.md` |
| UNEXPECTED | None |

The diff contains 3,755 insertions and 16 deletions. Prompt V3.1, Transport V2, the Claude provider, execution router, risk manager, Dhan placement, PaperBroker execution, position authority, and canonical decision/outcome/rejection implementations are unchanged. Existing webhook and instance changes are limited to C2 HOLD verification, readiness, and visibility integration.

Result: `C2_SCOPE_CONFIRMED`.

## Boundary review

### C1 approval and candidate integrity

`_approved_context()` independently re-reads and verifies the current source artifact, source SHA, candidate Pine artifact and SHA, strategy-layer artifact and SHA, assembled candidate binding, passing validation report, effective approved review, review/report/provenance bindings, Prompt V3.1 version/SHA, and Transport V2 version/SHA. Compile evidence is re-matched before installation, webhook verification, and readiness.

The static implementation is fail-closed for the full binding set, and the candidate-mutation test passes. The complete mutation matrix is not dynamic, so this is implementation-confirmed but test-evidence-incomplete.

Result: `C1_TO_C2_EXACT_BINDING_CONFIRMED` in code; additional tests required by B3.

### Compile evidence

Compile success is admin-only, manually recorded, terminal, unique per conversion, and binds conversion, version, all three content hashes, Prompt V3.1, Transport V2, admin, and time. There is no TradingView browser automation, password/cookie storage, or external editor control.

Compile failure is terminal and bounded, but not safely sanitized for all required secret classes.

```text
Compile-success result: PASS
Compile-failure terminal semantics: PASS
Compile-failure secret safety: FAIL (B1)
```

Therefore `TRADINGVIEW_COMPILE_EVIDENCE_CONFIRMED` cannot be issued without qualification.

### Migration 0016

The migration has `down_revision = "0015_r1b_persistence"`. It adds one compile-evidence table, two nullable instance columns, and nullable C2 binding/readiness columns on `tradingview_setups`. Foreign keys and uniqueness match the implemented lookup/lifecycle paths. It does not alter R1B evidence, broker, order, risk, position, or execution tables.

Automated empty-database up/down/up passed. An additional review-only probe seeded:

- one legacy `TradingViewSetup`;
- one owner and personal strategy;
- input and approved-candidate versions;
- source, candidate, and strategy-layer artifacts;
- validation report and admin review;
- current C1 conversion request;
- one existing inert strategy instance.

Results:

```text
0015 seeded -> 0016: PASS
0016 -> 0015: PASS
0015 -> 0016 again: PASS
Legacy/C1 row counts preserved: PASS
New C2 fields on legacy setup remained NULL: PASS
No compile evidence fabricated: PASS
```

Result: `C2_MIGRATION_CONFIRMED`.

### Installation, instance, and authorization

Exactly two modes are accepted: `MANAGED` and `SELF`. Creation requires the feature flag, exact approved context, terminal compile success, a real owner, and matching candidate. The service creates an owner-bound `StrategyInstance` with the exact candidate SHA, `signal_only` execution, verification false, and non-running status. Unsafe broker, order-size, lot, expiry, strike, security-ID, live-mode, and owner-binding fields are not part of the strict request schema.

SELF owner reads are owner-scoped. MANAGED owners do not receive managed Pine, credential controls, or admin compiler notes. Admin functions use authenticated server identity rather than request-supplied identity.

```text
Managed installation: PASS
Self installation: PASS
Instance owner binding: PASS
Instance born inert: PASS
Live-authority denial: PASS
Installation idempotency: database constrained; concurrent response semantics not dynamically proven
```

Results: `C2_INSTALLATION_LIFECYCLE_CONFIRMED`, `OWNER_BOUND_INERT_INSTANCE_CONFIRMED`, `MANAGED_SELF_AUTHORIZATION_CONFIRMED`, and `C2_LIVE_AUTHORITY_DENIED`, subject to B3's concurrency evidence gap.

### Credentials

The reused generator uses `secrets.token_urlsafe(32)`. Only the digest is stored. Plaintext is included only in the creation/rotation response and cannot be reconstructed by later reads. No credential appears in the webhook URL, browser storage, audit metadata, or frontend console calls. Rotation and revocation invalidate the old credential and clear eligibility.

```text
Randomness: PASS
Hash-only storage: PASS
Display once: PASS
Later GET/browser refresh: PASS
Rotation: PASS
Revocation: PASS
Old credential after rotation/revocation: PASS
One active credential database constraint: PASS
Concurrent generation response semantics: NOT DYNAMICALLY PROVEN
```

Result: `DISPLAY_ONCE_HASH_ONLY_CREDENTIAL_CONFIRMED`, with B3's concurrency evidence gap.

### Setup package

The package is generated from the exact current approved candidate. It carries candidate SHA, strategy and instance labels, `/api/webhooks/private`, a JSON-body credential placeholder/value, HOLD payload, and manual instructions. It contains no credential in the URL and no broker, order-sizing, instrument, live-mode, Anthropic, Dhan, or server-environment controls. A changed candidate fails the revalidated approval context.

Result: `C2_SETUP_PACKAGE_CONFIRMED`.

### Private webhook, HOLD, and zero execution

The traced path is:

```text
POST /api/webhooks/private
-> hash-only credential resolution
-> owner/instance binding
-> signal parsing/persistence
-> C2 HOLD-only validation
-> committed HOLD evidence pinning
-> server readiness derivation
```

Validation rechecks the active current credential, owner, instance, version, installation, exact approved context, compile evidence, candidate SHA, non-suspended state, and Paper-safe execution mode. Non-HOLD actions are rejected for C2. Local/synthetic connection tests do not satisfy the C2 real-HOLD binding.

The integration test asserts repeated valid HOLD behavior and zero `StrategyExecutionJob`, order intent, order, or position artifacts. Static tracing shows no call from C2 HOLD verification into risk, entry, exit, reversal, broker, or position-authority paths.

```text
Valid HOLD: PASS
Duplicate HOLD: PASS
Wrong credential: PASS
Revoked/rotated credential: PASS
Foreign instance/owner: PASS by owner/instance/credential binding
Non-HOLD: PASS
Synthetic route: PASS
Candidate mutation: PASS
Zero execution jobs: PASS
Zero orders: PASS
Zero positions: PASS
```

Results: `EXACT_HOLD_ROUTING_CONFIRMED`, `HOLD_ZERO_EXECUTION_AUTHORITY_CONFIRMED`, and `HOLD_IDEMPOTENCY_CONFIRMED`.

### Paper eligibility and engine selection

Paper eligibility is server-derived from effective C1 approval, exact compile evidence, installation, owner-bound instance, active current credential, current-credential HOLD, candidate/source/layer integrity, non-suspension, and Paper-safe mode. Rotation, revocation, suspension, mutation, or invalid compile binding makes the applicable gate false. Eligibility does not start the engine.

The engine list is owner-scoped and only marks currently eligible instances selectable. Selecting changes only `UserEngineConfig.selected_strategy_instance_id`; it does not create jobs, orders, positions, or start an engine.

The feature flag defect in B2 breaks the required deactivation property after a row has already become eligible.

```text
Server-derived Paper eligibility: PASS while feature enabled
Owner-only visibility: PASS
Engine remains stopped: PASS
Feature flag with existing eligible rows: FAIL
```

Results: `SERVER_DERIVED_PAPER_ELIGIBILITY_CONFIRMED` and `OWNER_ONLY_ENGINE_VISIBILITY_CONFIRMED` for enabled operation; `C2_FLAG_FAILS_CLOSED` is not confirmed.

### Audit and frontend

Audit events exist for compile success/failure, installation creation, credential generation/rotation/revocation, HOLD verification, Paper eligibility, and suspension. Audit metadata uses identifiers and truncated SHA references rather than Pine or credential plaintext.

Frontend controls are server-flag- and role-gated. Admin can review approved Pine, record compile results, choose owner/mode, create installations, manage credentials, inspect HOLD/readiness, and suspend. Owner UI distinguishes SELF/MANAGED, hides managed secrets, provides SELF-only setup controls, rehydrates state, and exposes no Live or browser-automation controls. Credential state is transient and disappears after dismissal/refresh.

```text
Audit event coverage: PASS
Audit credential/Pine omission: PASS
Overall secret/log/HTTP/database safety: FAIL (B1)
Admin UI: PASS
Owner UI: PASS
```

Results: `C2_ADMIN_UI_CONFIRMED` and `C2_OWNER_UI_CONFIRMED`; `C2_AUDIT_AND_SECRET_SAFETY_CONFIRMED` is not confirmed.

## Regression and shard accounting

### Discovery

```text
Command: python -m pytest app/tests --collect-only -q
Backend test modules discovered: 84
Tests collected: 1,146
Assignment: every module exactly once; no duplicates; no omissions
```

### Deterministic shard manifest

Each command was `python -m pytest` followed by exactly the listed module paths. Counts sum to the 1,146-test discovery total.

| Shard | Modules | Collected | Result | Duration |
| --- | --- | ---: | --- | ---: |
| 1 | `test_admin_claude_pine_conversion.py`, `test_atm_ltp_service.py`, `test_auth_login.py`, `test_aws_proxy_slots.py`, `test_c2_migration.py`, `test_c2_tradingview_integration.py`, `test_canonical_signal_adapter.py`, `test_canonical_signal_decision_persistence.py`, `test_canonical_signal_outcome_persistence.py`, `test_canonical_signal_shadow.py`, `test_chat_production_bridge.py` | 199 | 199 passed | 242.83s |
| 2 | `test_compliance_audit.py`, `test_dashboard_chat_feed.py`, `test_db_url_normalizer.py`, `test_deploy_env_config.py`, `test_dhan_client.py`, `test_dhan_marketfeed_ws.py`, `test_entitlements.py`, `test_ghost_position_watcher.py`, `test_instrument_resolver.py`, `test_live_guard.py`, `test_manual_super_levels.py` | 168 | 168 passed | 168.55s |
| 3 | `test_market_chart_service.py`, `test_market_closed_data_guard.py`, `test_migration_metadata.py`, `test_normalized_errors.py`, `test_option_position_monitor.py`, `test_order_idempotency.py`, `test_paper_mode.py`, `test_payments_razorpay.py`, `test_payments_razorpay_checkout.py`, `test_personal_pine.py`, `test_phase1_hardening.py` | 113 | 113 passed | 155.60s |
| 4 | `test_phase2_hardening.py`, `test_pine_capability_registry.py`, `test_pine_conversion.py`, `test_pine_master_prompt_qualification.py`, `test_pine_master_prompt_v3.py`, `test_pine_semantic_analysis_persistence.py`, `test_pine_semantic_preanalyzer.py`, `test_pine_transport_v2.py`, `test_pine_v3_package_assembly.py`, `test_pine_v31_source_binding.py`, `test_portfolio_analytics.py` | 153 | 153 passed | 112.81s |
| 5 | `test_position_operations.py`, `test_position_read_shadow.py`, `test_position_reconciler.py`, `test_position_shadow.py`, `test_private_webhook.py`, `test_private_webhook_execution.py`, `test_r1b_persistence_flags.py`, `test_r1b_persistence_schema.py`, `test_r1b2a_insert_only.py`, `test_r1b2a_zero_call_sites.py`, `test_r1b2b_hold_decision_failure_isolation.py` | 222 | 222 passed | 498.20s |
| 6 | `test_r1b2b_hold_decision_integration.py`, `test_r1b2b_runtime_call_sites.py`, `test_r1b2b3_runtime_call_sites.py`, `test_r1b2b3_trading_decision_concurrency.py`, `test_r1b2b3_trading_decision_failure_isolation.py`, `test_r1b2b3_trading_decision_integration.py`, `test_r1b2b3_worker_race_fix.py`, `test_read_only_dhan_data.py`, `test_readiness_poll_fix.py`, `test_readiness_unification.py`, `test_route_auth.py` | 122 | 122 passed | 344.02s |
| 7 | `test_scrip_master_refresh.py`, `test_secret_hygiene.py`, `test_security_id_resolver.py`, `test_shared_market_data.py`, `test_signal_parser.py`, `test_startup_config_validation.py`, `test_startup_reconciler.py`, `test_strategy_fanout.py`, `test_strategy_instances.py`, `test_strategy_registry.py` | 108 | 107 passed, 1 failed | 193.59s |
| 8 | `test_strategy_signal_rejection_persistence.py`, `test_supertrend_golden_reference.py`, `test_tick_pnl_emission.py`, `test_tradingview_setup.py`, `test_user_credential_vault.py`, `test_user_isolation.py`, `test_verification_and_selection.py`, `test_webhook_formats.py` | 61 | 61 passed | 181.65s |

Total:

```text
1,146 collected
1,145 passed
1 failed
```

The sole failure was:

```text
test_dhan_payload_validation_passes_after_security_id_resolution
expected: validation["ok"] is True
actual:   False
resolution["ok"]: True
payload securityId: 999999
```

The exact test was then run from a clean detached worktree at the pre-C2 baseline `10555eb7427e6517f02a138fdb7568b6c1a77472`. It failed identically at the same assertion with the same `DEFAULT_SECURITY_ID=999999` override. The first baseline worktree attempt used the wrong working directory and collected zero tests; it was discarded and reported here. The corrected exact run proves this is a known pre-existing baseline failure, not a C2 regression.

The earlier implementation report mentioned timestamp-order failures under parallel execution. This review's deterministic shard ran the complete `test_position_operations.py` module with adjacent position/webhook modules and all 222 shard tests passed. No timestamp failure or SQLite error recurred, and no retry was needed for the backend shard. This does not justify weakening ordering assertions; it records that the claimed problem was not reproducible in the bounded review run.

Result: `C2_REGRESSION_ACCOUNTING_COMPLETE`.

### Focused regression commands

```text
python -m pytest app/tests/test_c2_tradingview_integration.py app/tests/test_c2_migration.py app/tests/test_admin_claude_pine_conversion.py -q
32 passed in 72.11s
```

This is eight C2 integration tests, one C2 migration test, and all 23 C1 admin-Claude conversion tests. Private webhook, HOLD, StrategyInstance, credential, engine picker, auth/ownership, migration, Prompt V3.1, Transport V2, and legacy strategy modules were also included exactly once in the shard manifest.

## Toolchain

```text
Alembic heads: PASS — 0016_c2_tv_installation (head)
Empty migration cycle: PASS
Seeded legacy/C1 migration cycle: PASS
Python compileall app + alembic/versions: PASS
git diff --check: PASS
TypeScript (tsc -b): PASS
Frontend production build: PASS
Changed-file ESLint: PASS
Vitest deterministic retry: PASS — 6 files, 34 tests
Changed-file Ruff: 13 findings
```

The first Vitest attempt was run concurrently with build/lint and all six workers timed out before collecting tests. It was retried alone with Node 24, threads, and one worker:

```text
node node_modules/vitest/vitest.mjs run --reporter=verbose --pool=threads --maxWorkers=1
6 files passed; 34 tests passed
```

The available system Node 21.7.3 is outside Vite's supported versions and failed during startup. TypeScript, Vitest, and production build passed with the workspace Node 24.14.0.

Changed-file Ruff reports one unused import in `app/config.py` and twelve compact-statement `E701/E702` findings in `tradingview_setup_service.py`. Running Ruff on those same two files in a clean baseline worktree at `10555eb7427e6517f02a138fdb7568b6c1a77472` reproduced all 13 findings unchanged. New C2 modules and changed C2 hunks add no Ruff finding. This is pre-existing lint debt, not a C2 regression.

## Completion matrix

| Required item | Result |
| --- | --- |
| C1 exact binding | Code PASS; dynamic mutation matrix incomplete |
| Compile success | PASS |
| Compile failure | Terminal/bounded PASS; secret sanitization FAIL |
| Migration | PASS |
| Managed installation | PASS |
| Self installation | PASS |
| Installation idempotency | DB uniqueness PASS; concurrency response not proven |
| Instance owner binding | PASS |
| Instance inert mode | PASS |
| Live-authority denial | PASS |
| Credential randomness | PASS |
| Credential hash-only | PASS |
| Display once | PASS |
| Credential concurrency | DB uniqueness present; dynamic proof missing |
| Rotation | PASS |
| Revocation | PASS |
| Managed/SELF authorization | PASS |
| Setup package | PASS |
| Private webhook | PASS |
| Valid HOLD | PASS |
| Duplicate HOLD | PASS |
| Wrong credential | PASS |
| Revoked credential | PASS |
| Foreign instance/owner | PASS by exact binding |
| Non-HOLD | PASS |
| Synthetic route | PASS |
| Candidate mutation | PASS |
| Zero execution jobs | PASS |
| Zero orders | PASS |
| Zero positions | PASS |
| Paper eligibility | PASS while enabled |
| Owner-only engine | PASS while enabled |
| Engine remains stopped | PASS |
| Feature flag | FAIL for existing eligible rows |
| Audit | PASS |
| Secret/log/HTTP/database safety | FAIL |
| Admin UI | PASS |
| Owner UI | PASS |
| Test quality | FAIL — required evidence incomplete |
| Shard manifest | PASS |
| Focused C2/C1 tests | 32 passed |
| Full backend accounting | 1,145 passed; 1 baseline-identical Dhan failure |
| Alembic/migration/toolchain | PASS except baseline Ruff debt |

## Non-blocking findings

1. `NOVA_C2_TRADINGVIEW_INTEGRATION.md` lists the owner credential-generation route as `/credential`; the implemented owner route is `/self-credential`.
2. The setup package supplies the common relative path `/api/webhooks/private`, not a deployment-resolved absolute URL. This is safe but requires the operator to combine it with the correct backend origin.
3. Ruff's 13 changed-file findings and the Dhan failure are verified pre-existing baseline conditions.

## Required confirmations

```text
No runtime code changed during review: CONFIRMED
No test logic changed during review: CONFIRMED
Only exact C1-approved candidates can enter C2: CONFIRMED BY IMPLEMENTATION
TradingView compilation remains manual: CONFIRMED
No browser automation exists: CONFIRMED
Each StrategyInstance is owner-bound and born inert: CONFIRMED
No C2 instance is Live eligible: CONFIRMED
Only credential hashes are stored: CONFIRMED
Plaintext credentials are displayed once: CONFIRMED
Credentials never appear in URLs: CONFIRMED
A real private-webhook HOLD creates no execution job: CONFIRMED
A HOLD creates no order or position mutation: CONFIRMED
Paper eligibility is entirely server-derived: CONFIRMED
Only the owner sees the eligible strategy: CONFIRMED
Selecting the strategy does not start the engine: CONFIRMED
Prompt V3.1 remains unchanged: CONFIRMED
Transport V2 remains unchanged: CONFIRMED
Claude remains disabled by default: CONFIRMED
No real API key was required: CONFIRMED
No live order occurred: CONFIRMED
No merge or deployment occurred: CONFIRMED
```

## Final disposition

```text
Verdict: BLOCKED_SECRET_SAFETY
Report path: docs/product/NOVA_C2_TRADINGVIEW_INTEGRATION_REVIEW.md
Exact files changed: docs/product/NOVA_C2_TRADINGVIEW_INTEGRATION_REVIEW.md
Commit subject: docs(product): review C2 TradingView integration
Push target: origin/phase-product-c2-tradingview-integration-review
Real smoke-test readiness: NOT READY
User manual step status: NOT NEEDED YET
```

Correct B1 and B2, add focused regression tests for both plus the missing concurrency/mutation matrix, and repeat the affected review sections. Do not run the real TradingView HOLD smoke test until those checks pass.
