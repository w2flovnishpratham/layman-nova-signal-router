# NOVA C1 Independent Claude Pine Vertical-Slice Review

## Review identity

- Starting SHA: `5a31d5da829600c450060aecaf93fb5ab6e41851`
- Reviewed SHA: `9f007ab2d2eebde2cf0ee56943aca819909e3011`
- Review branch: `phase-product-claude-pine-vertical-slice-review`
- Review date: 2026-07-18
- Verdict: `BLOCKED_MANUAL_FALLBACK`
- C2 readiness: **NOT AUTHORIZED**

This is a review-only result. No runtime code or test logic was changed.

## Executive finding

The API-backed C1 path has strong admin, source-SHA, structured-output,
server-owned-transport, deterministic-validation and zero-execution boundaries.
It is disabled by default and fails closed.

The permanent manual fallback is not self-sufficient, however. Its package:

1. names `nova.claude-pine-conversion.v1` without supplying that schema;
2. omits required C1 response fields including `schema_version`,
   `strategy_layer`, `signal_mapping`, `behavior_preservation`,
   `capabilities`, and `user_summary`; and
3. embeds Prompt V3.1's conflicting instruction to return three artifacts and
   include frozen Transport V2, after telling the manual operator to return one
   strategy-layer-only JSON object without transport.

The API path receives the Pydantic output schema separately through Anthropic
`messages.parse`. The manual copy/paste path does not. A valid manual response
constructed directly by the test helper passes the shared validators, but that
does not prove the generated package can instruct Claude to produce one.
Therefore `MANUAL_FALLBACK_PARITY_CONFIRMED` cannot be issued.

## Changed-file classification

| File | Classification | Review result |
|---|---|---|
| `backend/.env.example` | CONFIGURATION | C1 disabled by default; no secret value committed |
| `backend/app/config.py` | CONFIGURATION | Backend-owned model, timeout, limits and quotas |
| `backend/app/routers/pine_conversion.py` | BACKEND_ROUTE | Admin routes use `require_admin` |
| `backend/app/schemas/pine_conversion.py` | BACKEND_SCHEMA | Strict Pydantic models with `extra="forbid"` |
| `backend/app/services/admin_pine_conversion_service.py` | BACKEND_SERVICE | C1 orchestration and persistence boundary |
| `backend/app/services/pine_conversion_provider.py` | BACKEND_PROVIDER | Anthropic adapter, disabled and fake providers |
| `backend/app/tests/test_admin_claude_pine_conversion.py` | TEST | Ten dynamic C1 tests |
| `backend/requirements.txt` | CONFIGURATION | Adds the Anthropic SDK |
| `docs/product/NOVA_C1_CLAUDE_PINE_VERTICAL_SLICE.md` | DOCUMENTATION | Implementation record |
| `frontend/src/api.ts` | FRONTEND_ADMIN_UI | C1 admin API types and calls |
| `frontend/src/strategies/AdminPineConversion.test.tsx` | TEST | Six admin-workspace UI tests |
| `frontend/src/strategies/AdminPineConversion.tsx` | FRONTEND_ADMIN_UI | Paste/upload, review and decision workspace |
| `frontend/src/strategies/ImportedPinePage.test.tsx` | TEST | Admin integration mocks |
| `frontend/src/strategies/ImportedPinePage.tsx` | FRONTEND_ADMIN_UI | Admin workspace integration |
| `frontend/src/strategies/personalStrategies.css` | FRONTEND_ADMIN_UI | C1 presentation only |

No changed file was classified `UNEXPECTED`.

The baseline-to-reviewed diff does not modify Prompt V3.1, Transport V2,
webhook ingestion, `StrategySignal`, `StrategyExecutionJob`, execution routing,
risk management, `PaperBroker`, Dhan, position storage, canonical decisions,
canonical outcomes or canonical rejections.

## Boundary results

### 1. Administrator authorization

Result: `ADMIN_ONLY_BOUNDARY_CONFIRMED`

- Unauthenticated list request through the real dependency path: HTTP 401.
- Authenticated non-admin submission and detail requests: HTTP 403.
- Authenticated administrator: allowed.
- Submission, list/source viewing, conversion, manual package, manual response,
  candidate/provenance viewing, approval and rejection all depend on
  `require_admin`.
- Cross-admin ownership is additionally constrained by `owner_user_id`.
- Attempts to submit `owner_user_id`, `admin_email`, `model`,
  `system_prompt`, `api_key`, `transport`, `broker_configuration`, or
  `execution_mode` all returned HTTP 422.

### 2. Exact source and SHA binding

Backend result: `EXACT_SOURCE_BINDING_CONFIRMED` for the exact Unicode string
accepted by the API.

Independent dynamic probes preserved and re-hashed correctly:

- LF;
- CRLF;
- one or more trailing newlines;
- no trailing newline; and
- Unicode text.

For every variant:

```text
source_sha256 == SHA256(source.encode("utf-8"))
analysis.source_sha256 == source_sha256
retrieved original_source == submitted source
```

The SHA chain is checked at submission, semantic analysis, provider response,
candidate persistence, deterministic validation and approval. Mutating the
stored original source before approval returned HTTP 409
`CANDIDATE_SHA_MISMATCH`. Mutating the transport portion of the candidate
before approval produced the same closed result.

Non-blocking edge finding: browser `TextDecoder("utf-8", {fatal: true})` uses
the default BOM handling and removes a leading UTF-8 BOM before submission.
Pasted text and non-BOM UTF-8 files are exact; raw uploaded bytes containing a
BOM are not byte-for-byte recoverable. The product must either document BOM as
an encoding marker outside the Pine source or preserve it deliberately.

### 3. Pre-analysis and provider blocking

Result: `LOCAL_BLOCKERS_PREVENT_PROVIDER_USAGE`

The real semantic pre-analyzer and capability registry run in `submit`, before
token counting or provider construction. L3/L4 hard-blocked source cannot enter
`convert`. The hard-block probe observed:

```text
token-count calls = 0
conversion calls  = 0
repair calls      = 0
```

Disabled conversion and missing provider configuration also made zero provider
calls. Provider output cannot clear the persisted deterministic blocker.

### 4. Claude provider security

Result: `CLAUDE_PROVIDER_BOUNDARY_CONFIRMED`

- `CLAUDE_CONVERSION_ENABLED` defaults to `false`.
- Missing API key or model becomes `MANUAL_CONVERSION_REQUIRED`.
- The model comes from backend configuration and is not request-selectable.
- The Anthropic key is read only while constructing the SDK client.
- No C1 persistence, response, logging or frontend surface contains the key.
- Provider failures are mapped to fixed safe error codes.
- The Anthropic message argument set is exactly `model`, `max_tokens`,
  `system`, and `messages`.
- No tools, web search, MCP, shell, code execution or router-level Anthropic
  call exists.
- SDK retries are disabled; retry/repair policy stays in the service.

### 5. Structured output

Result: `STRUCTURED_OUTPUT_FAILS_CLOSED`

`ClaudePineConversionOutput` is strict and forbids additional fields.
Independent schema probes rejected a missing required field, an unknown
tool-like field and an invalid enum. Existing dynamic tests cover valid output,
wrong source SHA, model-generated transport, behavior-change status mismatch
and invalid manual JSON. The validators additionally reject empty output,
unsupported capability/status mismatches, unknown/incomplete capability
manifests, alerts in the strategy layer, unresolved placeholders, secrets and
unbalanced delimiters.

The real provider uses `messages.parse(...,
output_format=ClaudePineConversionOutput)` and treats absent or unparsable
output as a safe provider failure. Claude output remains untrusted.

### 6. Server-owned Transport V2

Result: `SERVER_OWNED_TRANSPORT_CONFIRMED`

- Prompt V3.1 baseline and reviewed blob:
  `62e670dd61817fbdbb5fd543821cd64ad02470da`
- Prompt V3.1 SHA-256:
  `4fc31dbd5a94429806227754a32959716e87f68cbb20b952c746d8a11e8785b7`
- Transport V2 baseline and reviewed blob:
  `e4cebfb4263d218ee25dcf45a0b2191cec69a4db`
- Transport V2 SHA-256:
  `18a3247c93c0c17e2bb70847a635c721bacf6e231d8d14c14db7871da56ef96f`

Both files are byte-identical to the approved baseline. The service reads them
through frozen-hash checks, rejects transport markers in model output, replaces
only the two approved placeholders and enforces exactly one begin/end transport
marker. Candidate validation retains HOLD mutual exclusion and existing
transport validators.

### 7. Behavior-preservation boundary

Result: `BEHAVIOR_PRESERVATION_CONTRACT_CONFIRMED`

The system and restricted-repair policies prohibit new/removed filters,
entry/exit changes, timeframe and `request.security` changes, confirmation or
repainting changes, reversal changes, SL/TP changes and optimization.
`logic_changed=false` is not sufficient for acceptance: source SHA, capability
manifest, strategy-layer constraints, final Pine validation and administrator
review are still mandatory.

As expected for static conversion review, semantic equivalence is not proven by
the model declaration or static validator alone. TradingView compilation and
behavioral verification remain C2 responsibilities after this review blocker is
resolved.

### 8. Token, quota and cache controls

Token/quota result: confirmed.

- Source and provider input are never silently truncated.
- Source byte/line limits and counted-token input limit fail safely.
- `max_tokens` is supplied to Anthropic and `max_tokens` stop reason becomes
  `OUTPUT_TOKEN_LIMIT`.
- Per-admin and global daily quotas run before provider construction.
- An independent global-quota probe returned HTTP 429 with zero token,
  conversion and repair calls.
- The SDK timeout is backend-owned and provider timeout is sanitized.
- Disabled conversion produces zero external calls.

Cache-key result: confirmed.

Independent mutation probes produced a distinct cache key for changes to:

- source SHA;
- prompt SHA;
- registry version;
- registry SHA;
- model;
- response schema;
- deterministic options;
- transport version; and
- transport SHA.

Reuse is intentionally restricted to the same administrator, successful review
states and `structured_output_valid=true`. Invalid/failed rows cannot become
successful hits.

Non-blocking functional defect: a successful cached conversion containing an
administrator-review warning is reconstructed with that warning as a
capability ID. The shared validator then returns HTTP 422
`CAPABILITY_MANIFEST_UNKNOWN`. The probe confirmed zero provider calls, so this
is safe and cost-bounded, but warning-bearing successful candidates are not
reusable. Preserve the original capability classification separately from
human-readable warnings when reconstructing cached output.

### 9. Restricted repair

Result: `ONE_RESTRICTED_REPAIR_CONFIRMED`

- Configuration accepts only zero or one repair.
- The provider has no retry loop and Anthropic SDK retries are zero.
- Exact validation error codes and exact original source are supplied.
- The repair prompt prohibits transport and trading-logic changes.
- A missing canonical signal was repaired exactly once and validated.
- Behavior-change/semantic defects are outside `REPAIRABLE_CODES` and go to
  manual conversion rather than automatic repair.
- A failed repair becomes `MANUAL_CONVERSION_REQUIRED`.

### 10. Manual fallback

Result: **NOT CONFIRMED — RELEASE BLOCKER**

What passes:

- every manual route is admin-only and owner-bound;
- the package contains the exact source once and its SHA;
- relevant capability policy and pre-analysis are present;
- Anthropic, Dhan, webhook, user and environment credentials are absent;
- manually supplied valid C1 JSON goes through the same strict schema, source
  SHA, transport assembly, deterministic validation, review and approval path.

What blocks:

- the generated package does not include the actual C1 response schema;
- six of the nine required top-level C1 fields are not represented;
- the embedded V3.1 package instructs Claude to return three artifacts;
- it instructs Claude to include frozen Transport V2; and
- those instructions conflict with the package's strategy-layer-only JSON
  instruction.

This is not merely a documentation problem. The fake-provider/manual test
constructs a valid C1 object independently of the generated package, replacing
the behavior it claims to prove. The package must give a manual model one
unambiguous C1 schema and one output contract while leaving Prompt V3.1 and
Transport V2 unchanged.

### 11. Approval integrity

Core result: `APPROVAL_SHA_INTEGRITY_CONFIRMED`

Approval binds source SHA, strategy-layer SHA, final candidate SHA, prompt
version/hash, transport version/hash, administrator and timestamp. Validation
failure and stale validation cannot be approved. Source, candidate and
transport mutation fail closed. Duplicate approval, approval after rejection
and regeneration after approval returned HTTP 409. Approval creates only an
immutable admin-review record and status
`APPROVED_FOR_TRADINGVIEW_COMPILE`.

Test-evidence finding: an independent two-thread SQLite probe produced two
approval records because SQLite ignores `SELECT ... FOR UPDATE` and there is no
database uniqueness or optimistic status constraint. PostgreSQL's row lock
should serialize the production path correctly, but that claim is not proven
by the repository's SQLite suite. Add a PostgreSQL concurrency integration test
or an additional database-enforced/optimistic single-decision invariant before
claiming cross-database race coverage.

### 12. Zero execution authority

Result: `C1_ZERO_EXECUTION_AUTHORITY_CONFIRMED`

Searches of changed imports, direct calls, aliases and callbacks found no C1
execution router, risk manager, `PaperBroker`, Dhan order, position authority,
webhook credential generator or execution registry integration. Independent
database probes after conversion, manual submission, approval and rejection:

```text
StrategyInstance rows                    0
StrategyInstanceWebhookCredential rows   0
StrategyExecutionJob rows                0
```

No TradingView alert, broker order, paper/live session or position mutation was
created.

### 13. Secret and source logging

Result: `C1_SECRET_AND_SOURCE_LOGGING_SAFE`

The C1 provider/service/router/UI contain no diagnostic logger, `print` or
frontend console call. Audit records contain IDs, hashes, byte counts,
eligibility and safe modes—not source or secrets. Provider exception text is
not returned. The manual package includes the exact source by design but no API
key or execution credential. Source is returned only through the authorized
admin detail endpoint.

The existing test injects a test Anthropic marker and confirms it is absent
from the package. Static searches found no new Authorization, Dhan, TOTP,
database URL or filesystem-path exposure.

### 14. Frontend

Result: `ADMIN_CONVERSION_UI_CONFIRMED`

The workspace is reachable only from the existing `isAdmin`-gated admin mode;
the backend remains authoritative. Tests cover refresh rehydration, paste/file
submission, analysis and blockers, loading/failure state, manual package and
response, source/layer/final candidate, diff, separate transport, validation,
provenance, approval confirmation and rejection reason.

No model selector, system prompt editor, temperature, tool access, execution
configuration or credential input exists.

### 15. Test quality

Result: **PARTIALLY SUFFICIENT**

The ten backend C1 tests execute real routes, services, persistence, semantic
analysis, transport assembly and validation with only the external provider
replaced by a deterministic fake. Frontend mocks are appropriately at the HTTP
boundary.

The principal insufficient mock is the manual fallback test: it does not derive
the response from, or validate the completeness of, the generated manual
package. The approval-race claim is also not covered under PostgreSQL semantics.
Cache tests cover an exact clean hit and model miss but not warning-bearing
successful candidates or every key dimension; independent review probes exposed
the warning reconstruction defect.

## Regression matrix

### Commands

The review used the pinned Python runtime:

```text
C:\Users\anubh\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```

Focused backend:

```text
python -m pytest \
  app/tests/test_admin_claude_pine_conversion.py \
  app/tests/test_pine_conversion.py \
  app/tests/test_pine_transport_v2.py \
  app/tests/test_pine_v31_source_binding.py \
  app/tests/test_pine_semantic_preanalyzer.py \
  app/tests/test_pine_capability_registry.py \
  app/tests/test_personal_pine.py \
  app/tests/test_auth_login.py \
  app/tests/test_route_auth.py \
  app/tests/test_tradingview_setup.py -q
```

Result: **117 passed**.

Full backend collection:

```text
python -m pytest app/tests --collect-only -q
```

Result: **1,124 tests collected from 82 test files**.

The 82 sorted test files were assigned by index modulo four. Assignment counts
were 21, 21, 20 and 20; every file was assigned exactly once. Sorted file-list
manifest SHA-256:

```text
ca89e8c4b5dd6ffcc95c5350319974fb61467a53a09615058e26650a06fc89c0
```

Shard results:

| Shard | Files | Result |
|---|---:|---|
| 1 | 21 | 267 passed |
| 2 | 21 | 337 passed |
| 3 | 20 | 278 passed, 1 failed |
| 4 | 20 | 240 passed, 1 failed |
| Total | 82 | 1,122 passed, 2 failed |

Failure classification:

1. `test_dhan_payload_validation_passes_after_security_id_resolution`
   failed with `resolution["ok"] is True`, `securityId == "999999"` and
   `validation["ok"] is False`. It failed identically five of five isolated
   runs at the reviewed SHA and identically at baseline
   `5a31d5da829600c450060aecaf93fb5ab6e41851`. The test and relevant path are
   unchanged by C1. Classification: **pre-existing baseline failure; unrelated
   to C1**.
2. `test_production_strategy_webhook_rejects_stale_or_future_timestamp[301]`
   returned 202 instead of 401 in shard 4. It passed five of five isolated runs
   and passed at the baseline SHA. Its `301`-second integer-time boundary can
   enter the accepted 300-second window after test/setup delay.
   Classification: **pre-existing timing/environment flake; unrelated to C1**.

The previously reported R1B concurrency target
`test_concurrent_identical_actions_create_one_of_everything[BUY_CE]` passed five
of five isolated runs. No C1 connection was reproduced. Classification:
**reported baseline/environment noise**.

Other backend checks:

```text
python -m compileall -q app
python -m alembic heads
```

Results:

- Python compilation: pass.
- Alembic head: `0015_r1b_persistence (head)`.

Ruff was run on all changed Python files. It reported 27 findings: one existing
unused config import, existing compact one-line route style plus one added
instance of that style, and fixture-import `F811` findings in the new C1 test.
No code was changed because this phase permits only the report. This is
non-blocking style/test debt, but the Ruff command itself is not green.

Frontend commands were run directly through the pinned Node runtime:

```text
node node_modules/vitest/vitest.mjs run
node node_modules/typescript/bin/tsc -b
node node_modules/vite/bin/vite.js build
node node_modules/eslint/bin/eslint.js \
  src/api.ts \
  src/strategies/AdminPineConversion.tsx \
  src/strategies/AdminPineConversion.test.tsx \
  src/strategies/ImportedPinePage.tsx \
  src/strategies/ImportedPinePage.test.tsx
```

Results:

- TypeScript: pass.
- Vitest: 4 files, 26 tests passed.
- Production build: pass; existing chunk-size advisory only.
- Changed-file ESLint: pass.
- `git diff --check`: pass.

## Blocking findings

1. **Manual fallback contract is incomplete and contradictory.** The generated
   package does not provide the strict C1 schema and simultaneously requests the
   incompatible V3.1 three-artifact, model-owned-transport output. This blocks
   the claimed permanent manual vertical slice.

## Non-blocking findings

1. Warning-bearing successful cache entries fail safely with
   `CAPABILITY_MANIFEST_UNKNOWN` instead of being reused.
2. Approval serialization is implemented with PostgreSQL row locking but lacks
   a PostgreSQL integration test or a secondary database invariant; SQLite
   permits duplicate concurrent review rows.
3. Browser upload strips a UTF-8 BOM before API submission; clarify or preserve
   BOM semantics for a literal raw-file-byte guarantee.
4. Changed-file Ruff is not green due existing style debt and new test/route
   style findings.
5. The Dhan baseline failure and timestamp-window flake are unrelated to C1.

## Required completion confirmations

- No runtime code changed during review.
- No test logic changed during review.
- Prompt V3.1 remains unchanged.
- Transport V2 remains unchanged.
- Only administrators can use C1.
- Claude output remains untrusted.
- Claude cannot modify server-owned transport.
- A manually supplied valid response uses the same validation gates, but the
  generated manual package does not yet provide a valid unambiguous contract.
- Approval remains bound to the exact candidate SHA.
- Approval means ready for TradingView compilation only.
- No `StrategyInstance` was created.
- No credential was created.
- No execution job was created.
- No broker, router or position authority changed.
- No live order occurred.
- No merge or deployment occurred.

## Required correction boundary

Do not modify Prompt V3.1 or Transport V2. Correct only the C1 manual-package
assembly so it supplies the complete strict C1 response schema and removes or
unambiguously subordinates the incompatible three-artifact/model-transport
output instruction. Add a dynamic package-contract test that proves a response
following only the generated package can pass the same manual schema and
validation path.

After that correction, rerun this review, including the warning-bearing cache
case and a PostgreSQL approval-race integration check. C2 remains unauthorized
until a passing C1 review verdict is recorded.
