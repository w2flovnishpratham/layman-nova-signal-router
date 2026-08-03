# NOVA Phase R1B — Canonical Persistence and Provenance Design

## 1. Executive recommendation

**Verdict: `READY_FOR_R1B0_PREREQUISITE_HARDENING`**

R1B should use **Option B: dedicated, additive persistence tables**. Do not add
canonical columns to `strategy_signals` in the first persistence release.
Keep that table, its uniqueness rule, webhook replay identity, job rows, and
execution behavior unchanged.

The minimum schema is:

1. `canonical_signal_decisions`: one immutable intent record per accepted
   `StrategySignal`.
2. `canonical_signal_outcomes`: zero or more append-only, idempotent
   state/routing observations per decision.
3. `strategy_signal_rejections`: safe, bounded, post-authentication ingress
   rejections only.
4. `pine_semantic_analyses`: immutable source/analyzer/registry provenance.

No canonical row controls execution. The legacy `NormalizedSignal`, job worker,
execution router, PaperBroker/Dhan path, and JSON position files remain
authoritative. The new records are evidence written in separate transactions
after existing decisions occur. Failure to write canonical evidence must not
reject or alter a valid existing webhook during the initial rollout.

R1B-0 must precede the migration. It resolves R1A findings NB-01 through NB-05:
pure adapter imports, formal secret-taint handling, real `PARTIAL_MATCH`
semantics, correct fixture provenance, and exhaustive fixture expectations.

## 2. R1A findings resolution

### 2.1 Evidence and change-impact map

Every recommendation in this report refers to one of the following IDs. This
matrix records the required repository evidence and implementation impacts.

| ID | File path; model/function; line range | Existing behavior | Proposed behavior | Migration impact | Rollback impact | Security impact |
|---|---|---|---|---|---|---|
| R-01 | `backend/app/config.py`; module constants/`Settings`; lines 16, 338; `backend/app/domain/legacy_signal_adapter.py`; module import; line 7 | Importing the adapter imports `app.config`, which constructs `Settings()` and may read environment/`.env`. | Add `backend/app/domain/trading_constants.py` containing `DEFAULT_EXCHANGE_SEGMENT = "NSE_FNO"`. Import it directly in the adapter. `app.config` imports and re-exports the same constant so all existing runtime imports remain byte/value compatible. | None; R1B-0 only. | Revert the two imports and delete the pure module. | Removes environment-dependent adapter import and narrows the trusted evidence constructor. |
| R-02 | `backend/app/domain/canonical_signal.py`; `_freeze_metadata()`/`CanonicalSignalEvent`; lines 41-113; `backend/app/services/private_webhook_service.py`; `parse_request()`/shadow adapter call; lines 69-72, 105-118, 329-381 | Credential is ingress-only, metadata is recursively filtered, but credential-shaped text can still be copied into a top-level user field such as `signal_id`. | Add a pure secret-taint/redaction policy used by canonical/rejection writers and projections. Do not change ingress acceptance in R1B. Do not copy raw `signal_id`, `comment`, or credential-shaped values into new canonical tables. | New tables store hashes/links and bounded safe metadata only; no legacy column change. | Disable persistence flags; legacy records and responses remain unchanged. | Prevents new secret duplication while preserving compatibility and anti-enumeration. |
| R-03 | `backend/app/services/pine_semantic_preanalyzer.py`; `AnalysisConfidence`/`analyze_source()`; lines 25-27, 343-390 | `PARTIAL_MATCH` exists but is never emitted; confidence is high or indeterminate. | Implement the three-state policy in R1B-0. Confidence is independent of severity; partial/indeterminate evidence can only preserve or increase restrictions, never lower them. | None in R1B-0. Later `pine_semantic_analyses.confidence` persists the closed value. | Revert analyzer rule before persistence is enabled; existing analyses do not exist yet. | Prevents ambiguous evidence from being persisted or presented as safe. |
| R-04 | `backend/app/pine_capabilities/registry.v1.json`; five capability entries; lines 532-631; `backend/app/tests/test_pine_semantic_preanalyzer.py`; inline cases; lines 69-90 | Five registry fixture citations point to files that exist but do not demonstrate the cited construct. | Add/correct dedicated fixture pairs F39, F67, F28, F34, and F15, then update only the matching registry citations after tests are exhaustive. | None; R1B-0 only. | Revert fixture/registry commit as one unit; registry hash returns to prior value. | Prevents false provenance from becoming qualification evidence. |
| R-05 | `backend/app/tests/test_pine_semantic_preanalyzer.py`; fixture loop; lines 12-27; `backend/app/tests/pine_fixtures/r1a/*.expected.json` | Expected records use subset assertions and omit many temporal/code/hash fields. | Require complete expected documents and exact ordered/set equality; bind each to source, analyzer, and registry hashes. | None; R1B-0 only. | Revert expectation schema and tests before persistence flag enablement. | Detects source drift and unexpected extra/missing blockers or disclosures. |
| R-06 | `backend/app/db/models.py`; `StrategySignal`, `StrategyExecutionJob`, `WebhookEvent`; lines 191-219, 391-452; `backend/app/services/private_webhook_service.py`; persistence/history; lines 388-448, 505-585, 602-752 | One legacy signal row is updated with summary state; jobs retain normalized payload/result; no typed canonical intent exists. | Add dedicated decision and outcome tables. Keep `strategy_signals` unchanged and join it for the ledger. | One additive revision creates two new tables; no existing column rewrite. | Disable flags, deploy old app, then drop new tables. | Avoids copying credential-bearing legacy JSON and structurally binds decisions to owner/instance. |
| R-07 | `backend/app/routers/private_webhook.py`; endpoint; lines 53-84; `backend/app/services/private_webhook_service.py`; auth/lifecycle/ingest; lines 162-252, 388-448 | Pre-auth failures are deliberately ownerless; post-auth HTTP rejections exist only in response/audit paths. | Persist only authenticated, pre-acceptance rejections with safe codes/hashes. Never attach invalid credential probes to users. | Create `strategy_signal_rejections`. | Disable flag; retention job may delete rows; table can be dropped independently. | Preserves uniform 401/anti-enumeration and stores no credential/body/source. |
| R-08 | `backend/app/db/models.py`; `StrategySourceArtifact`/`StrategyValidationReport`; lines 651-725; `backend/app/services/personal_pine_service.py`; artifact/validation flow; lines 103-118, 187-221, 325-396 | Pine source and basic validation provenance exist, but semantic registry/analyzer provenance is in memory only. | Add immutable `pine_semantic_analyses` linked to the existing source artifact with an exact provenance tuple and closed bounded payload. | Create one new table; do not alter source storage. | Disable flag and fall back to in-memory analyzer; drop table on migration downgrade. | Full Pine source remains only in the existing artifact table; analysis payload excludes it. |
| R-09 | `backend/app/db/models.py`; existing open-string status columns throughout; migration `20260714_0011_personal_pine_validation.py` | Existing schema favors portable strings; PostgreSQL ENUMs are not used for these domains. | Use readable `VARCHAR` plus named `CHECK` constraints and application `StrEnum`s. | Portable constraints in the new migration; SQLite batch handling only where needed. | Drop tables/constraints without PostgreSQL enum-type cleanup. | Closed values block unreviewed authority-bearing strings. |
| R-10 | `backend/app/services/private_webhook_service.py`; `list_webhook_executions()`; lines 680-752 | Ledger-like history already uses direct owner-scoped joins and safe projections. | Extend this service projection in R1C; do not add a view/materialized/denormalized ledger in R1B. | None. | Ignore new tables when flags/read support are removed. | Reuses owner isolation and explicit safe-field projection. |
| R-11 | `backend/app/services/private_webhook_service.py`; job/HOLD payload creation; lines 505-585; `backend/app/workers/strategy_job_worker.py`; summary updates; lines 105-151 | Historical intent is partly deterministic; historical current state/routing chronology is not. | Use a separate offline idempotent backfill command after live writes are proven. Backfill only intent fields; leave state/outcome/reasons null or absent. | No migration-time data update. | Delete only rows marked with the selected backfill version if rollback is required. | Never invents user state or broker outcomes. |
| R-12 | `backend/alembic/versions/20260716_0014_verify_select.py`; revision metadata; lines 1-53; `backend/app/tests/test_migration_metadata.py`; revision guard; entire file | Current head is `0014_verify_select`; deployed version column is `VARCHAR(32)`. | Reserve `0015_r1b_persistence` (20 characters), create all four tables with flags off, and roll out code in later slices. | One additive, downgradeable migration from 0014. | Disable flags before downgrade; drop child tables in dependency order. | Schema-first/flags-off rollout keeps old/new app versions compatible. |

### 2.2 NB-01 — pure adapter constants

Exact R1B-0 file changes:

```text
ADD backend/app/domain/trading_constants.py
  DEFAULT_EXCHANGE_SEGMENT = "NSE_FNO"

CHANGE backend/app/domain/legacy_signal_adapter.py
  from app.domain.trading_constants import DEFAULT_EXCHANGE_SEGMENT

CHANGE backend/app/config.py
  remove local literal
  from app.domain.trading_constants import DEFAULT_EXCHANGE_SEGMENT
```

Only the adapter must switch immediately. Other runtime modules may continue
importing the re-export from `app.config`, avoiding a broad import-only diff.
Tests must assert that importing the adapter does not import/construct
`app.config.settings` and that compatibility output remains field-identical.

### 2.3 NB-02 — formal secret-taint policy

Credential-shaped means a case-insensitive known credential prefix such as
`nwk_` followed by token characters, or a value equal to a known secret in the
request-local taint set. Prefix scanning is defense in depth; equality to the
known credential is authoritative when the request context has it.

| Field | Class | Storage policy | Logging policy | User return | Maximum/validation | Redaction |
|---|---|---|---|---|---|---|
| credential | `SECRET` | Existing credential SHA-256 only; never new tables | Never | Plaintext only at existing one-time issue flow | Existing ingress max 128 | Always `[REDACTED]` |
| signal_id | `USER_IDENTIFIER` | Existing legacy rows only; canonical rows link to `StrategySignal`; rejection may store safe text or SHA-256 | Never as a new log/metric label | Existing API unchanged in R1B; future canonical ledger redacts secret-shaped value | Existing max 128; no new ingress restriction | `credential-shaped-id` in new projections |
| strategy_version | `USER_METADATA` | Optional canonical `safe_metadata` after validation | Never | Future safe projection only | Existing max 40; allow printable identifier characters; otherwise omit | Omit invalid/secret-shaped value |
| timeframe | `USER_METADATA` | Optional canonical `safe_metadata` | Never as metric label | Future safe projection | Existing max 12; letters/digits and standard timeframe punctuation only | Omit invalid/secret-shaped value |
| reference_price | `USER_METADATA` | Optional numeric `safe_metadata`; never authority | Never as label | Future optional display | Finite positive number only | Omit invalid |
| comment | `USER_METADATA` | Do not copy into canonical/rejection tables; remains in legacy job payload | Never | Existing behavior unchanged; exclude from new ledger | Existing max 200 | Omit from new persistence |
| user_id | `SERVER_TRUSTED` | Required owner binding in decision/rejection tables | Never as metric label; existing structured audit policy unchanged | Only implicit authenticated ownership | UUID | Not exposed cross-tenant |
| instance_id | `SERVER_TRUSTED` | Required composite owner binding | Never as metric label | Own instance identifier may be returned | UUID | Not exposed cross-tenant |
| source_sha256 | `DERIVED_SAFE` | Required analysis provenance | Admin/audit only, not metric label | Existing source workflows may return it | Exactly 64 lowercase hex | None |
| registry_sha256 | `DERIVED_SAFE` | Required analysis provenance | Admin/audit only, not metric label | Not exposed to normal users in R1B | Exactly 64 lowercase hex | None |
| payload fingerprint | `DERIVED_SAFE` | Decision/rejection hash only | Do not label metrics; diagnostic prefix only if needed | Admin only | Exactly 64 lowercase hex | None |
| error detail | `DERIVED_SAFE` | Closed code plus server-authored template only | Code only | Existing public error remains unchanged | Detail max 160; no raw exception/free text | Replace unknown detail with closed generic text |

`safe_metadata` is a closed JSON object with only validated
`strategy_version`, `timeframe`, and `reference_price`; encoded size is at most
2 KiB. It never contains `comment`, `signal_id`, credential, owner IDs, broker
data, quantities, risk, contracts, or arbitrary nested data.

R1B does **not** reject an existing webhook merely because `signal_id` looks
credential-shaped. That would change compatibility. The value remains only in
legacy records, is not recopied into canonical evidence, and is redacted from
new R1C projections. A stricter ingress rule requires a separate compatibility
phase.

### 2.4 NB-03 — confidence semantics

| Confidence | Exact meaning | Severity rule | Persistence/display |
|---|---|---|---|
| `HIGH_CONFIDENCE_MATCH` | Deterministic lexical/structural pattern with a registry-backed classification. | Most restrictive proven match applies. | Persist internally; admin-only in R1B. |
| `PARTIAL_MATCH` | One or more meaningful constructs are detected, but complete parameters or control flow cannot be proven. | May add disclosure/blocking; never reduces severity and never promotes a source to L0/L1 by itself. If no stronger safe proof exists, effective level is at least L2, or the registry's stricter matched level. | Persist internally with the exact matched evidence; admin-only. |
| `ANALYSIS_INDETERMINATE` | Required meaning, availability, or compile status cannot safely be established. | Never defaults to safe. Preserve the most restrictive proven match; with no proven match, fail closed to L4/T9 as today. | Persist internally; normal users receive only the safe blocker/disclosure projection in later phases. |

`effective_capability_level` and `confidence` are independent. Level answers
"what restriction applies"; confidence answers "how completely did this
analyzer establish the evidence." A lower-confidence result cannot override a
more restrictive high-confidence match.

### 2.5 NB-04 — fixture-provenance corrections

The new/corrected fixtures should be minimal and dedicated:

| Capability | Fixture | Minimum Pine source | Expected level | Temporal | Blocker/disclosure | Confidence | Registry update |
|---|---|---|---|---|---|---|---|
| `PROCESS_ORDERS_ON_CLOSE` | F39 | `strategy("F39", process_orders_on_close=true)` plus one confirmed `strategy.entry` | L2 | T1 | `DISC_ORDER_CLOSE_TIMING` | `HIGH_CONFIDENCE_MATCH` | Replace F05 with F39 |
| `STRATEGY_ORDER_SEMANTICS` | F67 | `strategy("F67")` and `strategy.order("L", strategy.long)` | L3 | T6 | `BLK_ORDER_SEMANTICS` | `HIGH_CONFIDENCE_MATCH` | Replace F13 with F67 |
| `PINE_CONTROLLED_ENTRY_QUANTITY` | F28 | `strategy.entry("L", strategy.long, qty=2)` | L2 | T0 | `DISC_SERVER_OWNED_SIZING` | `HIGH_CONFIDENCE_MATCH` | Replace F31 with F28 |
| `POSITION_STATE_REFERENCE` | F34 | condition on `strategy.position_size` plus an entry | L3 | T6 | `BLK_FILL_DEPENDENT` | `HIGH_CONFIDENCE_MATCH` | Replace F36 with F34 |
| `PENDING_ORDER_CANCELLATION` | F15 | `strategy.cancel_all()` in a strategy | L3 | T6 | `BLK_PENDING_ENGINE` | `HIGH_CONFIDENCE_MATCH` | Replace F13 with F15 |

Expected manifests must also list every incidental/base capability matched by
the minimal source. The focal capability above is not a license for subset
assertions.

### 2.6 NB-05 — exhaustive expected-fixture contract

Every expected file must use:

```json
{
  "fixture_id": "Fxx",
  "source_sha256": "64-lowercase-hex",
  "analyzer_version": "nova.pine-semantic-preanalyzer.vN",
  "registry_id": "nova.pine-capabilities",
  "registry_version": "vN",
  "registry_sha256": "64-lowercase-hex",
  "matched_capabilities_exact": [],
  "effective_capability_level": "Lx_...",
  "temporal_classes_exact": [],
  "blocker_codes_exact": [],
  "disclosure_codes_exact": [],
  "admin_review_points_exact": [],
  "confidence": "..."
}
```

Rules:

- `fixture_id` must match the filename prefix.
- Source SHA is SHA-256 of the exact committed UTF-8 bytes, matching
  `analyze_source()` at `pine_semantic_preanalyzer.py:343-350`.
- Arrays are deduplicated and lexicographically sorted before comparison and
  serialization.
- Tests assert exact equality, not subsets, for every array and scalar.
- The expected JSON itself is canonicalized with sorted keys, compact
  separators, UTF-8, and one repository-standard trailing newline when a
  separate expectation hash is needed.
- A registry/analyzer change must update the registry fixture citation,
  implementation test, all affected expected records, and a documented hash
  review in one commit.
- Any source, registry, analyzer, or expected-value mismatch fails CI. CI must
  not regenerate expected files automatically.

## 3. Persistence-option comparison

| Property | Option A — columns on `strategy_signals` | Option B — dedicated tables | Option C — hybrid |
|---|---|---|---|
| Query simplicity | Simplest for intent fields | One indexed join | Simple for duplicated fields |
| Historical rows | Many nullable columns | Zero related rows | Nullable columns plus zero rows |
| Legacy row growth | Permanent wide row | None | Permanent partial growth |
| Write coupling | Canonical write touches authoritative legacy transaction/row | Separate fail-isolated transaction | Both coupled and separate |
| Immutability | Poor: `StrategySignal.result_summary/status` are updated | Strong insert-only model | Full record immutable, duplicated columns mutable |
| Multiple outcomes/retries | Awkward or JSON | Natural append-only child rows | Child rows still required |
| Versioning | One set of columns | Provenance lives with record | Duplicate-version ambiguity |
| Rollback | Drop columns, with rolling-deploy hazards | Disable reads/writes; drop isolated tables | Most complex |
| Future ledger | Direct row plus job | Indexed service joins | Direct plus join |
| SQLite/PostgreSQL migration | Batch alter risk | Straight table creation | Both risks |
| Secret duplication | Easy to copy legacy metadata | New table can omit it | Duplication risk |

**Recommendation: Option B.** One owner-scoped indexed join is cheaper and
safer than duplicating evidence into a row whose summary is intentionally
mutable. R1C query volume does not justify denormalization.

## 4. Recommended schema

| Table | Cardinality | Parent | Purpose | Authority |
|---|---|---|---|---|
| `canonical_signal_decisions` | 0..1 per `strategy_signals` row | `StrategySignal` | Immutable normalized intent and compatibility evidence | Evidence only |
| `canonical_signal_outcomes` | 0..N per canonical decision | decision; optional execution job | Truthful append-only state/routing chronology | Evidence only |
| `strategy_signal_rejections` | 0..N per authenticated instance | trusted instance; optional webhook event | Safe post-auth pre-acceptance rejection audit | Evidence only |
| `pine_semantic_analyses` | 0..N per source artifact | `StrategySourceArtifact` | Immutable analyzer/registry/source provenance | Qualification evidence, never execution authority |

**Proposed `strategy_signals` columns: none.**

This keeps existing rows readable by old code, avoids SQLite table recreation,
and allows all four new tables to be dropped without reconstructing a legacy
table.

## 5. Canonical decision model

### 5.1 `canonical_signal_decisions` columns

| Column | Database type / nullable | Source and trust | Index / uniqueness | Retention | User/admin visibility | Logging |
|---|---|---|---|---|---|---|
| `id` | `GUID`, NOT NULL PK | Server-generated; `SERVER_TRUSTED` | PK | Parent lifetime | Internal/admin | Never label |
| `strategy_signal_id` | `GUID`, NOT NULL FK `strategy_signals.id ON DELETE CASCADE` | Existing committed row; `SERVER_TRUSTED` | UNIQUE | Parent lifetime | Internal/admin | Never |
| `webhook_event_id` | `GUID`, NULL FK `webhook_events.id ON DELETE SET NULL` | Replay lookup; `SERVER_TRUSTED` | Index | May null when replay record is pruned | Admin | Never |
| `user_id` | `GUID`, NOT NULL | Auth context; `SERVER_TRUSTED` | Composite owner FK; index with received time | Parent/account lifetime | Own-data scope/admin | Never label |
| `strategy_instance_id` | `GUID`, NOT NULL | Auth context; `SERVER_TRUSTED` | Composite FK with `user_id`; index | Parent/account lifetime | Own instance/admin | Never label |
| `contract_version` | `VARCHAR(40)`, NOT NULL | Canonical event; `DERIVED_SAFE` | Check/non-empty | Row lifetime | Internal/admin | Safe counter value only if bounded |
| `adapter_version` | `VARCHAR(50)`, NOT NULL | Adapter constant; `DERIVED_SAFE` | Index with contract version only if operationally needed | Row lifetime | Admin | Safe bounded label |
| `wire_action` | `VARCHAR(20)`, NOT NULL | Server-normalized action; `DERIVED_SAFE` | CHECK four actions; index with instance/time | Row lifetime | Future ledger/admin | Safe bounded label |
| `event_type` | `VARCHAR(30)`, NOT NULL | Adapter | CHECK | Row lifetime | Future ledger/admin | Safe bounded label |
| `desired_state` | `VARCHAR(20)`, NOT NULL | Adapter | CHECK | Row lifetime | Future ledger/admin | Safe bounded label |
| `intent_reason` | `VARCHAR(40)`, NOT NULL | Adapter | CHECK | Row lifetime | Future ledger/admin | Safe bounded label |
| `signal_time` | timezone-aware `DateTime`, NOT NULL | Validated wire value; `USER_METADATA` | No standalone index | Row lifetime | Future ledger/admin | Never |
| `received_at` | timezone-aware `DateTime`, NOT NULL | Server clock; `SERVER_TRUSTED` | `(user_id, received_at)` and `(strategy_instance_id, received_at)` | Row lifetime | Future ledger/admin | Never |
| `compatibility_action` | `VARCHAR(20)`, NULL | Adapter compatibility output; `DERIVED_SAFE` | CHECK `ENTRY/EXIT`; NULL for HOLD | Row lifetime | Admin/future ledger | Safe bounded label |
| `compatibility_side` | `VARCHAR(10)`, NULL | Adapter compatibility output | CHECK `BUY/SELL`; NULL for HOLD | Row lifetime | Admin | Safe bounded label |
| `compatibility_option_side` | `VARCHAR(8)`, NULL | Adapter compatibility output | CHECK `CE/PE`; NULL for EXIT/HOLD | Row lifetime | Admin/future ledger | Safe bounded label |
| `source` | `VARCHAR(50)`, NOT NULL | Server constant | CHECK approved source | Row lifetime | Future ledger/admin | Safe bounded label |
| `safe_metadata` | portable JSON, NULL | Sanitized allowlist; `USER_METADATA` | No JSON index | Row lifetime | Admin; selected R1C fields only | Never |
| `payload_fingerprint` | `CHAR(64)`, NOT NULL | Existing replay fingerprint; `DERIVED_SAFE` | No standalone index | Row lifetime | Admin | Never label |
| `provenance_kind` | `VARCHAR(20)`, NOT NULL | Writer | CHECK `LIVE/BACKFILL` | Row lifetime | Admin | Safe bounded label |
| `backfill_version` | `VARCHAR(30)`, NULL | Offline backfill | Index only if backfill cleanup requires it | Row lifetime | Admin | Safe bounded value |
| `created_at` | timezone-aware `DateTime`, NOT NULL | Server clock | Index through parent/time queries | Row lifetime | Admin | Never |

Raw `signal_id` is deliberately omitted; it is already available through
`StrategySignal` and may be secret-shaped. `current_state`, `routing_result`,
`no_op_reason`, and `exit_reason` are also omitted because they are not known
at adapter time.

Table constraints:

- `UNIQUE(strategy_signal_id)` establishes one historical interpretation for
  the signal at the version actually used.
- Composite FK
  `(strategy_instance_id, user_id) -> strategy_instances(id, user_id)
  ON DELETE CASCADE` structurally preserves tenant ownership.
- HOLD requires `event_type=CONNECTIVITY_TEST`, `desired_state=NONE`,
  compatibility fields NULL.
- Trading events require `event_type=STRATEGY_SIGNAL` and a non-null
  compatibility action.

Migration impact is isolated table creation. Rollback drops the table after
outcomes. Security impact is positive: no credential, comment, raw body, raw
signal ID, quantity, risk, contract selection, or broker mode is copied.

## 6. Connectivity-event model

HOLD remains a normal `StrategySignal` because existing setup verification
queries it (`backend/app/services/tradingview_setup_service.py:281-309`) and
existing history expects it. It receives a canonical decision:

```text
wire_action = HOLD
event_type = CONNECTIVITY_TEST
desired_state = NONE
intent_reason = CONNECTIVITY_TEST
compatibility_* = NULL
```

It also receives one outcome:

```text
phase = ROUTING_COMPLETED
routing_result = CONNECTIVITY_NO_JOB
current_state = NULL
no_op_reason = CONNECTIVITY_TEST
execution_job_id = NULL
```

Connectivity events:

- remain linked to the original replay/WebhookEvent when available;
- remain setup-verification evidence;
- appear in the future ledger with a connectivity/trading filter;
- never create a `NormalizedSignal`, execution job, queue, worker call, or
  broker call;
- use existing duplicate/conflict behavior.

If canonical persistence fails, current HOLD persistence and setup evidence
still succeed. This follows R-06 and has no migration impact beyond the new
tables; rollback returns to the existing HOLD-only representation without data
or execution loss.

## 7. Rejection model

### 7.1 Policy comparison

| Policy | Coverage | Privacy/noise | Decision |
|---|---|---|---|
| Every rejection, including pre-auth | Maximum | Unsafe owner attribution and credential-probe retention | Reject |
| Only canonical/semantic rejections | Narrow | Misses lifecycle/time/replay operational evidence | Reject |
| Only security-relevant rejections | Narrow and subjective | Incomplete operational history | Reject |
| Every **post-auth, pre-acceptance** rejection | Complete once owner is trusted | Bounded and attributable | Recommend |

Schema/body/content-type failures that occur before successful credential
resolution are not stored. Existing uniform 401 and anti-enumeration behavior
remain unchanged. Worker/job failures are already durable on
`StrategyExecutionJob`; do not duplicate them as ingress rejections.

### 7.2 `strategy_signal_rejections` columns

| Column | Database type / nullable | Source and trust | Index / uniqueness | Retention | User/admin visibility | Logging |
|---|---|---|---|---|---|---|
| `id` | `GUID`, NOT NULL PK | Server | PK | 30 days default | Admin only in R1B | Never |
| `user_id` | `GUID`, NOT NULL | Successful auth | Composite owner FK | 30 days/account deletion | Admin; future own safe projection only | Never label |
| `strategy_instance_id` | `GUID`, NOT NULL | Successful auth | `(instance, received_at)` | Same | Admin | Never label |
| `webhook_event_id` | `GUID`, NULL FK `SET NULL` | Replay record if one exists | Index | May null on replay pruning | Admin | Never |
| `signal_id_safe` | `VARCHAR(128)`, NULL | Validated non-secret-shaped ID only | No index | 30 days | Admin | Never |
| `signal_id_sha256` | `CHAR(64)`, NULL | Hash of validated ID | No index | 30 days | Admin | Never |
| `payload_fingerprint` | `CHAR(64)`, NULL | Existing safe canonical payload hash when computable | No index | 30 days | Admin | Never |
| `stage` | `VARCHAR(40)`, NOT NULL | Server catch point | CHECK; index with time | 30 days | Admin | Safe bounded label |
| `rejection_code` | `VARCHAR(60)`, NOT NULL | Closed server code | CHECK; index with time | 30 days | Safe code may be user-visible later | Safe bounded label |
| `safe_detail` | `VARCHAR(160)`, NULL | Closed server template only | None | 30 days | Admin | Never as label/free text |
| `adapter_version` | `VARCHAR(50)`, NULL | Server version if adapter ran | None | 30 days | Admin | Safe bounded label |
| `contract_version` | `VARCHAR(40)`, NULL | Server version if adapter ran | None | 30 days | Admin | Safe bounded label |
| `dedupe_key` | `CHAR(64)`, NOT NULL | Hash of instance, safe fingerprint/ID hash, stage, code | UNIQUE | 30 days | Internal | Never |
| `received_at` | timezone-aware `DateTime`, NOT NULL | Request server clock | `(user_id, received_at)` | 30 days | Admin | Never |
| `created_at` | timezone-aware `DateTime`, NOT NULL | DB/server clock | None | 30 days | Admin | Never |

Initial stages:

```text
LIFECYCLE
SIGNAL_TIME
REPLAY_CONFLICT
CANONICAL_NORMALIZATION
SEMANTIC_POLICY
JOB_CREATION
```

`POST_AUTH_SCHEMA_POLICY` is reserved but not written until a separately
reviewed two-stage parser can authenticate before complete schema validation.
Canonical normalization cannot reject a valid legacy webhook while canonical
authority remains disabled; such failures are shadow metrics, not rejection
rows.

Initial codes are the current safe server codes where applicable:
`INVALID_ACTION`, `MISSING_TIMEZONE`, `STALE_SIGNAL`, `INACTIVE_INSTANCE`,
`LIVE_EXECUTION_SAFETY_BLOCK`, `CONFLICTING_DUPLICATE`, `STORE_UNAVAILABLE`,
and `JOB_PERSISTENCE_FAILED`. Adding codes requires an application enum,
database check migration, and API/security review.

No credential, full payload, raw body, broker secret, email, Pine source, raw
exception, or hostile metadata is stored. Insert failure is logged as a
counter/code and never changes the current HTTP response.

## 8. Semantic-analysis provenance model

### 8.1 `pine_semantic_analyses` columns

| Column | Database type / nullable | Source and trust | Index / uniqueness | Retention | User/admin visibility | Logging |
|---|---|---|---|---|---|---|
| `id` | `GUID`, NOT NULL PK | Server | PK | Source-artifact lifetime | Internal/admin | Never |
| `source_artifact_id` | `GUID`, NOT NULL FK `strategy_source_artifacts.id ON DELETE CASCADE` | Existing immutable artifact | Index | Artifact/account lifetime | Owner-bound through version; admin | Never |
| `source_sha256` | `CHAR(64)`, NOT NULL | Recomputed exact UTF-8 source hash | Provenance unique tuple | Row lifetime | Existing workflows may expose source hash; admin | Never label |
| `analyzer_version` | `VARCHAR(50)`, NOT NULL | Analyzer constant | Provenance unique tuple | Row lifetime | Admin | Safe bounded label |
| `registry_id` | `VARCHAR(60)`, NOT NULL | Validated registry | Provenance unique tuple | Row lifetime | Admin | Safe bounded label |
| `registry_version` | `VARCHAR(30)`, NOT NULL | Validated registry | Provenance unique tuple | Row lifetime | Admin | Safe bounded label |
| `registry_sha256` | `CHAR(64)`, NOT NULL | Canonical registry hash | Provenance unique tuple | Row lifetime | Admin | Never metric label |
| `analysis_schema_version` | `VARCHAR(30)`, NOT NULL | Persistence serializer constant | Provenance unique tuple | Row lifetime | Admin | Safe bounded label |
| `effective_capability_level` | `VARCHAR(60)`, NOT NULL | Analyzer result | CHECK; index | Row lifetime | Safe projected level only after R1C review; admin | Safe bounded label |
| `confidence` | `VARCHAR(40)`, NOT NULL | Analyzer result | CHECK | Row lifetime | Admin only in R1B | Safe bounded label |
| `analysis_payload` | portable JSON, NOT NULL | Closed serializer; `DERIVED_SAFE` | No JSON index initially | Row lifetime | Admin; R1C safe projection only | Never |
| `analysis_payload_sha256` | `CHAR(64)`, NOT NULL | Canonical JSON hash | Integrity check, not standalone index | Row lifetime | Admin | Never label |
| `supersedes_analysis_id` | `GUID`, NULL self-FK `ON DELETE SET NULL` | Explicit reanalysis chain | Index | Row lifetime | Admin | Never |
| `created_at` | timezone-aware `DateTime`, NOT NULL | Server clock | `(source_artifact_id, created_at)` | Row lifetime | Admin | Never |

Unique provenance tuple:

```text
(
  source_artifact_id,
  source_sha256,
  analyzer_version,
  registry_id,
  registry_version,
  registry_sha256,
  analysis_schema_version
)
```

The writer recomputes the source SHA and requires equality with
`StrategySourceArtifact.content_sha256` before analysis and again before
insert. A different analyzer, registry version/hash, or schema creates a new
row. An identical tuple reuses the existing row. No update API exists;
reanalysis inserts and optionally references `supersedes_analysis_id`.

`analysis_payload` is a closed object containing only exact sorted:

```text
matched_capabilities
temporal_classes
blocker_codes
disclosure_codes
admin_review_points
```

plus no source text. Encoded canonical JSON is limited to 64 KiB.
`feature_vector` is deliberately omitted in R1B: the current result already
contains sufficient evidence, and storing lexer fragments risks Pine-source
disclosure. Add a separately reviewed bounded feature vector only when an
actual admin query requires it.

`pine_version_id` is also deliberately not duplicated: it is deterministically
available through `source_artifact_id -> StrategySourceArtifact.strategy_version_id`.
This avoids a second FK value that could disagree with the artifact actually
hashed and analyzed.

Semantic row insert failure blocks qualification persistence, not webhook or
order execution. Registry unavailability remains durable as L4 indeterminate
only when the closed failure result and provenance can be safely represented;
otherwise the qualification operation fails closed without approving source.

## 9. Enum/check-constraint design

Use Python `StrEnum` plus `VARCHAR` and named `CHECK` constraints. Do not use
PostgreSQL ENUMs, small integers, or JSON-only codes. Strings are readable and
cross-language; checks are strong and portable; additions use an ordinary
constraint migration with a clean downgrade.

| Domain | Initial stored values |
|---|---|
| `CanonicalEventType` | `STRATEGY_SIGNAL`, `CONNECTIVITY_TEST` |
| `DesiredPositionState` | `BULLISH`, `BEARISH`, `FLAT`, `NONE` |
| `CanonicalIntentReason` | `DIRECTIONAL_SIGNAL`, `EXPLICIT_EXIT`, `CONNECTIVITY_TEST` |
| `CanonicalRoutingResult` | `NOT_EVALUATED`, `CONNECTIVITY_NO_JOB`, `STATE_NO_OP`, `ENTRY_ROUTED`, `EXIT_ROUTED`, `REVERSAL_ROUTED`, `BLOCKED`, `FAILED`, `PENDING`, `PARTIAL` |
| `CanonicalNoOpReason` | `CONNECTIVITY_TEST`, `ALREADY_FLAT`, `ALREADY_BULLISH`, `ALREADY_BEARISH`, `INSTANCE_PAUSED` |
| `CanonicalCurrentState` | `UNKNOWN`, `FLAT`, `BULLISH`, `BEARISH` |
| `CanonicalExitReason` | `EXPLICIT_EXIT`, `REVERSAL_EXIT`, `STOP_LOSS`, `TAKE_PROFIT`, `TRAILING_STOP`, `SESSION_EXIT`, `MANUAL_EXIT`, `RISK_EXIT` |
| `SignalRejectionStage` | values in section 7 |
| `SignalRejectionCode` | reviewed current safe codes in section 7 |
| `AnalysisConfidence` | `HIGH_CONFIDENCE_MATCH`, `PARTIAL_MATCH`, `ANALYSIS_INDETERMINATE` |
| `CapabilityLevel` | the five current L0-L4 registry values |
| `TemporalClass` | the ten current T0-T9 registry values; stored inside closed analysis JSON |

Do not collapse intent reason, routing result, exit reason, and no-op reason.
They are known at different times and have different trust sources.

## 10. Write sequence

### 10.1 Accepted trading signal

```text
unchanged request parsing/auth/lifecycle/time validation
-> unchanged replay claim
-> unchanged StrategySignal + StrategyExecutionJob transaction commits
-> if CANONICAL_SIGNAL_PERSISTENCE:
     separate idempotent canonical decision transaction
-> unchanged 202 response and worker wake-up
-> worker claims/revalidates existing job
-> if outcome flag:
     append STATE_EVALUATED after actual JSON-authority read
-> unchanged dispatch -> execution_router -> broker path
-> existing job completion/result transaction
-> if outcome flag:
     append final routing outcome from the returned trusted result
```

The separate decision transaction intentionally permits a missing evidence row
if its write fails. A missing non-authoritative row is safer than rolling back
or delaying the existing job. Duplicate delivery may retry the decision insert;
`UNIQUE(strategy_signal_id)` makes it idempotent.

### 10.2 HOLD

```text
unchanged replay claim
-> unchanged StrategySignal HOLD transaction
-> separate decision insert
-> separate CONNECTIVITY_NO_JOB outcome insert
-> unchanged 202 response/setup evidence
```

No queue or execution side effect is added.

### 10.3 Rejections

After successful auth, the owning router/service catch point attempts one
separate deduplicated rejection insert. Failure is swallowed after a safe
counter. The public status, reason, and body are the existing values.

### 10.4 Pine analysis

```text
load owner-scoped immutable source artifact
-> recompute and verify source SHA
-> load/verify registry and hash
-> deterministic analyzer
-> serialize closed bounded payload and hash it
-> reuse exact provenance tuple or insert immutable row
-> only then allow downstream qualification logic to reference that row
```

## 11. Routing-outcome capture

### 11.1 Capture-point comparison

| Point | Truth available | Problem |
|---|---|---|
| A — webhook before job | Requested state only | Cannot know current state, no-op, reversal, or broker result |
| B — worker before routing | Fresh JSON-authority current state and lifecycle | Cannot claim routing result yet |
| C — router result | Actual high-level route/broker result | One mutable record loses chronology/retries |
| D — append at B and C | Truth at each time | One extra table/join |

**Recommend D**, implemented through `canonical_signal_outcomes`.

### 11.2 `canonical_signal_outcomes` columns

| Column | Database type / nullable | Source and trust | Index / uniqueness | Retention | User/admin visibility | Logging |
|---|---|---|---|---|---|---|
| `id` | `GUID`, NOT NULL PK | Server | PK | Decision lifetime | Internal/admin | Never |
| `canonical_decision_id` | `GUID`, NOT NULL FK `canonical_signal_decisions.id ON DELETE CASCADE` | Existing decision | `(decision, created_at)` | Decision lifetime | Internal/admin | Never |
| `execution_job_id` | `GUID`, NULL FK `strategy_execution_jobs.id ON DELETE SET NULL` | Worker job | Index | May null if job is deleted | Admin/future ledger | Never |
| `attempt` | `INTEGER`, NULL | Existing job attempt | CHECK positive | Row lifetime | Admin | Safe aggregate only |
| `phase` | `VARCHAR(30)`, NOT NULL | Writer point | CHECK `STATE_EVALUATED/ROUTING_COMPLETED/ROUTING_FAILED` | Row lifetime | Future ledger/admin | Safe bounded label |
| `current_state` | `VARCHAR(20)`, NULL | Actual JSON authority read | CHECK | Row lifetime | Future ledger/admin | Safe bounded label |
| `routing_result` | `VARCHAR(30)`, NOT NULL | Trusted no-op/router result | CHECK | Row lifetime | Future ledger/admin | Safe bounded label |
| `no_op_reason` | `VARCHAR(40)`, NULL | Trusted state/router result | CHECK | Row lifetime | Future ledger/admin | Safe bounded label |
| `exit_reason` | `VARCHAR(40)`, NULL | Trusted backend source/result | CHECK | Row lifetime | Future ledger/admin | Safe bounded label |
| `safe_detail_code` | `VARCHAR(60)`, NULL | Closed translator output | CHECK/allowlist | Row lifetime | Future safe projection/admin | Safe bounded label |
| `result_sha256` | `CHAR(64)`, NULL | Hash of canonicalized safe result projection | None | Row lifetime | Admin | Never |
| `idempotency_key` | `CHAR(64)`, NOT NULL | Hash of decision/job/attempt/phase/result | UNIQUE | Row lifetime | Internal | Never |
| `created_at` | timezone-aware `DateTime`, NOT NULL | Server clock | `(decision, created_at)` | Row lifetime | Future ledger/admin | Never |

Requested state is joined from `canonical_signal_decisions.desired_state`; it
is not duplicated. Raw broker response, full job result, and position snapshot
remain in existing authoritative/operational stores.

Crash windows remain honest:

- crash before state outcome: no state row;
- crash after broker side effect before final outcome: job stays
  running/recovery-required and no final outcome is invented;
- retry: idempotency key prevents duplicate evidence;
- partial/pending results are appended as `PARTIAL`/`PENDING`, never promoted
  to success;
- reversal outcome is recorded only after the router returns its actual
  reversal status.

## 12. Read/ledger projection

R1B should use an owner-scoped service projection with direct indexed joins:

```text
StrategySignal
LEFT JOIN CanonicalSignalDecision
LEFT JOIN latest/ordered CanonicalSignalOutcome
LEFT JOIN StrategyExecutionJob
LEFT JOIN existing job/result/order/position evidence as needed
```

Extend the pattern in
`private_webhook_service.list_webhook_executions()` (lines 680-752) only in
R1C. Do not create a database view, materialized view, denormalized ledger
table, or frontend policy logic in R1B.

The future projection can provide wire action, event/requested/current states,
intent/routing/no-op/exit reasons, job/order status, contract from the existing
safe job projection, times, strategy version, and analysis provenance. A
server-side filter selects `CONNECTIVITY_TEST` versus `STRATEGY_SIGNAL`.

Migration impact: none beyond indexed source tables. Rollback: ignore new
joins. Security: reuse `_owned_instance()` and explicit safe-field selection;
never serialize `safe_metadata` or analysis payload wholesale.

## 13. Backfill policy

| Option | Risk | Decision |
|---|---|---|
| No backfill | Safest, but historical ledger has no intent labels | Acceptable fallback |
| Lazy request-time backfill | Mutates on reads and creates races | Reject |
| Migration-time backfill | Long/fragile migration; invents pressure | Reject |
| Offline explicit command | Reviewable, restartable, rate-limited | Recommend after live proof |

The offline command:

- runs only after R1B-2 live intent writes are stable;
- selects signals without a decision;
- derives trading action from immutable job `raw_payload.normalized_action`;
- derives HOLD only from the exact legacy HOLD summary and absence of a job;
- applies the frozen four-action mapping;
- records `provenance_kind=BACKFILL` and a reviewed `backfill_version`;
- uses `UNIQUE(strategy_signal_id)` for idempotency;
- leaves webhook link null if not deterministically found;
- creates no outcome rows and never reconstructs current state, routing result,
  no-op reason, exit reason, broker status, or timing;
- produces dry-run counts/hash and supports bounded batches.

Schema creation and backfill are separate releases. Backfilled rows remain
evidence only. Rollback may delete rows by `backfill_version` without touching
legacy data.

## 14. Migration specification

### 14.1 Revision identity

```text
Proposed file: backend/alembic/versions/20260718_0015_r1b_persistence.py
revision:      0015_r1b_persistence
length:        20
down_revision: 0014_verify_select
```

Twenty characters fit the deployed `VARCHAR(32)` revision column.

### 14.2 Upgrade order

1. Create `canonical_signal_decisions`.
2. Create its owner/time and instance/time indexes.
3. Create `canonical_signal_outcomes`.
4. Create outcome decision/time and optional job indexes.
5. Create `strategy_signal_rejections`.
6. Create rejection instance/time, user/time, stage/code/time indexes.
7. Create `pine_semantic_analyses`.
8. Create analysis artifact/time and capability-level indexes.
9. Leave all feature flags false; perform no data backfill.

New standalone-table identity/provenance columns are NOT NULL because no
historical row must be populated. Optional/stage-late fields are nullable. No
NOT NULL column is added to an existing table.

### 14.3 Constraints and foreign-key behavior

| Table | Unique/check constraints | Foreign keys |
|---|---|---|
| decisions | `uq_canonical_decision_signal`; checks for action/event/state/reason/compatibility/source/provenance and HOLD/trading consistency | signal `CASCADE`; webhook `SET NULL`; composite instance-owner `CASCADE` |
| outcomes | `uq_canonical_outcome_idempotency`; checks for phase/state/result/reasons/positive attempt | decision `CASCADE`; job `SET NULL` |
| rejections | `uq_signal_rejection_dedupe`; checks for stage/code/hash lengths | composite instance-owner `CASCADE`; webhook `SET NULL` |
| analyses | `uq_pine_analysis_provenance`; checks for SHA lengths/level/confidence/payload size in application | artifact `CASCADE`; supersedes `SET NULL` |

Do not use destructive cascades from webhook events because replay pruning is
independent. User/strategy deletion reaches decisions/rejections/analyses
through their trusted parent and removes personal data.

### 14.4 Downgrade order

1. Require all four flags false and old-compatible app deployed.
2. Drop analysis indexes/table.
3. Drop rejection indexes/table.
4. Drop outcome indexes/table.
5. Drop decision indexes/table.
6. Alembic records `0014_verify_select`.

No existing table or row is rewritten.

### 14.5 Required migration verification

- PostgreSQL 18 upgrade `0014 -> 0015`.
- PostgreSQL downgrade `0015 -> 0014`.
- PostgreSQL upgrade again.
- Fresh PostgreSQL migration chain.
- SQLite upgrade/downgrade/upgrade for portable schema behavior.
- Existing `StrategySignal`, job, webhook, source, instance, and position rows
  remain readable.
- Optional new fields accept null.
- uniqueness/check/FK/cascade behavior is tested.
- revision ID length test asserts 20 and head/down-revision.
- JSON position authority and PostgreSQL position-shadow behavior are
  unchanged.

## 15. Privacy and retention

| Data | Retention/deletion | Privacy controls |
|---|---|---|
| Canonical decisions/outcomes | Same lifecycle as parent `StrategySignal`/instance; no independent permanent retention | Cascade on account/instance deletion; no raw secret/body/comment |
| Rejections | Default 30 days, configurable; hard delete in bounded job | Post-auth only; hash/redact identifiers; admin-only |
| Semantic analyses | Same lifecycle as source artifact/version; reanalysis rows retained while parent exists | Cascade on source/account deletion; payload excludes source |
| Safe metadata | Same as decision; 2 KiB maximum | Closed allowlist and taint scan |
| Analysis payload | Same as analysis; 64 KiB maximum | Closed schema; no source/feature fragments |
| Logs/metrics | Existing operational retention; no durable identity labels | Codes/version only; no IDs/free text |

Immutable means "not edited in place while retained," not "exempt from account
deletion or privacy law." A legal hold must be an explicit external governance
process; this schema must not silently defeat deletion.

Admin access uses existing authenticated authorization and is audited. Normal
users receive no raw analysis payload, feature vector, rejection detail,
fingerprint, registry hash, or safe metadata in R1B. Exports use the later safe
ledger projection, not table dumps. A CI/runtime secret scan covers migration
fixtures and structured JSON.

## 16. Failure isolation

| Failure | Required behavior | Metric/log | Rollback |
|---|---|---|---|
| Decision insert fails | Existing signal/job/HOLD and response continue | failure counter + closed DB error class | Disable intent flag |
| Outcome insert fails | Existing job/router result remains authoritative | outcome failure counter | Disable outcome flag |
| Rejection insert fails | Existing public rejection unchanged | rejection-write failure counter | Disable rejection flag |
| Analysis insert fails | Do not approve/persist qualification reference; webhook/execution unaffected | analysis failure counter | Disable analysis persistence |
| Registry unavailable | Analyzer remains conservative L4/indeterminate; no execution effect | registry unavailable/hash metric | Disable analysis persistence |
| Analyzer unavailable | Qualification fails closed; no webhook effect | analysis failure code | Disable analysis persistence |
| Tables absent during rolling deploy | Flags default false; guarded writers treat undefined-table as disabled/failure | deployment alert | Deploy migration or keep flags off |
| Old worker sees new rows | It ignores new tables and executes existing job | none | Supported |
| New worker sees old signal/job | Missing decision means no canonical outcome write; execution continues | missing-decision counter | Supported |
| Migration partially applied | Flags remain false; migration transaction/inspection determines repair | deploy alert | Complete or downgrade migration |

Do not use canonical evidence to retry, suppress, reroute, size, select a
contract, or compensate an order. No canonical failure creates a second
execution side effect.

## 17. Feature-flag rollout

| Flag | Default/dependency | Rollout | Failure behavior | Observability/rollback |
|---|---|---|---|---|
| `PINE_SEMANTIC_ANALYSIS_PERSISTENCE` | false; R1B-0 complete + table present | R1B-1 internal/admin validation canary | Qualification persistence fails closed; webhook unaffected | created/reused/failure/hash metrics; turn off |
| `CANONICAL_SIGNAL_PERSISTENCE` | false; R1A shadow remains green + table present | R1B-2 paper-only canary, then all modes as evidence only | Existing webhook/job continues | success/failure/missing/mismatch metrics; turn off |
| `CANONICAL_SIGNAL_OUTCOME_PERSISTENCE` | false; intent persistence on | R1B-3 paper-only canary before any real-order observation | Existing worker/router continues | outcome success/failure/unmapped metrics; turn off |
| `SIGNAL_REJECTION_PERSISTENCE` | false; rejection table present | R1B-3 post-auth safe-code canary | Public rejection unchanged | count/write-failure metrics; turn off |

All flags are server environment settings with the same conservative invalid
value handling as `CANONICAL_SIGNAL_SHADOW`. No webhook field, metadata, user,
strategy, or frontend control can set them.

Deployment order is R1B-0, migration/schema with flags false, compatible app,
analysis flag, intent flag, outcome flag, rejection flag. Rollback reverses
flags before code or migration.

## 18. Observability

Counters:

```text
canonical_intent_write_success
canonical_intent_write_failure{error_class}
canonical_outcome_write_success{phase,routing_result}
canonical_outcome_write_failure{phase,error_class}
canonical_shadow_mismatch{wire_action}
signal_rejection_count{stage,rejection_code}
signal_rejection_write_failure{error_class}
semantic_analysis_created{level,confidence}
semantic_analysis_reused{level,confidence}
semantic_analysis_failure{error_class}
registry_hash_mismatch{registry_version}
canonical_decision_missing_for_job
canonical_outcome_unmapped_result
```

Labels are closed, low-cardinality values. Never label with credential,
signal/user/instance ID, email, source, hash, broker/order ID, or free-form
text.

Initial rollout thresholds:

- any canonical shadow mismatch for the four frozen actions stops rollout;
- intent/outcome write failures above 0.5% over 15 minutes disable that writer;
- any cross-tenant/FK ownership violation pages immediately;
- any registry hash mismatch disables semantic persistence;
- analysis failures above 1% over 15 minutes stop qualification rollout;
- missing decisions are monitored but never block jobs;
- rejection volume alerts on baseline-relative rate, not user identity.

Logs contain event code, phase, version, and opaque existing correlation ID
only where already safe. They do not contain record payloads.

## 19. Test plan

### 19.1 Migration

- upgrade/downgrade/upgrade on PostgreSQL and SQLite;
- existing database with historical signals/jobs/webhook/source artifacts;
- nullable optional-field compatibility;
- all unique/check/FK/cascade behaviors;
- revision length and single head;
- old application against 0015 and new flags-off application against 0014;
- no position model/read-path change.

### 19.2 Canonical decision

- exact BUY_CE/BULLISH, BUY_PE/BEARISH, EXIT/FLAT, HOLD/connectivity rows;
- all compatibility fields equal old mapping;
- one decision per signal;
- duplicate delivery reuses decision;
- decision failure leaves current signal/job/response unchanged;
- metadata allowlist/2 KiB bound;
- credential/comment/raw ID absent;
- credential-shaped signal ID remains legacy-only and is redacted in new
  projection;
- composite owner FK prevents cross-tenant record.

### 19.3 Outcomes

- flat entry, same-side no-op, opposite reversal, exit while flat, explicit
  exit;
- paused-entry no-op/block;
- failed entry/exit, partial fill, pending order, retry;
- state captured only after actual JSON read;
- route result captured only after actual return;
- idempotent outcome insert;
- crash-window tests prove absent evidence is not invented;
- outcome failure does not affect router/broker/job result.

### 19.4 Rejections

- post-auth lifecycle/time/action/replay rejection stored;
- invalid/unknown/revoked credential creates no user-owned rejection;
- pre-auth malformed schema/body creates no rejection;
- raw credential/body/comment/source absent;
- secret-shaped signal ID null/redacted with hash only;
- duplicate rejection deduped;
- bounded safe detail and closed codes;
- public HTTP status/body and anti-enumeration unchanged;
- retention deletion is owner-safe.

### 19.5 Semantic analyses and fixtures

- exact provenance reuse and new row for changed analyzer/registry/hash/schema;
- source hash recomputation mismatch fails;
- registry hash mismatch fails closed;
- historical analysis has no update path;
- reanalysis creates new row/supersedes link;
- payload closed, canonical, <=64 KiB, no source;
- all expected fixtures exact;
- dedicated five provenance fixtures;
- real `PARTIAL_MATCH` cases and minimum severity;
- L4/indeterminate persistence;
- source/account deletion cascade.

### 19.6 Regression gates

- existing 820 backend tests;
- private webhook/execution suites;
- Pine conversion/validation/transport/source-binding suites;
- migration metadata and PostgreSQL cycle;
- TypeScript, Vitest, frontend build;
- no Dhan/network dependency for new tests;
- no AI/TradingView compile claim.

### 19.7 Design-task validation

This documentation-only branch ran:

```text
python -m pytest app/tests/test_migration_metadata.py -q
  2 passed

python -m pytest app/tests/test_canonical_signal_adapter.py
                 app/tests/test_pine_capability_registry.py
                 app/tests/test_pine_semantic_preanalyzer.py -q
  44 passed

python -m alembic heads
  0014_verify_select (head)
```

No full runtime regression was necessary because this task changes only the
design document; the approved R1A review already recorded the full 820-test
confirmation.

## 20. Implementation slices

### R1B-0 — prerequisite hardening

- R-01 pure trading constant/re-export.
- R-02 secret-taint/redaction utility and policy tests.
- R-03 implement/test `PARTIAL_MATCH`.
- R-04 add/correct five fixture pairs and registry citations.
- R-05 convert all fixture expectations to exhaustive exact evidence.
- Re-run full regression suites.
- **No migration and no persistence.**

### R1B-1 — schema and semantic provenance

- Create the single 0015 migration with all four empty tables, flags false.
- Add ORM models and check constraints.
- Add immutable analysis serializer/writer and provenance tests.
- Enable only semantic-analysis persistence in internal/admin canary.
- No canonical intent/outcome/rejection writes yet.

### R1B-2 — canonical intent evidence

- Add separate-transaction decision writer.
- Persist trading intent and HOLD connectivity intent.
- Keep old mapping/job/HOLD path authoritative.
- Add optional offline backfill command only after live-write review.
- No state/routing outcome fields yet.

### R1B-3 — outcomes and rejections

- Append actual worker state and final router result evidence.
- Add post-auth pre-acceptance rejection writer.
- Extend admin/internal queries only.
- Keep public webhook, job, execution router, broker, and normal-user UI
  unchanged.

This is safer than one large R1B change because R1B-0 stabilizes evidence,
R1B-1 proves migration/provenance, R1B-2 proves non-authoritative dual-write,
and R1B-3 instruments runtime truth only after the earlier layers are stable.

## 21. Rollback

Application rollback:

1. Set outcome/rejection flags false.
2. Set intent and semantic persistence flags false.
3. Verify old webhook/job/analysis flows.
4. Deploy the last compatible app.

Data/schema rollback:

1. Export only if governance requires retaining evidence.
2. Run 0015 downgrade in the order specified in section 14.
3. Confirm Alembic head `0014_verify_select`.
4. Run private webhook/HOLD/replay and Pine validation regressions.

Because no `strategy_signals`, job, webhook, source artifact, position, prompt,
transport, or broker column is changed, rollback loses only optional R1B
evidence. It cannot remove or recreate an order.

## 22. Explicit out-of-scope items

- R1B implementation in this design task.
- Any migration file or ORM/runtime/test/registry/fixture change.
- Public webhook fields or actions.
- Canonical execution authority.
- PostgreSQL position authority or JSON fallback.
- Execution-router, risk, PaperBroker, Dhan, contract, lots, or credential
  behavior.
- Pending/limit/stop/cancel/replace, partial-exit intent, pyramiding,
  multi-position, or multi-leg support.
- Prompt V4, Transport V3, or prompt/transport edits.
- Normal-user confidence/feature-vector/rejection-detail UI.
- Materialized ledger or analytics warehouse.
- AI conversion or live-order enablement.
- Merge or deployment.

## 23. Final R1B implementation recommendation

**Proceed only with `R1B-0` prerequisite hardening.**

After R1B-0 passes independent review, implement the single additive
`0015_r1b_persistence` migration and R1B-1 semantic provenance with all flags
false by default. Review each later writer before enabling it.

The recommended persistence option is dedicated tables, with no new
`strategy_signals` columns. Intent is immutable and one-to-one; outcomes are
append-only and chronological; rejections are post-auth and privacy-bounded;
semantic analysis is uniquely bound to source, analyzer, registry, and schema.
Direct owner-scoped service joins are sufficient for R1C.

Remaining implementation risks are:

1. accidentally coupling a best-effort evidence transaction to existing job
   persistence;
2. persisting secret-shaped legacy identifiers or arbitrary metadata;
3. mapping router results incompletely or too early;
4. treating PostgreSQL position shadow data as current-state authority;
5. persisting analyzer evidence before R1B-0 fixture/confidence corrections;
6. enabling flags before schema and mixed-version compatibility checks.

These risks are controlled by the staged rollout, separate transactions,
closed codes, composite owner constraints, exact provenance, default-false
flags, and explicit rollback order.

Safety confirmation:

- No runtime code changed.
- No test logic changed.
- No registry or fixture changed.
- No prompt changed.
- No transport changed.
- No migration was created.
- No webhook or execution behavior changed.
- No AI conversion was enabled.
- No live order occurred.
- No merge or deployment occurred.
