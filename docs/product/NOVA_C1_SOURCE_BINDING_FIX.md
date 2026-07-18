# NOVA C1F2 — Current Source Artifact Binding Fix

## Status

```text
C1_SOURCE_BINDING_FIXED_PENDING_FINAL_REREVIEW
```

- Starting branch: `phase-product-claude-pine-manual-fallback-rereview`
- Starting SHA: `19272880bda9d868c42e5be3ad84a365c9e02b34`
- Fix branch: `phase-product-claude-pine-source-binding-fix`
- Blocked review:
  `docs/product/NOVA_C1_MANUAL_FALLBACK_REREVIEW.md`
- Migration impact: none
- Execution-authority impact: none

## Reported defect

Manual response ingestion previously compared the response SHA only with
`PineConversionRequest.input_source_sha256`. If the current
`StrategySourceArtifact.content` changed after manual-package generation while
the stored request SHA remained unchanged, the old response could still create
a candidate and reach `READY_FOR_ADMIN_REVIEW`. Approval detected the mutation,
but that was too late.

The corrected behavior rejects the response before candidate construction,
Transport V2 persistence, deterministic validation, or review readiness.

## Authoritative integrity rule

The source is hashed exactly as it was at original C1 submission:

```python
hashlib.sha256(source.encode("utf-8")).hexdigest()
```

There is no line-ending, whitespace, trailing-newline, or Unicode
normalization. The required invariant is:

```text
PineConversionRequest.input_source_sha256
==
StrategySourceArtifact.content_sha256
==
SHA256(current StrategySourceArtifact.content exact UTF-8 bytes)
==
ClaudePineConversionOutput.source_sha256
```

The corresponding `StrategyVersion.source_sha256` is also required to equal the
request SHA.

The current strategy must remain the private personal strategy owned by the
authenticated administrator. The input version must belong to that strategy
and owner. The unique `pine_script` artifact must belong to that input version
and submitter. Request JSON cannot select an artifact, version, strategy, owner,
or administrator.

## Locked pre-ingestion check

`submit_manual_response()` parses the strict C1 JSON and then enters the
existing locked workflow transition. In that transaction it:

1. reloads and locks the owner-scoped `PineConversionRequest`;
2. reloads and locks the expected private `StrategyCatalog`;
3. reloads and locks the request's original `StrategyVersion`;
4. reloads and locks the unique original `StrategySourceArtifact`;
5. recomputes SHA-256 from the current exact content;
6. verifies the owner/reference chain and all authoritative hashes; and
7. changes the request to `ai_conversion_running` only after integrity passes.

A response-only SHA mismatch preserves the established 422
`SOURCE_SHA_MISMATCH` response. Missing, foreign, retargeted, deleted, or
mutated source evidence returns 409
`SOURCE_ARTIFACT_INTEGRITY_MISMATCH`.

## Shared persistence recheck and TOCTOU protection

`_persist_candidate()` is shared by manual, API, and API-cache results. Its
candidate transaction now reloads and locks the conversion, strategy, original
version, and source artifact, then repeats the exact source and response binding
check before it:

- renders Transport V2;
- creates a candidate `StrategyVersion`;
- creates source/layer artifacts;
- assigns `candidate_version_id`; or
- transitions the request to validation.

The API path retains its existing earlier source-integrity check before provider
use. Thus manual and API paths have path-specific validation plus the same
final persistence boundary.

Tests mutate the source through a committed database transaction immediately
after the first check and immediately before the real `_persist_candidate()`
call. The final reload detects the mutation. This proves the persistence check
does not trust source content retained from an earlier SQLAlchemy session.

## Closed failure and candidate atomicity

The persistence-time mismatch is handled before candidate-related writes. The
same transaction records:

```text
status = manual_conversion_required
safe_error_code = SOURCE_ARTIFACT_INTEGRITY_MISMATCH
structured_output_valid = false
validation_status = NOT_RUN
review_status = PENDING
```

The route returns HTTP 409 without source content, hashes, paths, SQL details,
or raw exceptions. Tests verify:

- candidate version count remains unchanged;
- `candidate_version_id` remains null;
- `validation_report_id` remains null;
- no candidate source or strategy-layer artifact exists;
- no `StrategyAdminReview` exists;
- the request is not `READY_FOR_ADMIN_REVIEW`; and
- no provider retry or package regeneration occurs.

Because the integrity check precedes all candidate writes within the shared
transaction, a failed check cannot leave a partial candidate or review-ready
transition.

## Regression coverage

Dynamic C1 tests cover:

- exact reported content-only mutation after manual-package generation;
- stored artifact SHA mutation with unchanged content;
- content and stored SHA changed together while the request remains original;
- response retargeting to the new artifact SHA;
- mutation between preliminary manual validation and candidate persistence;
- mutation between API provider validation and candidate persistence;
- API mutation before provider use with zero provider calls;
- source artifact from another conversion;
- source artifact/version from another owner;
- missing/deleted source artifact;
- request/strategy/version mismatch;
- valid unchanged manual response;
- valid fake-provider API response;
- exact LF source;
- exact CRLF source;
- trailing newline and no trailing newline;
- Unicode Pine content;
- zero candidate, validation report, or review evidence on failure; and
- zero `StrategyInstance`, webhook credential, or execution job.

Verification at implementation completion:

```text
Focused source-binding tests: 10 passed
Complete C1 backend module: 23 passed
C1/Pine/Prompt/Transport/auth matrix: 131 passed
Full backend: 1,137 collected; 1,136 passed; 1 baseline Dhan failure
Frontend Vitest: 26 passed
TypeScript: passed
Production build: passed
Alembic head: 0015_r1b_persistence
Changed-file Ruff: passed
Python compilation: passed
git diff --check: passed
```

The Dhan failure was:

```text
test_dhan_payload_validation_passes_after_security_id_resolution
expected validation["ok"] is True
actual validation["ok"] is False
```

It reproduced identically at starting SHA
`19272880bda9d868c42e5be3ad84a365c9e02b34`. C1F2 does not change Dhan,
execution, router, broker, webhook, or position files.

## Frozen contract verification

The manual package is unchanged. It still:

- obtains its schema from
  `ClaudePineConversionOutput.model_json_schema(mode="validation")`;
- requests exactly one raw JSON object;
- requests `strategy_layer` only;
- includes exact source, recorded SHA, analyzer findings, capabilities, and
  approved options;
- excludes model-owned Transport V2; and
- remains administrator-only.

Frozen artifacts remain byte-identical:

```text
Prompt V3.1 Git blob:
62e670dd61817fbdbb5fd543821cd64ad02470da
Prompt V3.1 SHA-256:
4fc31dbd5a94429806227754a32959716e87f68cbb20b952c746d8a11e8785b7

Transport V2 Git blob:
e4cebfb4263d218ee25dcf45a0b2191cec69a4db
Transport V2 SHA-256:
18a3247c93c0c17e2bb70847a635c721bacf6e231d8d14c14db7871da56ef96f
```

Claude conversion remains disabled by default. No real Anthropic key was used.

## Tenant, idempotency, money, and rollback

- Tenant isolation: strengthened. The source verifier binds the conversion,
  strategy, version, artifact, and authenticated owner chain server-side.
- Idempotency: unchanged. No retry, cache-key, or identity rule changed.
- Money correctness: unchanged. The source and candidate remain inert evidence;
  no execution path is introduced.
- Migration: none. No model or schema changed.
- Rollback: keep `CLAUDE_CONVERSION_ENABLED=false`, revert the C1F2 commit, and
  preserve existing source, candidate, validation, and review evidence. No
  database downgrade is required. Do not alter Prompt V3.1, Transport V2, or
  execution records.

## Final re-review requirement

One targeted independent re-review must verify the four-way source invariant,
pre-ingestion and shared persistence checks, TOCTOU rejection, candidate
atomicity, owner/reference isolation, unchanged manual package, frozen prompt
and transport hashes, and zero execution authority.

C1 and C2 remain unapproved until that re-review passes.
