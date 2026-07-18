# NOVA C1F — Independent Manual Fallback Fix Re-review

## Review identity

- Starting SHA: `ee167ce20f9712bab455e7bd76a097e95c20337f`
- Reviewed correction SHA: `f5da951f03af690394e727625b3c833871262bad`
- Original C1 SHA: `9f007ab2d2eebde2cf0ee56943aca819909e3011`
- Review branch: `phase-product-claude-pine-manual-fallback-rereview`
- Review type: narrow, independent, review-only
- Verdict: **BLOCKED_SOURCE_BINDING**

The correction fixes the originally reported schema/package contradiction. The
generated manual package now requests one raw C1 JSON response, embeds the
authoritative `ClaudePineConversionOutput` validation schema, keeps Transport
V2 server-owned, and sends valid manual output through the shared validation
and candidate-persistence path.

One exact-source invariant is still missing. If the stored original
`StrategySourceArtifact.content` is changed after manual-package generation,
`submit_manual_response()` compares the returned SHA only with
`PineConversionRequest.input_source_sha256`. It does not re-hash the current
original artifact before accepting the response. An adversarial test changed
the original content while retaining the recorded SHA, submitted an otherwise
valid response containing the old SHA, and observed:

```text
manual response HTTP status: 200
conversion status: READY_FOR_ADMIN_REVIEW
candidate created: yes
later approval HTTP status: 409
later approval reason: CANDIDATE_SHA_MISMATCH
```

The approval boundary catches the mutation, but the explicit review requirement
is that every source mismatch fail closed at manual response ingestion. A
mismatched source must not create a candidate or become review-ready.

## Correction diff classification

Diff reviewed:

```text
ee167ce20f9712bab455e7bd76a097e95c20337f
..f5da951f03af690394e727625b3c833871262bad
```

| File | Classification | Diff |
|---|---|---:|
| `backend/app/routers/pine_conversion.py` | RUFF_ONLY | +2/-1 |
| `backend/app/services/admin_pine_conversion_service.py` | MANUAL_PACKAGE_FIX | +57/-10 |
| `backend/app/tests/test_admin_claude_pine_conversion.py` | TEST | +117/-6 |
| `docs/product/NOVA_C1_CLAUDE_PINE_VERTICAL_SLICE.md` | DOCUMENTATION | +18/-9 |
| `docs/product/NOVA_C1_MANUAL_FALLBACK_FIX.md` | DOCUMENTATION | +148/-0 |
| `frontend/src/strategies/AdminPineConversion.test.tsx` | TEST | +2/-0 |
| `frontend/src/strategies/AdminPineConversion.tsx` | ADMIN_UI_FIX | +8/-2 |

No unexpected file was changed. The correction did not change Prompt V3.1,
Transport V2, the Anthropic provider, semantic analyzer, capability registry,
webhook ingestion, `StrategyInstance`, credentials, execution jobs, router or
broker authority, positions, Dhan, or R1B evidence writers.

Prompt and transport immutability evidence:

```text
Prompt V3.1 Git blob at original C1 and correction:
62e670dd61817fbdbb5fd543821cd64ad02470da
Prompt V3.1 SHA-256:
4fc31dbd5a94429806227754a32959716e87f68cbb20b952c746d8a11e8785b7

Transport V2 Git blob at original C1 and correction:
e4cebfb4263d218ee25dcf45a0b2191cec69a4db
Transport V2 SHA-256:
18a3247c93c0c17e2bb70847a635c721bacf6e231d8d14c14db7871da56ef96f
```

## Contract review results

### Authoritative response schema

**AUTHORITATIVE_C1_SCHEMA_CONFIRMED**

`manual_package()` obtains the schema from:

```python
ClaudePineConversionOutput.model_json_schema(mode="validation")
```

The generated JSON schema exactly equaled the parser model's current validation
schema. It communicates the schema version, required and nested fields, enum
values, exact source SHA, unknown-field rejection, strategy-layer-only
requirements, and logic-change policy. There is no separately maintained
handwritten response schema.

### One-JSON-object contract

**SINGLE_JSON_RESPONSE_CONFIRMED**

The package requires exactly one raw JSON object and prohibits Markdown fences,
introductory or trailing prose, and additional artifacts. Actual parser probes:

| Input | Result |
|---|---|
| Valid raw JSON | 200; accepted |
| Markdown-fenced JSON | 422 `INVALID_MANUAL_RESPONSE` |
| Prose before JSON | 422 `INVALID_MANUAL_RESPONSE` |
| Prose after JSON | 422 `INVALID_MANUAL_RESPONSE` |
| Two JSON objects | 422 `INVALID_MANUAL_RESPONSE` |
| Legacy three-artifact response | 422 `INVALID_MANUAL_RESPONSE` |

### Legacy contradiction removal

**LEGACY_OUTPUT_CONTRADICTIONS_REMOVED**

The generated package contains no instruction requesting three artifacts, final
webhook-ready Pine, a separate manifest, model-owned Transport V2, `alert()`
transport, webhook URLs, credential placeholders, or execution configuration.
The full Transport V2 blob is absent. The mentions of transport, credentials,
broker fields, and execution fields are explicit prohibitions, not requests.

### Strategy-layer-only and transport ownership

**STRATEGY_LAYER_ONLY_CONFIRMED**

The package limits `strategy_layer` to conversion logic and the three canonical
signals. A response containing the Transport V2 begin marker is rejected with
`MODEL_TRANSPORT_FORBIDDEN`. Both valid API and manual probes produced a
strategy layer without transport and a final candidate containing exactly one
server-appended Transport V2 block.

### Exact source binding

**MANUAL_SOURCE_SHA_BINDING_NOT_CONFIRMED — BLOCKING**

| Case | Result |
|---|---|
| Correct SHA | 200; `READY_FOR_ADMIN_REVIEW` |
| Missing SHA | 422 `INVALID_MANUAL_RESPONSE` |
| Changed SHA | 422 `SOURCE_SHA_MISMATCH` |
| SHA from another conversion | 422 `SOURCE_SHA_MISMATCH` |
| Original source changed after package generation | **200; candidate created; `READY_FOR_ADMIN_REVIEW`** |

The package itself correctly contains the exact original source once, inside
`BEGIN_UNTRUSTED_PINE_SOURCE` / `END_UNTRUSTED_PINE_SOURCE`, plus the recorded
SHA, analyzer findings, matched capabilities, relevant capability policies, and
approved options.

The defect is in the post-package integrity check. `submit_manual_response()`
validates the returned SHA against recorded request metadata but does not verify
that the current original artifact still hashes to that SHA before calling
`_persist_candidate()`.

The smallest correction is to verify both the stored artifact SHA and a fresh
hash of its content against `input_source_sha256` inside the locked manual
response transition, then re-check inside the candidate-persistence transaction
to close a time-of-check/time-of-use window. A regression test must mutate the
original artifact after package generation, submit the old correctly shaped
response, and assert failure with no candidate and no review-ready state.

### Manual/API pipeline parity

**MANUAL_API_VALIDATION_PARITY_CONFIRMED EXCEPT FOR THE BLOCKING PRE-PERSISTENCE
SOURCE-INTEGRITY CHECK**

Both paths use `ClaudePineConversionOutput`, `_validate_layer()`,
`_persist_candidate()`, `_assemble_candidate()`, Transport V2 hash checks,
deterministic validation, candidate hashing, review-readiness rules, and
approval provenance. Probe results for equivalent valid responses:

```text
manual provider mode: MANUAL_ADMIN_COPY_PASTE
API provider mode: CLAUDE_API
both READY_FOR_ADMIN_REVIEW: yes
both eligible_for_review: yes
both contain exactly one server-owned Transport V2: yes
validation report keys equal: yes
```

The API path checks the current original source artifact before provider work.
The manual ingestion path lacks the equivalent current-artifact check. This is
the only observed validation-parity failure.

### Logic-change handling

**MANUAL_LOGIC_CHANGE_FAILS_CLOSED**

`logic_changed=false` can proceed when all validators pass.
`logic_changed=true` returned 422
`LOGIC_CHANGED_REQUIRES_MANUAL_CORRECTION`, left the request
`MANUAL_CONVERSION_REQUIRED`, and created no candidate.

### Admin authorization

**MANUAL_FALLBACK_ADMIN_ONLY_CONFIRMED**

```text
Unauthenticated submission: 401
Normal-user submission: 403
Normal-user package generation: 403
Normal-user exact-detail view: 403
Normal-user manual-response submission: 403
Admin package/response/detail path: allowed
```

Ownership is derived from the authenticated admin. Request fields do not select
another owner or administrator.

### Secret safety

**MANUAL_PACKAGE_SECRET_SAFE**

Unique markers were placed in the isolated test settings for the Anthropic key,
Dhan Client ID, Dhan PIN, TOTP secret, private webhook secret, database path,
and filesystem path. None appeared in the generated package or responses.
Authorization headers and environment-variable values are not included. The
exact Pine source is intentionally present in the returned package. Audit-log
calls record IDs and hashes, not source content.

### Approval integrity

**MANUAL_APPROVAL_SHA_INTEGRITY_CONFIRMED**

Approval binds source, strategy-layer, final-candidate, Prompt V3.1, and
Transport V2 hashes. `StrategyAdminReview` records the reviewer identity and a
non-null `reviewed_at` timestamp. Mutating a manual candidate after validation
caused approval to fail with HTTP 409 `CANDIDATE_SHA_MISMATCH`.

The same approval check also rejected the changed-original-source probe. That
late protection is correct but does not cure the earlier review-readiness
violation.

### Zero execution authority

**C1F_ZERO_EXECUTION_AUTHORITY_CONFIRMED**

Counts after all isolated manual/API probes:

```text
StrategyInstance: 0
StrategyInstanceWebhookCredential: 0
StrategyExecutionJob: 0
```

Static tracing found no TradingView alert creation, worker scheduling, broker or
router call, position mutation, or Paper/Live session creation in the corrected
manual path.

### Test quality

**C1F_TEST_EVIDENCE_INSUFFICIENT FOR EXACT SOURCE MUTATION**

The changed tests dynamically cover generated package content, valid manual
response, wrong SHA, missing field, unknown field, legacy response,
model-owned transport, empty layer, `logic_changed=true`, candidate mutation,
authorization, and zero execution-resource creation. The previous contradictory
package would fail the new schema/package assertions.

The suite does not cover the required case where the original source artifact
changes after manual-package generation but before manual-response submission.
That omitted test would have exposed the blocker.

## Regression matrix

### Commands

Representative commands, all run with Python 3.12.13 and the pinned local Node
runtime:

```text
python -m pytest app/tests/test_admin_claude_pine_conversion.py -k manual -vv
python -m pytest app/tests/test_admin_claude_pine_conversion.py -q
python -m pytest \
  app/tests/test_admin_claude_pine_conversion.py \
  app/tests/test_pine_conversion.py \
  app/tests/test_pine_master_prompt_v3.py \
  app/tests/test_pine_v3_package_assembly.py \
  app/tests/test_pine_v31_source_binding.py \
  app/tests/test_pine_transport_v2.py \
  app/tests/test_pine_semantic_preanalyzer.py \
  app/tests/test_pine_capability_registry.py \
  app/tests/test_route_auth.py -q

# All 82 app/tests/test_*.py modules in deterministic file-name shards.
python -m pytest <deterministic shard files> -q

python -m alembic heads
python -m compileall -q app
python -m ruff check \
  app/config.py \
  app/routers/pine_conversion.py \
  app/services/admin_pine_conversion_service.py \
  app/tests/test_admin_claude_pine_conversion.py

node node_modules/vitest/vitest.mjs run
node node_modules/typescript/bin/tsc -b
node node_modules/vite/bin/vite.js build
node node_modules/eslint/bin/eslint.js \
  src/api.ts \
  src/strategies/AdminPineConversion.tsx \
  src/strategies/AdminPineConversion.test.tsx \
  src/strategies/ImportedPinePage.tsx \
  src/strategies/ImportedPinePage.test.tsx

git diff --check
```

### Results

- Focused manual fallback: 4 passed, 9 deselected.
- Complete C1 backend module: 13 passed.
- C1/Pine/Prompt V3/V3.1/Transport V2/analyzer/registry/auth matrix:
  113 passed.
- Full backend: 1,127 tests across 82 modules accounted for; 1,126 passed after
  isolated reruns; one known Dhan assertion failed.
- Dhan failure:
  `test_dhan_payload_validation_passes_after_security_id_resolution` expected
  `validation["ok"] is True`, received `False`. It reproduced identically at
  starting SHA `ee167ce20f9712bab455e7bd76a097e95c20337f`.
- One concurrent full-shard run hit a transient SQLite `disk I/O error` while
  creating an index. The exact affected test passed immediately in isolation.
- One concurrent shard run failed
  `test_concurrent_identical_actions_create_one_of_everything[EXIT]` because
  the decision count was zero. The exact test then passed three of three
  isolated runs at the reviewed SHA and three of three at the starting SHA.
  No correction-diff file participates in that R1B concurrency path.
- Alembic head: `0015_r1b_persistence (head)`.
- Python compilation: passed.
- Frontend Vitest: 4 files, 26 tests passed.
- TypeScript: passed.
- Production build: passed; existing chunk-size advisory only.
- Changed-file frontend ESLint: passed.
- `git diff --check`: passed.
- Ruff: 16 unrelated baseline findings reproduced: one unused import in
  `config.py` and 15 legacy compact-format findings in
  `routers/pine_conversion.py`. The correction service and C1 test file are
  Ruff-clean. The 11 reported C1 findings remain fixed.

## Blocking and non-blocking findings

### Blocking

1. `submit_manual_response()` does not verify the current original source
   artifact against the recorded source SHA before accepting and persisting a
   manual response. A changed original can create a candidate and reach
   `READY_FOR_ADMIN_REVIEW`.
2. The changed tests omit the required source-changed-after-package regression.

### Non-blocking

1. The deterministic starting-SHA Dhan failure remains unrelated baseline
   behavior.
2. A transient SQLite setup error and an R1B concurrency race appeared during
   parallel full-suite sharding; both passed isolated reruns and neither touches
   the correction scope.
3. Sixteen Ruff findings remain unrelated baseline debt.

## Completion status

```text
Starting SHA: ee167ce20f9712bab455e7bd76a097e95c20337f
Reviewed correction SHA: f5da951f03af690394e727625b3c833871262bad
Review branch: phase-product-claude-pine-manual-fallback-rereview
Verdict: BLOCKED_SOURCE_BINDING
Report: docs/product/NOVA_C1_MANUAL_FALLBACK_REREVIEW.md
C2 readiness: NOT AUTHORIZED
```

- No runtime code changed during re-review.
- No test logic changed during re-review.
- The package uses the actual C1 response schema.
- The package requests exactly one JSON object.
- Legacy three-artifact instructions are absent.
- Claude is not asked to generate Transport V2.
- Manual output contains `strategy_layer` only.
- NOVA appends Transport V2 server-side.
- Manual and API paths use the same parser and downstream validators.
- Manual responses remain bound to recorded source-SHA metadata, but are not
  bound to a fresh hash of the current original artifact at ingestion.
- Manual fallback remains admin-only.
- Prompt V3.1 remains unchanged.
- Transport V2 remains unchanged.
- No `StrategyInstance` was created.
- No credential was created.
- No execution job was created.
- No execution authority changed.
- Claude remains disabled by default.
- No real API key was required.
- No live order occurred.
- No merge or deployment occurred.

C1 is not closed. C2 TradingView integration is not authorized until the
current-original-source integrity check and its regression test are present and
independently re-reviewed.
