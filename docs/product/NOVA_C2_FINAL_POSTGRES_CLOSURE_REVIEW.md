# NOVA C2 — Final PostgreSQL closure review

Independent, review-only verification of the remaining C2 evidence gaps on the
exact PostgreSQL 0017 schema. No runtime code or migrations were modified. All
non-toolchain evidence was produced with temporary, untracked harness scripts
driving the **real FastAPI routes** against a disposable PostgreSQL 16 database
migrated through the real Alembic chain. The only tracked change on the review
branch is this document.

- **Reviewed SHA:** `f08a3b57aec66ebed8b75a4e6463992b64fa2d86`
- **Remediation SHA (width-fix branch):** `1627d620d98fcf9986543bfb6386a3b43643ab5c`
- **Review branch:** `phase-product-c2-final-postgres-closure-review`
- **PostgreSQL:** 16.14 (disposable Docker container; chain `base → 0015 → 0016 → 0017`)

## Verdict

```
APPROVED_WITH_NON_BLOCKING_FINDINGS
```

This final check **found one blocking defect** (a migration-metadata test
regression introduced by the width fix) and it has been **remediated and
re-verified**. All remaining findings are non-blocking and outside the C2
closure surface.

## 1. Exact PostgreSQL 0017 schema

Disposable database migrated by the real Alembic chain (no manual ALTER).

- `information_schema`: `strategy_admin_reviews.previous_status = character varying(64)`,
  `new_status = character varying(64)`
- `alembic_version.version_num = 0017_expand_admin_review_status`
- `alembic heads = 0017_expand_admin_review_status`

`POSTGRESQL_0017_SCHEMA_CONFIRMED`

## 2. Real HTTP C1 approval flow

Driven through the actual admin routes (no direct INSERT substituted):
`POST /api/admin/pine-conversions` → `/manual-response` → `/approve`.

- submit + deterministic analysis → `analysis_status=ANALYZED`
- manual candidate response → DB `pine_conversion_requests.status = ready_for_admin_review`
- `POST .../approve` → **HTTP 200**, `approval_integrity=True`,
  `review_status=APPROVED_FOR_TRADINGVIEW_COMPILE`
- DB `pine_conversion_requests.status = approved_for_tv_compile`
- stored `strategy_admin_reviews.previous_status = ready_for_admin_review`
- stored `strategy_admin_reviews.new_status = approved_for_tv_compile`
- no `DataError`, no `StringDataRightTruncation`, no HTTP 500

`REAL_POSTGRESQL_C1_APPROVAL_CONFIRMED`

## 3–7. Process-isolated PostgreSQL concurrency

Each racer ran as a **separate OS process** (own engine, own connection pool,
own app context), synchronized on a shared wall-clock barrier, hitting the real
routes against the same PostgreSQL database. Every worker reported HTTP status,
safe domain code, resource id, duration, and exception class. **No raw database
exception escaped** in any scenario.

### 4. Duplicate same-owner installation
3 concurrent `POST /api/admin/strategy-installations` (same owner / candidate /
mode). Result: all three **HTTP 200 idempotent** returning the **same**
installation id; DB = **1 TradingViewSetup, 1 StrategyInstance**. No 500, no
duplicate rows.
`POSTGRES_SAME_OWNER_INSTALLATION_CONCURRENCY_CONFIRMED`

### 5. Different-owner installation
2 concurrent installs, same approved candidate, different owners. Both **HTTP
200**, distinct installation ids, distinct StrategyInstance ids, correct owner
binding, no cross-owner leakage.
`POSTGRES_DIFFERENT_OWNER_INSTALLATION_CONCURRENCY_CONFIRMED`

### 6. Concurrent credential generation
3 concurrent generations for one installation. Result: **1 winner (200)**, 2
controlled losers (**409 CREDENTIAL_EXISTS**); exactly **1 active credential
row**; exactly **1 returned plaintext** whose SHA-256 matches the active stored
hash; that plaintext authenticates a HOLD (202); no other plaintext exists. No
500, no raw IntegrityError.
`POSTGRES_CREDENTIAL_GENERATION_CONCURRENCY_CONFIRMED`

### 7. Generation versus rotation
Start with one active credential; race generation vs rotation. Result: rotation
won (**200**, new token), generation lost (**409 CREDENTIAL_EXISTS**); exactly
**1 active credential** after; older credential revoked. Active plaintext →
HOLD **202**; superseded plaintext → HOLD **401 INVALID_CREDENTIAL**. A valid
HOLD with the final active credential created **0 execution jobs, 0 order
intents, 0 positions**.
`POSTGRES_GENERATION_ROTATION_CONCURRENCY_CONFIRMED`

## 8. C2F correction reconfirmation

`app/tests/test_c2_security_flag_fix.py` — **43 passed**:

- operator/compile/setup text sanitizer bounded, deterministic, idempotent;
  secret markers absent from response, DB, audit, and logs before persistence
- `test_feature_flag_is_runtime_kill_switch_and_reenable_is_non_destructive`:
  flag false → `FEATURE_DISABLED`; Paper eligibility off; engine strategy
  non-selectable; selection returns controlled conflict; re-enable restores
  readiness only when all other gates pass
- all 13 Paper gates fail closed independently

`C2_SECRET_SAFETY_CONFIRMED`
`C2_OPERATIONAL_KILL_SWITCH_CONFIRMED`

## 9. Valid complete PostgreSQL vertical slice

Real routes, one PostgreSQL 0017 database:
`approve → compile-success → SELF install → owner self-credential → real
private-webhook HOLD → Paper eligibility → owner engine visibility → selection`.

- HOLD → **202**, `reason=HOLD`, `paper_eligible=True`
- installation `paper_eligible=True`, `live_eligible=False`, `hold_status=VERIFIED`
- owner sees only their strategy; other owner sees `[]`; engine entry
  `selectable=True`; selection **200**
- instance `status=ready`, `execution_mode=signal_only`, `verification_mode=False`
- **0 execution jobs, 0 order intents, 0 positions**; 1 recorded signal
- credential plaintext not recoverable in the installation detail response

`POSTGRESQL_C1_C2_VERTICAL_SLICE_CONFIRMED`

## 10. Complete backend accounting

- Module discovery: **86** test modules
- Test discovery: **1195** tests
- Shard manifest: 4 deterministic contiguous shards by sorted filename
  (**22 / 22 / 22 / 20** modules); shard union == discovery exactly (no
  omissions, no duplicates)

### Full run at reviewed SHA `f08a3b5`

| Shard | Modules | Result | Duration |
| --- | --- | --- | --- |
| 00 | 22 | 384 passed, 3 skipped | 4:15 |
| 01 | 22 | 285 passed | 2:52 |
| 02 | 22 | 340 passed, **1 failed** | 6:00 |
| 03 | 20 | 181 passed, **1 failed** | 2:41 |
| **Total** | **86** | **1190 passed, 3 skipped, 2 failed = 1195** | |

The 3 skipped are the PostgreSQL-gated width tests in
`test_c2_postgres_status_width.py` (`NOVA_PG_TEST_URL` unset for the SQLite
accounting run; they are separately executed green against PostgreSQL — see §1).

### Defect found and remediated (blocking → resolved)

`test_r1b_persistence_schema.py::test_migration_metadata_is_exact` failed:
it pinned `script.get_heads() == ["0016_c2_tv_installation"]`. Migration `0017`
correctly advances the single Alembic head to `0017_expand_admin_review_status`,
but this **second** head-pinning assertion was not updated by the width fix (only
`test_migration_metadata.py` was). This is a migration-metadata regression and
blocks closure under the stated rule.

Remediation (width-fix branch `phase-product-c2-postgres-status-width-fix`,
commit `1627d62`): the assertion was updated to
`["0017_expand_admin_review_status"]` — test-only, one line, no schema or
runtime change. Re-verification: `test_r1b_persistence_schema.py`,
`test_migration_metadata.py`, `test_c2_migration.py` → **32 passed**. No C1, C2,
C2F, migration, or PostgreSQL failure remains.

### Remaining deterministic failure (known, non-blocking)

`test_security_id_resolver.py::test_dhan_payload_validation_passes_after_security_id_resolution`
fails deterministically at both `f08a3b5` and after remediation. It exercises
Dhan `securityId` payload validation with `DEFAULT_SECURITY_ID` — the Dhan /
execution domain, unrelated to C2, PostgreSQL, or the width fix. Reproduces
identically at the reviewed SHA. **Known non-blocking Dhan failure.**

### Flaky test classification (non-blocking, pre-existing)

`test_position_operations.py::test_entry_full_fill_and_duplicate` and
`::test_partial_then_full_exit_and_duplicates` are **non-deterministically
flaky**. Repeated isolated runs: **4 passed / 2 failed of 6** (and 2/3 in a
second batch, 5/5 in a third). Root cause is a test-side ordering assumption:
the test asserts an ordered event list `["entry_submitted", "entry_filled",
"exit_partial_fill", "exit_filled"]`, but the event query has no stable
tie-break and `entry_submitted`/`entry_filled` share a timestamp, so they
occasionally return reversed. This is a pre-existing position-domain flake,
independent of the reviewed change (whose entire diff touches only the
admin-review column width and one migration-metadata assertion — nothing in
`position_operations`), and outside the C2 closure surface. Reported here, not
hidden from the total.

## 11. Toolchain

- `alembic heads` → `0017_expand_admin_review_status (head)`
- PostgreSQL migration cycle: upgrade `head`(64) → downgrade `0016`(20) →
  re-upgrade `head`(64) — clean and reversible
- Python compilation: OK
- Ruff (migration / model / C2 correction files): all checks passed
- `git diff --check`: clean
- **TypeScript** (`tsc -b`): **PASS** (exit 0)
- **ESLint**: 11 pre-existing errors (setState-in-effect, react-refresh/only-export-components)
  in untouched components — non-blocking, present at the reviewed SHA, no
  frontend file changed
- **Vitest** and **`vite build`**: fail to start with
  `TypeError [ERR_INVALID_ARG_VALUE]: 'format' ... Received ['underline','gray']`
  — a Node v24.18 `util.styleText` incompatibility in the tooling's color
  dependency, environmental and pre-existing, unrelated to any code change. The
  authoritative TypeScript type-check (`tsc -b`) passes.

Frontend findings are non-blocking for C2: the reviewed change touches zero
frontend files, and TypeScript type-checking is clean.

## Completion report

| Field | Value |
| --- | --- |
| Reviewed SHA | `f08a3b5` (remediation `1627d62`) |
| Review branch | `phase-product-c2-final-postgres-closure-review` |
| Verdict | `APPROVED_WITH_NON_BLOCKING_FINDINGS` |
| PostgreSQL version | 16.14 |
| Alembic head | `0017_expand_admin_review_status` |
| Status-column widths | `previous_status` / `new_status` = `character varying(64)` |
| Real HTTP C1 approval | HTTP 200, approval_integrity True, no truncation/500 |
| Stored previous_status | `ready_for_admin_review` |
| Stored new_status | `approved_for_tv_compile` |
| Same-owner concurrency | 3×200 idempotent, 1 setup / 1 instance |
| Different-owner concurrency | both 200, distinct installs & instances, correct binding |
| Credential-generation concurrency | 1×200 + 2×409, exactly 1 active |
| Generation-vs-rotation | rotation 200 / generation 409, exactly 1 active |
| Active-credential count | 1 |
| Active-plaintext result | HOLD 202 (accepted) |
| Superseded-plaintext result | HOLD 401 INVALID_CREDENTIAL (rejected) |
| PostgreSQL valid HOLD | 202, reason=HOLD, paper_eligible=True |
| Zero execution | 0 jobs, 0 order intents, 0 positions |
| Paper eligibility | True; live_eligible False |
| Owner visibility | owner-only; other owner sees [] |
| Engine remains stopped | instance ready/signal_only, not verification_mode; selection is metadata only |
| Secret-safety | confirmed (sanitized before persistence; markers absent) |
| Feature-kill-switch | confirmed (runtime disable + non-destructive re-enable) |
| Backend module discovery | 86 |
| Backend test discovery | 1195 |
| Shard manifest | 22/22/22/20; union == discovery |
| Full backend result | 1190 passed, 3 skipped, 2 failed (1 remediated, 1 known Dhan) |
| Known Dhan comparison | reproduces identically at `f08a3b5`; non-blocking |
| R1B / flaky classification | migration head assertion remediated; `test_position_operations` flaky 4/6 isolated (pre-existing event-order tie-break, non-blocking) |
| TypeScript | PASS (`tsc -b` exit 0) |
| Vitest | environmental startup failure (Node 24 `util.styleText`), non-blocking |
| Build | `vite build` blocked by same Node 24 tooling issue; TypeScript clean |
| ESLint/Ruff/compile | Ruff pass; py_compile pass; ESLint 11 pre-existing (non-blocking) |
| Migration cycle | upgrade→downgrade→re-upgrade clean (64→20→64) |
| Diff check | clean |
| Exact files changed | this document (review branch); `test_r1b_persistence_schema.py` (width-fix branch remediation) |
| Commit | `docs(product): close C2 PostgreSQL verification` |
| Push | `origin/phase-product-c2-final-postgres-closure-review` |
| Blocking findings | 1 found (migration metadata regression) → remediated & re-verified |
| Non-blocking findings | known Dhan failure; pre-existing flaky `test_position_operations`; Node-24 frontend tooling; pre-existing ESLint |
| C2 closure status | closed (with non-blocking findings) |
| Real web-test readiness | ready for one real private-webhook HOLD smoke |
| User manual step status | none required |

## Explicit confirmations

- The complete C1 approval HTTP route succeeds on PostgreSQL 0017.
- No direct database INSERT was substituted for route verification.
- All four C2 races pass on the exact PostgreSQL schema.
- Exactly one active credential remains after credential races.
- Only plaintext matching the active stored hash remains usable.
- Valid HOLD creates no job, order, or position.
- Secret sanitization remains effective before persistence.
- Disabling C2 deactivates existing eligible C2 strategies (runtime kill switch).
- Prompt V3.1 was not modified.
- Transport V2 was not modified.
- No C2 instance became Live eligible.
- Claude remains disabled by default.
- No production or real user credentials were used.
- No live order occurred.
- No merge or deployment occurred.

## Scope statement

Only this document is a tracked change on the review branch. The one blocking
defect this review found (a stale head assertion) was remediated by a one-line
test-only change on the width-fix branch (`1627d62`) and re-verified. Disposable
PostgreSQL databases, containers, and untracked harness scripts were used and
not committed. The width fix itself (migration `0017`, ORM lengths, downgrade
guard) was reviewed, not modified.
