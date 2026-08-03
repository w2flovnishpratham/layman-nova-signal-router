# NOVA C2F2 — PostgreSQL admin-review status width fix

## Summary

The valid C1 approval route could not persist its own status values on
PostgreSQL. `strategy_admin_reviews.previous_status` and `new_status` were
declared `VARCHAR(20)`, but the approval route writes `ready_for_admin_review`
(22 chars) and `approved_for_tv_compile` (23 chars). PostgreSQL rejected the
insert with `StringDataRightTruncation`; SQLite silently accepted it and masked
the defect. Migration `0017_expand_admin_review_status` widens both columns to
`VARCHAR(64)`, which fixes the approval route on the real PostgreSQL schema.

## Root cause

`admin_pine_conversion_service.approve()` records the decision by inserting a
`StrategyAdminReview` row:

- `previous_status = row.status` — at approval time this is
  `ready_for_admin_review` (22 chars).
- `new_status = "approved_for_tv_compile"` (23 chars).

Both target columns were created `VARCHAR(20)` by migration
`0011_personal_pine_validation`. On PostgreSQL a 22/23-char value into a
`VARCHAR(20)` column raises `StringDataRightTruncation` before the row is
written, so the whole approval transaction fails with a database error. The
truncation happens at the persistence layer, so no application-level guard can
route around it — only widening the column fixes it.

### Why SQLite masked it

Two independent SQLite behaviours hid the defect:

1. SQLite does not enforce declared `VARCHAR` length — it stores any string.
2. Alembic's SQLite `batch_alter_table(recreate="always")` rebuilds a table by
   reflecting column types from the ORM's `Base.metadata`, not from the
   migration's literal column spec. So on SQLite the columns take whatever width
   the ORM currently declares, regardless of the migration.

Because of (1), a SQLite upgrade test can never reproduce the truncation. Only
PostgreSQL enforces the declared length, so the fix is verified against a real,
disposable PostgreSQL database migrated through the actual Alembic chain.

## Column widths

| Column | Before | After |
| --- | --- | --- |
| `strategy_admin_reviews.previous_status` | `VARCHAR(20)` | `VARCHAR(64)` |
| `strategy_admin_reviews.new_status` | `VARCHAR(20)` | `VARCHAR(64)` |

`64` accommodates the current closed status vocabulary with headroom (longest
value written is 23 chars) and avoids an immediate follow-up migration for
similarly named workflow states.

### Sibling status columns reviewed (audit)

Every column that could receive the long C1/C2 approval vocabulary was checked.
Only the two admin-review columns were defective:

| Column | Declared | Longest value it receives | Verdict |
| --- | --- | --- | --- |
| `strategy_admin_reviews.previous_status` | `VARCHAR(20)` | `ready_for_admin_review` (22) | **widened to 64** |
| `strategy_admin_reviews.new_status` | `VARCHAR(20)` | `approved_for_tv_compile` (23) | **widened to 64** |
| `pine_conversion_requests.status` | `VARCHAR(30)` | `manual_conversion_required` (26) | unchanged — fits |
| `strategy_versions.status` | `VARCHAR(20)` | `changes_requested` (17); `rejected` in the C1/C2 path | unchanged — fits |
| `strategy_validation_reports.status` | `VARCHAR(20)` | short validation states | unchanged — fits |
| `strategy_catalog.status` | `VARCHAR(20)` | `coming_soon` / `draft` / `active` | unchanged — fits |
| `strategy_instance_positions.reconciliation_status` | `VARCHAR(40)` | `skipped_not_real_mode` (21) | unchanged — fits; also position code, out of scope |

The long approval values (`ready_for_admin_review`, `approved_for_tv_compile`)
only ever land in `pine_conversion_requests.status` (already `VARCHAR(30)`, safe)
and the two `strategy_admin_reviews` columns (defective, fixed here). No
unrelated columns were widened.

## Migration 0017

- Revision: `0017_expand_admin_review_status`
- Down revision: `0016_c2_tv_installation`
- Width-only change. Nullability, foreign keys, indexes, review semantics and
  stored status values are untouched.

`upgrade()` widens both columns `20 → 64`. On PostgreSQL this emits real
`ALTER TABLE strategy_admin_reviews ALTER COLUMN <col> TYPE VARCHAR(64)`
statements, so an already-deployed `VARCHAR(20)` database is genuinely widened.

## Safe downgrade

`downgrade()` narrows `64 → 20` but never silently truncates review evidence.
Before narrowing it counts rows whose `previous_status` or `new_status` exceeds
20 characters. If any exist it raises a controlled `RuntimeError` explaining that
C1/C2 review evidence must be removed or migrated explicitly first; the columns
stay wide and no data is lost. With no long values present the downgrade
succeeds.

Downgrade behaviour verified:

- empty database → succeeds (narrows to 20)
- legacy short-status rows only → succeeds
- row holding `approved_for_tv_compile` → fails safely, no truncation, row
  preserved

## Real PostgreSQL verification

Against a disposable PostgreSQL 16 database migrated through the real Alembic
chain (`base → 0017`):

- At the deployed `VARCHAR(20)` schema, inserting the exact approval values
  (`ready_for_admin_review`, `approved_for_tv_compile`) raises
  `psycopg.errors.StringDataRightTruncation` — the defect reproduced.
- Migration `0017` emits the real widening `ALTER` statements and
  `information_schema.columns.character_maximum_length` reports `64` for both
  columns.
- After `0017`, the same insert succeeds and both values are read back intact.
- Re-upgrade is idempotent; empty downgrade narrows back to 20; downgrade with
  long evidence present is blocked.

### C2 concurrency scenarios

The four required C2 concurrency scenarios (duplicate same-owner installation,
different-owner installation, concurrent credential generation, generation vs.
rotation) live in `test_c2_security_flag_fix.py` and pass on the standard
SQLite harness. They are written as in-process `threading.Barrier` +
`ThreadPoolExecutor` bursts through the FastAPI `TestClient` against a single
shared connection pool. That harness relies on SQLite's serialized write
locking; run in-process against real PostgreSQL it blocks at the
connection/row-lock level (`SELECT ... FOR UPDATE` on overlapping rows across
pooled threads). Re-verifying these four scenarios on the exact PostgreSQL
schema therefore requires a process-isolated harness — one OS process per
racer, each with its own connection — in the style of
`scripts/personal_pine_pg_verify.py`. That is a separate exercise from this
width-only fix and is not caused by it (the width change touches only two
column lengths).

## Tests

- `app/tests/test_c2_postgres_status_width.py` — real-PostgreSQL width proof
  (defect at 20, real `ALTER` to 64, persistence, empty/blocked downgrade).
  Gated by `NOVA_PG_TEST_URL`; skipped when unset.
- `app/tests/test_c2_migration.py` — dialect-agnostic checks (vocabulary
  persists at head, downgrade guard blocks on long evidence, ORM metadata width
  64). Documents why width transitions are not asserted on SQLite.
- `app/tests/test_migration_metadata.py` — head is `0017_expand_admin_review_status`
  off `0016_c2_tv_installation`; every revision id fits `alembic_version.version_num`
  (`VARCHAR(32)`). `0017_expand_admin_review_status` is 31 chars.

## Rollback

Downgrade `0017 → 0016` narrows the columns back to `VARCHAR(20)` only when no
stored value exceeds 20 characters; otherwise it fails safely without data loss.
To roll back after C1 approvals exist, remove or migrate the oversized review
rows explicitly first.

## Final closure requirement

Both `strategy_admin_reviews` status columns are `VARCHAR(64)`, the ORM and
PostgreSQL schema agree, and a valid C1 approval persists
`ready_for_admin_review` / `approved_for_tv_compile` without truncation on the
real PostgreSQL schema. This removes the last schema blocker for closing C2 and
proceeding to merge and staging. No runtime behaviour, Prompt V3.1, Transport
V2, execution, risk, broker, position, or Claude-enablement state was changed.
