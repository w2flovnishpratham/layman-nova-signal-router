# NOVA Phase R1A Independent Equivalence and Safety Review

## 1. Executive verdict

**Overall verdict: `APPROVED_WITH_NON_BLOCKING_FINDINGS`**

Phase R1A preserves the existing private-webhook execution contract. The
canonical adapter is invoked as a fail-isolated shadow after authentication,
lifecycle validation, signal-time validation, and replay claim. Its result is
not used by either HOLD persistence or execution-job persistence. The legacy
`build_normalized_signal()` function remains authoritative.

The review proved field equivalence for every field of `NormalizedSignal` for
`BUY_CE`, `BUY_PE`, and `EXIT`; proved that HOLD creates no normalized signal,
job, or broker call; and proved that replay identity is independent of the
canonical layer and its feature flag.

There are no blocking execution, security, registry/analyzer, or scope-creep
findings. The non-blocking findings concern adapter import purity, dormant
`PARTIAL_MATCH` semantics, sparse expected-fixture assertions, five misleading
registry fixture citations, and pre-existing timing-sensitive tests on this
Windows/OneDrive review host. They should be addressed before R1B makes
analyzer output or fixture provenance persistent/authoritative.

This approval permits **R1B design only**. It is not authorization to merge,
deploy, enable execution, or place orders.

## 2. Reviewed commit and scope

- Baseline before R1A: `495662fbf5a995184502511316b3d9d40c69abc9`
- Reviewed implementation branch: `phase-r1a-canonical-signal-foundation`
- Starting and reviewed implementation SHA:
  `f2b8ef0e45140dd153b685f97569053e0b5d400b`
- Review branch: `phase-r1a-equivalence-review`
- Starting worktree: clean
- Inputs reviewed:
  - `docs/pine/NOVA_PHASE_R0_CODEBASE_VERIFICATION.md`
  - `docs/pine/NOVA_PHASE_R1A_CANONICAL_SIGNAL_FOUNDATION.md`
  - all 70 files in the baseline-to-R1A diff
- Diff size: 70 files, 2,453 insertions, 1 deletion
- Review method: static diff inspection, baseline comparison, independent
  probes, targeted regression suites, full regression suites, and build/type
  checks

No runtime code, test logic, registry, prompt, transport, migration, execution
router, PaperBroker, or Dhan code was modified by this review.

## 3. Changed-file classification

All 70 changed files were classified; there are no `UNEXPECTED` files.

| Classification | Count | Files |
|---|---:|---|
| `DOMAIN_MODEL` | 2 | `backend/app/domain/canonical_signal.py`; `backend/app/domain/legacy_signal_adapter.py` |
| `SHADOW_INTEGRATION` | 1 | `backend/app/services/private_webhook_service.py` |
| `REGISTRY` | 3 | `backend/app/domain/pine_capabilities.py`; `backend/app/pine_capabilities/registry.schema.json`; `backend/app/pine_capabilities/registry.v1.json` |
| `PRE_ANALYZER` | 1 | `backend/app/services/pine_semantic_preanalyzer.py` |
| `FIXTURE` | 56 | Both `.pine` and `.expected.json` for each of F01, F02, F03, F05, F06, F07, F08, F11, F12, F13, F19, F30, F31, F33, F36, F37, F38, F40, F42, F44, F45, F46, F49, F50, F59, F60, F61, and F62 under `backend/app/tests/pine_fixtures/r1a/` |
| `TEST` | 4 | `backend/app/tests/test_canonical_signal_adapter.py`; `backend/app/tests/test_canonical_signal_shadow.py`; `backend/app/tests/test_pine_capability_registry.py`; `backend/app/tests/test_pine_semantic_preanalyzer.py` |
| `CONFIGURATION` | 2 | `backend/app/config.py`; `backend/requirements.txt` |
| `DOCUMENTATION` | 1 | `docs/pine/NOVA_PHASE_R1A_CANONICAL_SIGNAL_FOUNDATION.md` |
| `UNEXPECTED` | 0 | None |

The only changed private-webhook runtime file adds a feature-gated,
non-authoritative comparison. Production persistence still calls the old
mapping.

## 4. Public contract equivalence

Direct comparison against
`495662fbf5a995184502511316b3d9d40c69abc9` produced:

| Contract property | Evidence | Result |
|---|---|---|
| Accepted actions remain exactly `BUY_CE`, `BUY_PE`, `EXIT`, `HOLD` | `backend/app/services/private_webhook_service.py`, `NORMALIZED_ACTIONS`, lines 39-39 | `PASS` |
| No aliases added | `normalize_action()`, lines 87-89, still only trim/uppercase plus exact set membership | `PASS` |
| Request schema unchanged | `PrivateWebhookPayload`, lines 51-66 | `PASS` |
| Extra fields forbidden | `PrivateWebhookPayload.model_config`, line 57 | `PASS` |
| Credential is ingress-only | `PrivateWebhookRequest` and `parse_request()`, lines 69-72 and 105-118 | `PASS` |
| Credential is not serialized or logged | `SecretStr(exclude=True)` plus full-diff logging inspection | `PASS` |
| Unknown action behavior unchanged | `ingest()`, lines 391-396: audit plus 422 `INVALID_ACTION` | `PASS` |
| HTTP statuses unchanged | `backend/app/routers/private_webhook.py`, lines 29-33 and 53-84 | `PASS` |
| Error response shape unchanged | `_error()`, lines 29-33: `{ok,error,reason}` | `PASS` |
| Timestamp validation unchanged | `validate_signal_time()`, lines 121-136 | `PASS` |
| Rate limiting unchanged | router `_credential_rate_limited()`, lines 36-50 | `PASS` |
| Body-size and content-type validation unchanged | router lines 58-64 | `PASS` |

The router itself is unchanged in the R1A diff.

## 5. Adapter truth table

`backend/app/domain/legacy_signal_adapter.py`, `_LEGACY_ACTIONS`, lines 20-41:

| Input | Event type | Desired state | Intent reason | Result |
|---|---|---|---|---|
| `BUY_CE` | `STRATEGY_SIGNAL` | `BULLISH` | `DIRECTIONAL_SIGNAL` | Exact |
| `BUY_PE` | `STRATEGY_SIGNAL` | `BEARISH` | `DIRECTIONAL_SIGNAL` | Exact |
| `EXIT` | `STRATEGY_SIGNAL` | `FLAT` | `EXPLICIT_EXIT` | Exact |
| `HOLD` | `CONNECTIVITY_TEST` | `NONE` | `CONNECTIVITY_TEST` | Exact |

Additional adapter properties:

- `adapt_legacy_action()` indexes the closed map directly and raises
  `ValueError` for unknown values; it does not normalize case or whitespace
  (lines 44-67).
- It reads no position and makes no entry-versus-reversal decision.
- It accesses no DB, broker, FastAPI, filesystem, job queue, or execution
  service.
- `CanonicalSignalEvent` is a frozen, slotted dataclass; metadata is deep
  copied and recursively frozen (`backend/app/domain/canonical_signal.py`,
  lines 71-113).
- Forbidden metadata keys include execution, ownership, broker, quantity,
  contract, risk, and credential fields (lines 41-68). Nested mappings are
  checked too.
- User metadata cannot override canonical event fields.
- The service passes only `reference_price` and `comment` into metadata, not
  credential content (`private_webhook_service.py`, lines 333-343).
- The canonical object contains no selected quantity, broker mode, strike,
  expiry, risk, or order settings.
- One purity caveat remains: importing the adapter imports
  `DEFAULT_EXCHANGE_SEGMENT` from `app.config`, whose module constructs
  `Settings()` and can read environment/`.env`. This is non-authoritative,
  lazily imported inside the caught shadow block, and cannot affect webhook
  acceptance. See finding NB-01.
- The actual ingress credential cannot reach the canonical object. The domain
  type itself does not reject credential-shaped content in every top-level
  text field, such as `signal_id`; this is an existing user-selected wire field
  and is never included in the new shadow logs. See finding NB-02.

**Adapter truth-table result: mapping `PASS`; purity `PASS WITH NON-BLOCKING
BOUNDARY FINDINGS`.**

## 6. Compatibility field comparison

Independent probes constructed identical fixed inputs for the old
`build_normalized_signal()` and the new
`canonical_to_normalized_signal(adapt_legacy_action(...))`. Pydantic JSON dumps
were equal for all three trading actions.

Action mapping:

| Wire action | Old authoritative output | Compatibility output | Result |
|---|---|---|---|
| `BUY_CE` | `ENTRY / BUY / CE` | `ENTRY / BUY / CE` | Equal |
| `BUY_PE` | `ENTRY / BUY / PE` | `ENTRY / BUY / PE` | Equal |
| `EXIT` | `EXIT / SELL / None` | `EXIT / SELL / None` | Equal |

All 19 `NormalizedSignal` fields:

| Field | Old path | Compatibility path | Result |
|---|---|---|---|
| `payload_format` | `NOVA` | `NOVA` | Equal |
| `secret` | empty | empty | Equal |
| `signal_id` | `PWH-{instance_id}-{signal_id}` | same | Equal |
| `strategy_code` | `instance:{instance_id}` | same | Equal |
| `action` | map-dependent | same | Equal |
| `side` | map-dependent | same | Equal |
| `symbol` | `NIFTY` | `NIFTY` | Equal |
| `instrument_type` | `OPTIDX` | `OPTIDX` | Equal |
| `exchange_segment` | `DEFAULT_EXCHANGE_SEGMENT` | same constant | Equal |
| `option_side` | `CE`, `PE`, or `None` | same | Equal |
| `security_id` | `None` | `None` | Equal |
| `strike` | `None` | `None` | Equal |
| `expiry` | `None` | `None` | Equal |
| `qty` | `1` placeholder | `1` placeholder | Equal |
| `order_type` | `MARKET` | `MARKET` | Equal |
| `product_type` | `INTRADAY` | `INTRADAY` | Equal |
| `source` | `private_tradingview_webhook` | same | Equal |
| `raw_payload` | nine identical keys/values | same | Equal |
| model defaults/serialization | Pydantic model defaults | same model/defaults | Equal |

The `raw_payload` comparison includes `private_instance_webhook`,
`strategy_instance_id`, normalized action, signal ID, UTC signal time,
received time, strategy version, timeframe, reference price, and comment.
Sorted serialized JSON was identical.

**Compatibility verdict: `BYTE_OR_FIELD_EQUIVALENT`.**

## 7. Shadow call placement

Exact call sequence:

1. Router validates feature enablement, content type, body size, and JSON shape
   (`backend/app/routers/private_webhook.py`, lines 53-71).
2. Router parses request and separates credential from payload (lines 73-75).
3. Router rate-limits the credential hash (lines 75-76).
4. Router authenticates credential and resolves its owner/instance (line 77).
5. Router validates instance lifecycle (line 78).
6. Router calls `ingest()` (line 79).
7. `ingest()` normalizes action and rejects unknown values
   (`backend/app/services/private_webhook_service.py`, lines 388-396).
8. `ingest()` validates signal time (line 397).
9. It creates the unchanged payload fingerprint and replay claim
   (lines 399-418).
10. It resolves tampered, duplicate, and invalid replay outcomes
    (lines 424-442).
11. Only then it invokes `_shadow_compare_canonical()` (line 444).
12. Its return value is ignored. HOLD calls `_persist_hold()`; other actions
    call `_persist_execution_job()` (lines 445-448).
13. `_persist_execution_job()` independently calls the old authoritative
    `build_normalized_signal()` (lines 545-573).

Therefore the adapter cannot affect authentication, credential ownership,
replay identity, payload fingerprinting, lifecycle, mode, lots, contract
resolution, or execution. It has no execution call. Mismatch and exception
paths return `False`, which the caller discards. The old path remains
authoritative.

**Shadow-placement result: `PASS — NON_AUTHORITATIVE`.**

## 8. Feature-flag behavior

`backend/app/config.py`, `Settings.CANONICAL_SIGNAL_SHADOW`, line 122, defaults
to `False`. Its before-validator is at lines 146-159.

Independent configuration probes produced:

| Input | Result |
|---|---|
| missing | `False` |
| Python `True`, `"1"`, `"true"`, `"TRUE"`, `" yes "`, `"on"` | `True` |
| Python `False`, `"0"`, `"false"`, `"FALSE"`, `" no "`, `"off"`, `""` | `False` |
| invalid string, `"2"` | `False` |

Flag-off returns before importing canonical modules
(`private_webhook_service.py`, lines 312-320). Flag-on comparison is entirely
inside a catch-all isolation block; diagnostic logging has a separate
catch-all (lines 322-381). Shadow tests prove identical responses, signal-row
counts, job counts, job payloads, and replay hashes for flag off/on.

The flag is server configuration only. The strict webhook schema forbids a
payload or metadata flag override.

**Feature-flag result: `PASS`.**

## 9. HOLD invariant

Static proof:

```text
HOLD
  -> adapt_legacy_action(): CONNECTIVITY_TEST / NONE / CONNECTIVITY_TEST
  -> canonical_to_normalized_signal(): None
  -> ignored shadow return
  -> _persist_hold()
  -> StrategySignal only
  -> no NormalizedSignal
  -> no StrategyExecutionJob
  -> no broker call
```

Evidence:

- Adapter mapping: `legacy_signal_adapter.py`, lines 36-40 and 76-77.
- Shadow-specific HOLD assertion: `private_webhook_service.py`, lines 349-354.
- Authoritative HOLD branch: lines 444-448.
- HOLD persistence creates only `models.StrategySignal`:
  `_persist_hold()`, lines 505-542.
- Execution persistence is a different function starting at line 545.
- Existing execution tests prove HOLD never reaches worker/broker.
- Setup-flow tests prove HOLD remains valid connectivity/setup evidence.
- Duplicate and conflicting-duplicate behavior remains in the shared,
  pre-shadow replay path.
- A forced registry-load failure did not alter HOLD response or create a job.

The shadow maps HOLD to `NONE`, not `FLAT`, and compatibility returns `None`.

**HOLD verdict: `HOLD_EXECUTION_ISOLATED`.**

## 10. Replay and idempotency result

Replay inputs remain:

- provider: `instance-webhook:{instance_id}`
- event ID: `payload.signal_id`
- raw-body input: the existing payload fingerprint
- fingerprint fields: normalized action, signal ID, UTC signal time,
  strategy version, timeframe, reference price, comment
- JSON canonicalization: sorted keys and compact separators

Evidence: `private_webhook_service.py`, `payload_fingerprint()`, lines 139-155,
and replay claim at lines 399-418. The shadow call follows the replay decision
at line 444. No canonical field appears in the fingerprint.

Verified outcomes:

| Scenario | Result |
|---|---|
| same signal ID + same payload | duplicate response; no additional job |
| same signal ID + different action | 409 `CONFLICTING_DUPLICATE` |
| same signal ID + different metadata | fingerprint differs; conflicting duplicate |
| same signal ID + case-normalized action | same fingerprint |
| concurrent identical requests | one durable job; duplicate path for loser |
| shadow disabled versus enabled | same claim identity, response, and persisted payload |

The strategy-signal uniqueness constraint remains the durable post-claim guard
for a crash window or concurrent race (lines 432-438 and 582 onward).

**Replay result: `PASS — EQUIVALENT`.**

## 11. Failure isolation

Independent monkeypatch/probe outcomes:

| Forced condition | Observed behavior | Webhook impact | Result |
|---|---|---|---|
| adapter raises | shadow returns `False` | old mapping retained | Pass |
| compatibility conversion raises | shadow returns `False` | old mapping retained | Pass |
| comparison mismatch | safe diagnostic, returns `False` | old mapping retained | Pass |
| structured logger raises | logging exception swallowed | request unaffected | Pass |
| registry load raises | analyzer unavailable; HOLD ingestion still 202/no job | none | Pass |
| registry schema invalid | analyzer returns L4/`ANALYSIS_INDETERMINATE` with `BLK_REGISTRY_UNAVAILABLE` | none | Pass |
| pre-analyzer raises | webhook shadow comparison still succeeds independently | none | Pass |
| registry content/hash changes | changed hash is reported deterministically | none | Pass |

The registry and analyzer are not imported by the private-webhook router,
service startup path, adapter, or persistence path. Shadow failure never
replaces the old mapping (`private_webhook_service.py`, lines 329-381).

**Failure-isolation result: `PASS`.**

## 12. Logging and secret safety

The full R1A diff was searched for:

```text
credential
token
totp
secret
payload
raw_body
request.body
logger
print(
```

The only new runtime diagnostics are:

- mismatch: action, canonical event type, desired state, contract version
- shadow exception: action only

Evidence: `private_webhook_service.py`, `_shadow_compare_canonical()`, lines
322-381.

No new log contains credential plaintext, request body, authorization header,
broker token, TOTP, webhook URL, Pine source, metadata, owner email, or secret
hash. There are no new `print()` calls. Other search matches are domain
forbidden-key definitions, schema/test names, fixtures, or documentation.

The actual credential is extracted as `SecretStr`, excluded from serialization,
and never passed to the adapter (`private_webhook_service.py`, lines 69-72 and
105-118).

**Secret-safety result: `PASS`.**

## 13. Registry verification

Files reviewed:

- `backend/app/pine_capabilities/registry.v1.json`
- `backend/app/pine_capabilities/registry.schema.json`
- `backend/app/domain/pine_capabilities.py`

Results:

- Closed registry ID: `nova.pine-capabilities`
- Closed version: `1`
- Entry count: 38; all capability IDs unique
- Canonical SHA-256:
  `c1a255f3d7a2bce84a8c95f38dac52d2fcf35ee1474e3ecf28289b0708dfe00f`
- Independently reordering entries does not change the canonical hash.
- Unknown levels and temporal classes fail schema/load validation.
- Priority resolution is deterministic and most restrictive wins.
- Every L4 entry has a blocker code.
- The JSON schema is closed; no field can declare or enable runtime execution.
- Registry load is absent from the webhook import path.
- Load/schema failures become conservative analyzer output and do not affect
  webhook startup or ingestion.
- Deprecated-version data is represented deterministically as a string or
  null; there is no implicit active-version filter.
- Every cited fixture ID resolves to an existing fixture pair. Five citations
  do not actually demonstrate the named construct and are recorded as NB-04.

Level distribution:

| Level | Count |
|---|---:|
| L0 | 5 |
| L1 | 5 |
| L2 | 7 |
| L3 | 11 |
| L4 | 10 |

Temporal-class distribution:

| Class | Count |
|---|---:|
| T0 | 16 |
| T1 | 1 |
| T2 | 1 |
| T3 | 1 |
| T4 | 1 |
| T5 | 1 |
| T6 | 8 |
| T7 | 1 |
| T8 | 2 |
| T9 | 6 |

**Registry result: hash and closed-policy checks `PASS`; fixture provenance
`PASS WITH NON-BLOCKING FINDING`.**

## 14. Pre-analyzer verification

`backend/app/services/pine_semantic_preanalyzer.py` is deterministic lexical
analysis. Its imports are standard-library parsing/hash constructs plus the
local registry loader. It has no AI, network, TradingView, Dhan, broker, order,
or execution dependency and does not execute Pine.

Verified:

- output includes source SHA and registry ID/version/hash;
- result objects are frozen;
- most restrictive matched capability wins;
- comments and ordinary strings are masked before executable API detection;
- hostile/source-content security analysis remains separate;
- confirmed offset HTF is not promoted to T4;
- unsafe future lookahead is T4;
- `varip` and essential intrabar execution are L4;
- dynamic requests and synthetic charts are L4/indeterminate;
- malformed source F62 is L4/indeterminate and is not represented as compiled;
- ambiguous input is never promoted to safe.

F46, F50, and F62 intentionally combine an effective L4 result with
`ANALYSIS_INDETERMINATE`: the analyzer has enough evidence to impose a
conservative blocking level while explicitly declining a full semantic or
compilation claim.

`AnalysisConfidence.PARTIAL_MATCH` exists at lines 24-28 but no current branch
emits it; results are currently high-confidence matches or indeterminate. This
does not create a false-safe result, but the three-state persistence semantics
must be defined before R1B stores or exposes confidence. See NB-03.

**Pre-analyzer result: conservative safety `PASS`; complete three-state
confidence behavior `NOT_PROVEN`/non-blocking for R1B design.**

## 15. Fixture execution evidence

Reported classifications were reproduced:

- L0: F01, F02, F03, F05
- L1: F06, F07, F08, F42, F59
- L2: F19, F45
- L3: F11, F12, F13, F30, F31, F33, F36, F37
- L4: F38, F40, F44, F46, F49, F50, F60, F61, F62
- Indeterminate: F46, F50, F62

For all 28 fixture IDs:

- the `.pine` source exists;
- the `.expected.json` file exists;
- the test glob loads the source and expected document;
- the test runs `analyze_source()` rather than checking existence only;
- the source SHA is independently reproducible;
- no fixture is present but unexecuted.

The expected manifests assert matched capability IDs, effective level, and
confidence for all 28. However, they provide temporal-class expectations for
only 3/28, blocker codes for 17/28, disclosure codes for 2/28, and stored
source SHA for 0/28. Assertions use expected subsets rather than exact complete
sets. Thus full exact temporal/blocker/disclosure evidence is not encoded for
every fixture even though the analyzer results and targeted tests passed. See
NB-05.

**Fixture execution result: `PASS WITH NON-BLOCKING EVIDENCE GAPS`.**

## 16. Scope-creep result

The baseline-to-R1A diff contains no modification to:

- `execution_router.py`
- `risk_manager.py`
- `paper_broker.py`
- Dhan adapter
- position authority or reconciliation
- contract resolution
- lots or quantity calculation
- strategy verification rules
- engine selection
- private credential generation
- database models
- Alembic migrations
- Prompt V2, V3, or V3.1
- Transport V1 or V2
- frontend runtime behavior

The private-webhook router is unchanged. No production job/persistence branch
was replaced; the old `build_normalized_signal()` remains authoritative. The
configuration diff adds only the default-false shadow flag and conservative
boolean parsing. `backend/requirements.txt` adds the registry schema validator
dependency.

**Scope-creep result: `PASS`; `UNEXPECTED` file count is zero.**

## 17. Test results

Commands were run from the requested working directories using the same
review worktree:

| Gate | Command/result |
|---|---|
| R1A focused backend | `python -m pytest app/tests/test_canonical_signal_adapter.py app/tests/test_canonical_signal_shadow.py app/tests/test_pine_capability_registry.py app/tests/test_pine_semantic_preanalyzer.py -q` — 61 passed |
| Webhook/execution | `python -m pytest app/tests/test_private_webhook.py app/tests/test_private_webhook_execution.py -q` — 81 passed |
| Pine regressions | `python -m pytest app/tests/test_personal_pine.py app/tests/test_pine_conversion.py app/tests/test_pine_master_prompt_v3.py app/tests/test_pine_transport_v2.py app/tests/test_pine_v31_source_binding.py app/tests/test_tradingview_setup.py -q` — 64 passed |
| Full backend, first run | `python -m pytest app/tests -q` — 819 passed, 1 timing assertion failed |
| Timing-test isolation | `test_position_shadow.py::test_executor_saturation_skips_immediately` — passed 5/5 reruns; functional assertions had passed in the full run |
| Full backend, confirmation run | `python -m pytest app/tests -q` — 820 passed, 1 third-party warning in 761.28 s |
| Alembic | `python -m alembic heads` — `0014_verify_select (head)` |
| TypeScript | `node node_modules/typescript/bin/tsc -b --noEmit` — pass |
| Vitest, final exact run | `node node_modules/vitest/vitest.mjs run` — 3 files, 20 tests passed |
| Frontend build | `node node_modules/vite/bin/vite.js build` — pass |
| Diff check | final result recorded before commit |

The initial full backend miss was the unchanged
`test_executor_saturation_skips_immediately` wall-clock threshold (968 ms
observed versus `<500` ms); its substantive authority/skip/attempt assertions
passed, and the same test passed five consecutive isolated runs.

The first two Vitest attempts encountered fixed five-second timeouts while
Windows/OneDrive filesystem operations were abnormally slow. Individual
unchanged modules were then isolated, the production build passed, and the
final exact Vitest command passed all 20 tests in 26.22 seconds. No frontend
file is part of the R1A diff.

Third-party warnings observed during tests and the Vite Node-version/chunk-size
warnings are not R1A regressions.

## 18. Blocking findings

None.

There is no finding in the classes execution equivalence, security,
registry/analyzer safety, or scope creep that blocks R1B design.

## 19. Non-blocking findings

### NB-01 — adapter import has an indirect environment dependency

- **File path:** `backend/app/domain/legacy_signal_adapter.py`;
  `backend/app/config.py`
- **Class/function:** module imports; `Settings`
- **Line range:** adapter line 7; config lines 16 and 338
- **Observed behavior:** the adapter imports a constant from `app.config`, and
  importing that module constructs `Settings()`, which may read environment or
  `.env`.
- **Risk:** standalone adapter import is not perfectly pure and can fail due to
  configuration state. In the webhook it is lazily imported inside a
  fail-isolated shadow block, so it cannot reject or alter an existing request.
- **Verdict:** non-blocking for R1B design; move the constant to an
  environment-free domain/constants module before making the adapter
  authoritative.

### NB-02 — credential-shaped content is rejected in metadata, not every top-level text field

- **File path:** `backend/app/domain/canonical_signal.py`;
  `backend/app/services/private_webhook_service.py`
- **Class/function:** `_freeze_metadata()`; `CanonicalSignalEvent`;
  `_shadow_compare_canonical()`
- **Line range:** canonical lines 41-113; service lines 329-381
- **Observed behavior:** credential keys and lowercase `nwk_` metadata values
  are rejected recursively, and the ingress credential is never passed to the
  adapter. A caller could independently copy credential-shaped text into a
  permitted wire field such as `signal_id`, which is also a canonical field.
- **Risk:** no new credential disclosure occurs: `signal_id` was already
  user-controlled and persisted by the legacy path, and new shadow logs omit
  it. The domain type alone cannot prove that no arbitrary top-level text
  equals a secret.
- **Verdict:** non-blocking/no R1A regression; define explicit secret-taint
  expectations before accepting canonical events from additional sources.

### NB-03 — `PARTIAL_MATCH` is declared but not emitted

- **File path:** `backend/app/services/pine_semantic_preanalyzer.py`
- **Class/function:** `AnalysisConfidence`; `analyze_source()`
- **Line range:** lines 24-28 and 368-376
- **Observed behavior:** the enum declares `PARTIAL_MATCH`, but current analysis
  branches return only `HIGH_CONFIDENCE_MATCH` or
  `ANALYSIS_INDETERMINATE`.
- **Risk:** there is no false-safe classification, but persisting the enum in
  R1B without semantics would create an ambiguous public/internal contract.
- **Verdict:** non-blocking for R1B design; specify and test the third state
  before persistence or API exposure.

### NB-04 — five registry fixture citations do not demonstrate their named capability

- **File path:** `backend/app/pine_capabilities/registry.v1.json`;
  corresponding files under `backend/app/tests/pine_fixtures/r1a/`
- **Class/function:** entries `PROCESS_ORDERS_ON_CLOSE`,
  `STRATEGY_ORDER_SEMANTICS`, `PINE_CONTROLLED_ENTRY_QUANTITY`,
  `POSITION_STATE_REFERENCE`, `PENDING_ORDER_CANCELLATION`
- **Line range:** registry lines 532-631
- **Observed behavior:** cited fixtures F05, F13, F31, F36, and F13 exist, but
  respectively do not contain `process_orders_on_close`, `strategy.order`,
  entry quantity, `strategy.position_size`, or cancellation. Those constructs
  are covered by inline parameterized analyzer tests at
  `backend/app/tests/test_pine_semantic_preanalyzer.py`, lines 69-90.
- **Risk:** analyzer coverage exists and webhook execution is isolated, but
  registry provenance could mislead future R1B evidence/report consumers.
- **Verdict:** non-blocking for R1B design; correct the citations or add
  dedicated fixture pairs before persisted evidence depends on them.

### NB-05 — expected fixture manifests do not encode every asserted evidence dimension

- **File path:** `backend/app/tests/test_pine_semantic_preanalyzer.py`;
  `backend/app/tests/pine_fixtures/r1a/*.expected.json`
- **Class/function:** fixture parameterization and expected-result assertions
- **Line range:** test lines 12-27
- **Observed behavior:** all fixtures execute, but expected manifests use
  subset assertions and only some encode temporal classes, blocker codes, or
  disclosure codes; none stores the source SHA.
- **Risk:** a future unintended extra/missing diagnostic could escape the
  fixture matrix even though current focused tests and independent inspection
  establish conservative classifications.
- **Verdict:** non-blocking for R1B design; make expected evidence exhaustive
  before it becomes persisted or externally reported.

### NB-06 — unchanged timing tests are sensitive to this review host

- **File path:** `backend/app/tests/test_position_shadow.py`;
  `frontend/src/strategies/*.test.tsx`
- **Class/function:** `test_executor_saturation_skips_immediately`; frontend
  async UI tests
- **Line range:** backend lines 638-668; frontend
  `PersonalStrategiesPage.test.tsx` lines 102-141
- **Observed behavior:** one full backend run exceeded a 500 ms wall-clock
  assertion, then the exact test passed five consecutive times. Early Vitest
  runs exceeded fixed five-second limits during abnormal filesystem latency;
  the final exact suite passed all 20 tests.
- **Risk:** CI/reviewer noise can obscure real regressions. None of these files
  changed in R1A, and functional behavior passed.
- **Verdict:** non-blocking baseline/environment finding; avoid production
  correctness gates based on sub-second wall-clock timing on shared hosts.

## 20. R1B readiness decision

**`APPROVED_WITH_NON_BLOCKING_FINDINGS` for R1B design.**

The approval criteria are satisfied:

- public webhook behavior is unchanged;
- all compatibility fields are equivalent;
- HOLD no-job/no-broker behavior is proven;
- replay behavior is unchanged;
- credential and owner binding are unchanged;
- canonical output is strictly non-authoritative;
- registry/analyzer failure is isolated from webhook ingestion;
- no execution-path scope creep exists;
- required focused regressions, frontend regression/build checks, and
  confirmation runs pass.

R1B should treat NB-01 through NB-05 as design inputs before canonical/analyzer
data becomes authoritative, durable, or externally visible.

Explicit safety confirmation:

- No runtime code changed in this review.
- No test logic changed.
- No registry changed.
- No prompt changed.
- No transport changed.
- No migration was created.
- No execution router changed.
- No PaperBroker or Dhan behavior changed.
- No AI conversion was enabled.
- No live order occurred.
- No merge or deployment occurred.
