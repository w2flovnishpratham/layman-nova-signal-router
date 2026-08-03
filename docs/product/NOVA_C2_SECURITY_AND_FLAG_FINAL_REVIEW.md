# NOVA C2F Final Focused Re-review

## Review identity

| Item | Result |
| --- | --- |
| Reviewed SHA | `561c54c30b95f198d1a85b9118f9d1dd0568d974` |
| Reviewed branch | `phase-product-c2-security-flag-fix` |
| Review branch | `phase-product-c2-security-flag-final-review` |
| Review date | 2026-07-19 |
| Reviewer role | Independent FastAPI, PostgreSQL concurrency, security, and C2 integration review |
| Verdict | `BLOCKED_REGRESSION` |

The C2F sanitizer and feature-deactivation correction passes its focused
behavioral review. C2 cannot close because the unmodified PostgreSQL schema at
Alembic head `0016_c2_tv_installation` cannot persist the C1 approval status
required to enter C2.

No runtime code or tests were changed during this review. This document is the
only tracked change.

## Blocking finding

### PostgreSQL rejects the valid C1 approval that C2 requires

A clean PostgreSQL 18.4 database was migrated from base to
`0016_c2_tv_installation`. The real C1 HTTP flow successfully created a
conversion and accepted the manual conversion response, but the approval route
raised an unhandled SQLAlchemy `DataError` backed by PostgreSQL
`StringDataRightTruncation`.

The failing insert attempted to persist:

| Column | Database type | Required value | Value length |
| --- | --- | --- | --- |
| `strategy_admin_reviews.previous_status` | `VARCHAR(20)` | `ready_for_admin_review` | 22 |
| `strategy_admin_reviews.new_status` | `VARCHAR(20)` | `approved_for_tv_compile` | 23 |

The model and migration agree on the undersized constraint:

- `backend/app/db/models.py` declares both fields as `String(20)`.
- `backend/alembic/versions/20260714_0011_personal_pine_validation.py` creates
  both fields as `sa.String(length=20)`.
- `backend/app/services/admin_pine_conversion_service.py` persists the
  22- and 23-character status values.

SQLite does not enforce the declared `VARCHAR` length and therefore masks this
defect. PostgreSQL enforces it. A downgrade from 0016 to 0015 followed by a
re-upgrade to 0016 reproduced both columns as `VARCHAR(20)`.

Impact:

- A real PostgreSQL C1 candidate cannot reach `approved_for_tv_compile`.
- No exact C1-approved candidate can reach C2 compile or installation.
- The mandatory C2 PostgreSQL concurrency scenarios cannot start on the
  unmodified migrated schema.
- The raw database exception is not converted to a controlled domain response;
  in a normal HTTP deployment this is an HTTP 500.
- A real TradingView HOLD smoke test is not ready.

This is a runtime regression, not an unavailable-database evidence gap.
PostgreSQL was available and the defect was reproduced on the exact reviewed
schema.

## C2F correction review

### Secret sanitization

Result: PASS.

The focused suite exercised the real HTTP, service, persistence, audit, and
captured-log paths for compiler errors, setup notes, and suspension reasons.
It covered:

- Authorization, Bearer, and Basic values
- API keys
- access and refresh tokens
- cookies and sessions
- TOTP and OTP
- passwords and generic secrets
- PostgreSQL, Redis, and other sensitive DSNs
- Windows, UNC, and Unix absolute paths
- private-key blocks
- Anthropic-, Dhan-, and webhook-labelled secrets
- malformed Unicode and unsafe control characters

Observed results:

| Surface | Result |
| --- | --- |
| HTTP responses | All test markers absent |
| Compile-evidence database rows | All test markers absent |
| TradingView setup database rows | All test markers absent |
| Audit rows | All test markers absent |
| Captured logs | All test markers absent |
| Persistence ordering | Sanitization occurs before model construction and flush |
| Maximum length | 1,000 characters |
| Determinism | PASS |
| Idempotence | PASS |
| Useful diagnostics | `line 21 column 8 unexpected identifier` remained readable |

The sanitizer applies redaction before truncation, normalizes malformed Unicode,
removes unsafe controls, preserves readable line-oriented diagnostics, and
returns bounded plain text.

### Feature-flag kill switch

Result: PASS for the C2F correction.

A fully Paper-eligible SQLite-backed C2 installation was created through the
real routes, selected in the engine picker, and then evaluated with
`C2_TRADINGVIEW_INSTALLATION_ENABLED=false`.

Observed results:

| Check | Result |
| --- | --- |
| Readiness gate | `feature_enabled=false` |
| Installation status | `FEATURE_DISABLED` |
| Paper eligibility | `false` |
| Live eligibility | `false` |
| Engine entry | Non-selectable |
| Engine blocker | `C2_FEATURE_DISABLED` |
| Selection attempt | HTTP 409, `C2_FEATURE_DISABLED` |
| Private webhook attempt | HTTP 409, `C2_FEATURE_DISABLED` |
| Historical rows | Preserved |
| Selected pointer | Preserved, but non-selectable |
| Automatic engine lifecycle mutation | None |
| Re-enable | Restored Paper eligibility because every other gate remained valid |

The feature check is scoped to C2 setups. Legacy/non-C2 strategy readiness does
not read the C2 feature gate, and the complete strategy-instance regression
suite passed.

### Compile-boundary mutation matrix

Result: 13/13 PASS.

Every mutation failed closed with HTTP 409 and either
`C1_APPROVAL_REQUIRED` or `CANDIDATE_INTEGRITY_INVALID`:

1. source content
2. source SHA
3. strategy-layer content
4. strategy-layer SHA
5. candidate content
6. candidate SHA
7. validation eligibility
8. validation binding
9. review decision
10. review binding
11. prompt SHA
12. transport SHA
13. candidate reference

Each failure left zero compile evidence, installations, StrategyInstances,
credentials, jobs, orders, positions, and signals.

### Installation-boundary mutation matrix

Result: 13/13 PASS.

The same 13 mutations were applied after successful compile evidence and before
installation. Every mutation failed closed with HTTP 409 and left:

- the pre-existing compile evidence intact;
- zero installations;
- zero StrategyInstances;
- zero credentials;
- zero jobs, orders, positions, or signals;
- zero Paper eligibility.

### Paper-readiness gate matrix

Result: 13/13 PASS.

The feature flag was tested independently, and the remaining 12 gates were each
invalidated independently:

1. feature flag
2. C1 approval
3. compile success
4. installation active
5. owner/instance binding
6. signal-only paper-safe mode
7. active credential
8. current credential binding
9. verified HOLD
10. candidate integrity
11. source integrity
12. strategy-layer integrity
13. non-suspension

For every case:

- Paper eligibility was false;
- Live eligibility was false;
- the engine entry was absent for the broken owner binding or otherwise
  non-selectable with a stable blocker;
- selection failed closed with HTTP 404 or 409 as appropriate;
- jobs, orders, and positions remained zero.

Stable blocker coverage included:

- `C2_FEATURE_DISABLED`
- `C1_APPROVAL_INVALID`
- `TRADINGVIEW_COMPILE_REQUIRED`
- `INSTALLATION_INACTIVE`
- `INSTALLATION_OWNER_INVALID`
- `PAPER_MODE_REQUIRED`
- `CREDENTIAL_INACTIVE`
- `CREDENTIAL_BINDING_INVALID`
- `HOLD_NOT_VERIFIED`
- `CANDIDATE_INTEGRITY_INVALID`
- `SOURCE_INTEGRITY_INVALID`
- `STRATEGY_LAYER_INTEGRITY_INVALID`
- `INSTALLATION_SUSPENDED`

## PostgreSQL concurrency

### Acceptance result on the exact migrated schema

Result: BLOCKED BY THE POSTGRESQL C1 APPROVAL REGRESSION.

The disposable server was available:

```text
postgres (PostgreSQL) 18.4
127.0.0.1:55436
database: nova_c2f_review
```

The database was created empty and migrated through Alembic. No production data
or credentials were used. The valid C1 approval failed before C2 installation
creation, so the exact-schema concurrency acceptance run could not proceed.

This is not accepted as PostgreSQL concurrency evidence.

### Diagnostic isolation after widening only the disposable schema

To determine whether a second C2F concurrency defect was hidden behind the
approval regression, a second disposable database was migrated to the same head
and then modified only in that database:

```sql
ALTER TABLE strategy_admin_reviews
  ALTER COLUMN previous_status TYPE varchar(30);
ALTER TABLE strategy_admin_reviews
  ALTER COLUMN new_status TYPE varchar(30);
```

No repository file was changed. These diagnostic results are not approval
evidence because the schema no longer exactly matched the reviewed migration.

| Scenario | Diagnostic result |
| --- | --- |
| Duplicate same-owner installation requests | HTTP 200/200, identical installation ID |
| Stored same-owner installations | One installation and one StrategyInstance |
| Different-owner installation requests | HTTP 200/200, distinct installation IDs |
| Stored distinct owners | Three owners, three installations, three instances including the same-owner case |
| Concurrent credential generation | HTTP 200 plus controlled HTTP 409 `CREDENTIAL_EXISTS` |
| Generation versus rotation | Controlled HTTP 409 `CREDENTIAL_EXISTS` plus HTTP 200 |
| Raw IntegrityError/HTTP 500 | None in diagnostic races |
| Active credentials | Exactly one |
| Plaintext matching active hash | Exactly one returned plaintext |
| Superseded plaintext usability | HTTP 401 |
| Active plaintext usability | HOLD accepted with HTTP 202 |
| Jobs/orders/positions | 0/0/0 |

The diagnostic result supports the C2F row-lock and partial-unique-index design,
but C2 remains blocked until the actual migrated schema supports the valid
prerequisite flow and the races are rerun without a manual schema alteration.

## Valid-flow regression

The existing C2 vertical slice passed under its isolated SQLite test database:

- valid compile;
- SELF installation;
- MANAGED installation;
- one-time hash-only credential display;
- credential rotation and revocation;
- valid real-route HOLD;
- duplicate HOLD idempotence;
- owner-only visibility;
- engine remains stopped/ready rather than automatically activated;
- zero jobs, orders, and positions;
- Live eligibility remains false.

The diagnostic widened-schema PostgreSQL run also accepted the active credential
for a HOLD and produced zero execution artifacts. The unmodified PostgreSQL
valid flow remains blocked at C1 approval and therefore does not pass final
acceptance.

## Frozen boundaries and scope

The correction commit did not modify:

- Prompt V3.1
- Transport V2
- Claude conversion defaults/provider
- migration 0016
- Dhan
- execution router
- risk manager
- PaperBroker
- positions

The correction commit changed only:

- C2 TradingView service
- strategy-instance service
- the new untrusted-text sanitizer
- C2F tests
- C2 product documentation
- engine-picker blocker presentation

No external AI, Anthropic credential, TradingView credential, Dhan credential,
merge, deployment, engine start, order, or position operation was used.

## Verification results

### Focused suites

| Suite | Result |
| --- | --- |
| C2F security/flag/mutation/gate/concurrency tests | 43 passed |
| C2 integration plus C2 migration tests | 9 passed |
| Complete C2 total | 52 passed |
| C1 Claude Pine conversion tests | 23 passed |
| Private webhook, HOLD execution, and strategy-instance tests | 103 passed |

### Complete backend accounting

The 85 `app/tests/test_*.py` modules were sorted by name and assigned by
zero-based module index modulo four. Every module was executed.

| Shard | Modules | Passed | Failed | Result |
| --- | ---: | ---: | ---: | --- |
| 1 | 22 | 356 | 0 | PASS |
| 2 | 21 | 320 | 1 | Known Dhan baseline failure |
| 3 | 21 | 249 | 0 | PASS |
| 4 | 21 | 262 | 1 | Nondeterministic R1B concurrency miss |
| Total | 85 | 1,187 | 2 | 1,189 collected/executed |

Module allocation:

- Shard 1: `test_admin_claude_pine_conversion.py`,
  `test_c2_migration.py`, `test_canonical_signal_decision_persistence.py`,
  `test_compliance_audit.py`, `test_dhan_client.py`,
  `test_instrument_resolver.py`, `test_market_closed_data_guard.py`,
  `test_order_idempotency.py`, `test_personal_pine.py`,
  `test_pine_conversion.py`, `test_pine_semantic_preanalyzer.py`,
  `test_portfolio_analytics.py`, `test_position_shadow.py`,
  `test_r1b_persistence_schema.py`,
  `test_r1b2b_hold_decision_integration.py`,
  `test_r1b2b3_trading_decision_failure_isolation.py`,
  `test_readiness_poll_fix.py`, `test_secret_hygiene.py`,
  `test_startup_config_validation.py`, `test_strategy_registry.py`,
  `test_tradingview_setup.py`, `test_webhook_formats.py`.
- Shard 2: `test_atm_ltp_service.py`,
  `test_c2_security_flag_fix.py`,
  `test_canonical_signal_outcome_persistence.py`,
  `test_dashboard_chat_feed.py`, `test_dhan_marketfeed_ws.py`,
  `test_live_guard.py`, `test_migration_metadata.py`,
  `test_paper_mode.py`, `test_phase1_hardening.py`,
  `test_pine_master_prompt_qualification.py`,
  `test_pine_transport_v2.py`, `test_position_operations.py`,
  `test_private_webhook.py`, `test_r1b2a_insert_only.py`,
  `test_r1b2b_runtime_call_sites.py`,
  `test_r1b2b3_trading_decision_integration.py`,
  `test_readiness_unification.py`, `test_security_id_resolver.py`,
  `test_startup_reconciler.py`,
  `test_strategy_signal_rejection_persistence.py`,
  `test_user_credential_vault.py`.
- Shard 3: `test_auth_login.py`,
  `test_c2_tradingview_integration.py`,
  `test_canonical_signal_shadow.py`, `test_db_url_normalizer.py`,
  `test_entitlements.py`, `test_manual_super_levels.py`,
  `test_normalized_errors.py`, `test_payments_razorpay.py`,
  `test_phase2_hardening.py`, `test_pine_master_prompt_v3.py`,
  `test_pine_v3_package_assembly.py`, `test_position_read_shadow.py`,
  `test_private_webhook_execution.py`, `test_r1b2a_zero_call_sites.py`,
  `test_r1b2b3_runtime_call_sites.py`,
  `test_r1b2b3_worker_race_fix.py`, `test_route_auth.py`,
  `test_shared_market_data.py`, `test_strategy_fanout.py`,
  `test_supertrend_golden_reference.py`, `test_user_isolation.py`.
- Shard 4: `test_aws_proxy_slots.py`,
  `test_canonical_signal_adapter.py`, `test_chat_production_bridge.py`,
  `test_deploy_env_config.py`, `test_ghost_position_watcher.py`,
  `test_market_chart_service.py`, `test_option_position_monitor.py`,
  `test_payments_razorpay_checkout.py`,
  `test_pine_capability_registry.py`,
  `test_pine_semantic_analysis_persistence.py`,
  `test_pine_v31_source_binding.py`, `test_position_reconciler.py`,
  `test_r1b_persistence_flags.py`,
  `test_r1b2b_hold_decision_failure_isolation.py`,
  `test_r1b2b3_trading_decision_concurrency.py`,
  `test_read_only_dhan_data.py`, `test_scrip_master_refresh.py`,
  `test_signal_parser.py`, `test_strategy_instances.py`,
  `test_tick_pnl_emission.py`, `test_verification_and_selection.py`.

No C1, C2, or C2F module failed.

### Non-blocking backend failures

#### Known Dhan baseline

`test_dhan_payload_validation_passes_after_security_id_resolution` failed
identically at both:

- parent baseline `b0290ae5071068763ad359b5905f8af9b32b7656`;
- reviewed correction `561c54c30b95f198d1a85b9118f9d1dd0568d974`.

In both runs:

```text
resolution["ok"] = true
payload["securityId"] = "999999"
expected validation["ok"] = true
actual validation["ok"] = false
```

The assertion and warning were identical. This is the documented baseline Dhan
failure and is unrelated to C2F.

#### R1B concurrency nondeterminism

The full shard observed:

```text
test_concurrent_identical_actions_create_one_of_everything[BUY_PE]
expected CanonicalSignalDecision rows = 1
actual = 0
```

The individual test then passed five consecutive isolated reruns. This matches
the previously observed nondeterministic, shared-state/timing-sensitive R1B
test behavior and is outside the C2F correction.

### Migration and toolchain

| Check | Result |
| --- | --- |
| `python -m alembic heads` | `0016_c2_tv_installation (head)` |
| PostgreSQL base-to-head upgrade | PASS |
| PostgreSQL 0016-to-0015 downgrade | PASS |
| PostgreSQL 0015-to-0016 re-upgrade | PASS |
| Python 3.14.3 compilation | PASS |
| Ruff on correction Python files | PASS |
| TypeScript project build | PASS |
| Vitest | 6 files, 35 tests passed |
| Frontend production build | PASS |
| Changed-file ESLint | PASS |
| `git diff --check` | PASS |

The production build emitted non-blocking environment/bundle warnings:

- local Node.js was 21.7.3; Vite recommends 20.19+ or 22.12+;
- one minified frontend chunk exceeded 500 kB.

Neither warning caused a compilation or build failure.

## Commands

Representative commands used in the clean review worktrees:

```text
git worktree add -b phase-product-c2-security-flag-final-review <clean-path> 561c54c30b95f198d1a85b9118f9d1dd0568d974

docker run -d --name nova-c2f-final-review-pg \
  -e POSTGRES_USER=nova_c2f \
  -e POSTGRES_PASSWORD=<disposable-password> \
  -e POSTGRES_DB=nova_c2f_review \
  -p 127.0.0.1:55436:5432 postgres:18-alpine

DATABASE_URL=postgresql://nova_c2f:<redacted>@127.0.0.1:55436/nova_c2f_review \
python -m alembic upgrade head
python -m alembic downgrade 0015_r1b_persistence
python -m alembic upgrade head
python -m alembic current
python -m alembic heads

python -m pytest app/tests/test_c2_security_flag_fix.py -q
python -m pytest app/tests/test_c2_tradingview_integration.py app/tests/test_c2_migration.py -q
python -m pytest app/tests/test_admin_claude_pine_conversion.py -q
python -m pytest app/tests/test_private_webhook.py app/tests/test_private_webhook_execution.py app/tests/test_strategy_instances.py -q

# Full accounting:
# sorted app/tests/test_*.py, assigned by module index modulo 4,
# each shard executed in a separate clean detached worktree.
python -m pytest <deterministic-shard-modules> -q

python -m pytest app/tests/test_security_id_resolver.py::test_dhan_payload_validation_passes_after_security_id_resolution -vv
python -m pytest "app/tests/test_r1b2b3_trading_decision_concurrency.py::test_concurrent_identical_actions_create_one_of_everything[BUY_PE]" -q

python -m compileall -q app
python -m ruff check app/services/c2_tradingview_service.py app/services/strategy_instance_service.py app/services/untrusted_text_sanitizer.py app/tests/test_c2_security_flag_fix.py
tsc -b --pretty false
vitest run
npm.cmd run build
eslint src/strategies/EngineStrategyPicker.tsx src/strategies/EngineStrategyPicker.test.tsx src/strategies/strategyBlockers.ts
git diff --check
```

The PostgreSQL concurrency verifier used the real FastAPI routes and independent
SQLAlchemy sessions synchronized by a thread barrier. It did not call external
AI or use real Anthropic, TradingView, or Dhan credentials.

## Completion report

| Required field | Result |
| --- | --- |
| Reviewed SHA | `561c54c30b95f198d1a85b9118f9d1dd0568d974` |
| Review branch | `phase-product-c2-security-flag-final-review` |
| Verdict | `BLOCKED_REGRESSION` |
| Sanitizer HTTP result | PASS; all secret markers absent |
| Sanitizer persistence result | PASS; compile/setup/suspension rows sanitized before persistence and bounded |
| Sanitizer audit/log result | PASS; all markers absent |
| Feature-disabled readiness result | PASS; `FEATURE_DISABLED`, Paper false, Live false |
| Feature-disabled engine result | PASS; non-selectable, 409 `C2_FEATURE_DISABLED` |
| Re-enable result | PASS when all other gates remained valid |
| Compile mutation matrix | 13/13 PASS, no partial rows |
| Installation mutation matrix | 13/13 PASS, no partial rows |
| Paper gate matrix | 13/13 PASS, fail closed |
| PostgreSQL installation concurrency | Exact-schema run blocked at C1 approval; diagnostic widened-schema run PASS |
| PostgreSQL credential concurrency | Exact-schema run blocked at C1 approval; diagnostic widened-schema run PASS |
| One-active-credential result | Diagnostic PostgreSQL PASS; exactly one active hash and one matching plaintext |
| Valid HOLD result | SQLite PASS; diagnostic PostgreSQL HTTP 202; exact PostgreSQL flow blocked before C2 |
| Zero execution result | PASS in focused and diagnostic runs |
| Owner visibility result | PASS |
| Live denial result | PASS; always false |
| Migration result | Up/down/up PASS, but reproduces the blocking `VARCHAR(20)` schema |
| Focused tests | 43 C2F, 52 complete C2, 23 C1, 103 webhook/instance tests passed |
| Full backend accounting | 85 modules; 1,189 tests; 1,187 passed; known Dhan baseline plus flaky R1B failure |
| Frontend/toolchain results | TypeScript, 35 Vitest, production build, changed ESLint, Ruff, compileall, diff check PASS |
| Blocking findings | PostgreSQL C1 approval status columns are too short and cause an unhandled database error |
| Non-blocking findings | Known Dhan baseline; intermittent R1B test; local Node/Vite and bundle-size warnings |
| C2 closure status | NOT CLOSED |
| Real HOLD readiness | NOT READY |
| User manual step status | NOT NEEDED |

## Required next action

Do not merge or deploy this reviewed SHA as C2-closed. Correct the PostgreSQL
schema with the smallest additive migration that safely accommodates all valid
review status values, align the ORM lengths, add a PostgreSQL regression test
covering the real C1 approval route, and then rerun this focused review and the
four exact-schema concurrency scenarios.
