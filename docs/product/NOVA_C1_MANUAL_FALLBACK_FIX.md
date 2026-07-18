# NOVA C1 Manual Fallback Contract Fix

## Status

`C1_MANUAL_FALLBACK_FIXED_PENDING_REREVIEW`

- Starting review commit:
  `ee167ce20f9712bab455e7bd76a097e95c20337f`
- Blocked review:
  `docs/product/NOVA_C1_CLAUDE_PINE_VERTICAL_SLICE_REVIEW.md`
- Branch:
  `phase-product-claude-pine-manual-fallback-fix`
- C2 remains unauthorized pending independent re-review.

## Original blocker

The former manual package named the C1 schema but did not include it. It then
embedded Prompt V3.1's legacy request for multiple artifacts and model-produced
Transport V2. The manual parser accepted one strict C1 JSON object, so the
generated instructions could not reliably produce parser-compatible input.

## Corrected contract

`ClaudePineConversionOutput` in
`backend/app/schemas/pine_conversion.py` is the sole response-model authority.
The package generator serializes its Pydantic validation JSON Schema at runtime;
there is no separately maintained schema.

The package now requires:

- exactly one raw JSON object;
- schema version `nova.claude-pine-conversion.v1`;
- the exact submitted source SHA-256;
- the actual required fields, nested objects and enum values;
- no unknown fields;
- one non-empty strategy layer containing the three canonical signal booleans;
- behavior-change disclosure through the permitted structured fields; and
- no prose, Markdown fence or additional artifact.

Prompt V3.1 remains the reviewed historical behavior-preservation foundation.
Its legacy output format is not included in the C1 manual package. The current
C1 structured response envelope is authoritative.

## Strategy layer and Transport V2

Manual Claude output contains strategy/indicator logic and C1 canonical signals
only. It may not contain transport, `alert()`, webhook URLs, credentials,
broker/execution fields, quantity, strike, expiry, security ID, or paper/live
mode.

NOVA extracts `strategy_layer`, reads the hash-pinned Transport V2 template,
substitutes only the approved strategy/version placeholders, appends exactly one
transport block, calculates the final candidate SHA, and executes the existing
deterministic Pine validation. Prompt V3.1 and Transport V2 files were not
modified.

## Exact source binding

The generated package contains the exact stored Pine source once, its SHA-256,
approved options, matched capability IDs and policies, and deterministic
analyzer findings. No source normalization occurs during package generation.

The response must return the same SHA. Missing or mismatched SHA, wrong schema,
missing fields, unknown fields, empty strategy layer, model-generated transport,
or non-JSON prose fails closed before candidate review.

## Manual/API parity

Both paths use:

1. `ClaudePineConversionOutput`;
2. `_validate_layer`;
3. capability-manifest comparison;
4. `_assemble_candidate`;
5. hash-pinned Transport V2;
6. `pine_validation` and `personal_pine_service.validate_version`;
7. the same candidate SHA calculation; and
8. the same compile-only admin approval binding.

The only intended provenance difference is:

```text
provider_mode = MANUAL_ADMIN_COPY_PASTE
```

A response declaring `logic_changed=true` cannot become automatically
review-ready. It fails closed with
`LOGIC_CHANGED_REQUIRES_MANUAL_CORRECTION`.

## Authorization and secret safety

Manual package generation, source viewing, response submission and review
remain behind `require_admin` and conversion ownership. Normal users receive
403. The package contains no Anthropic key, authorization header, Dhan
credential, private webhook credential, server environment value or filesystem
path. Claude conversion remains disabled by default and no real API key is
needed for this fallback.

## Tests

Focused backend tests verify:

- authoritative schema version, fields, nested schema and enums;
- exact source and SHA;
- one-object/raw-JSON instructions;
- absence of legacy artifacts, transport template and secrets;
- valid manual response acceptance and manual provenance;
- server-owned Transport V2 assembly and candidate SHA;
- wrong SHA, missing field, unknown field, wrong schema, empty layer,
  non-JSON prose and transport-in-layer rejection;
- logic-change fail-closed behavior;
- admin-only access;
- approval/rejection integrity; and
- zero strategy instances, credentials and execution jobs.

Frontend tests verify the administrator can generate, copy and view the exact
manual package and paste the returned JSON without exposing provider controls
or secrets.

Verification results:

- focused C1 manual/backend: 13 passed;
- C1, Pine, Prompt V3/V3.1, Transport V2 and authorization matrix: 148 passed;
- full backend: 1,127 collected across 82 files, 1,126 passed and one known
  Dhan failure reproduced identically at the starting review commit;
- frontend: 26 Vitest tests passed;
- TypeScript, production build, changed-file ESLint, Python compilation,
  Alembic head and `git diff --check`: passed; and
- Ruff: 11 C1 findings fixed; 16 unrelated pre-existing findings remain in
  `config.py` and legacy compact route formatting. The correction service and
  test file are Ruff-clean.

## Rollback

1. Keep `CLAUDE_CONVERSION_ENABLED=false`.
2. Revert the C1F fix commit.
3. Preserve submitted source, candidate, validation and review evidence.
4. Do not alter Prompt V3.1, Transport V2, migrations, execution data or
   credentials.
5. The prior manual package remains blocked and must not be treated as a valid
   fallback after rollback.

## Re-review requirement

This implementation resolves the narrow manual-contract blocker only. An
independent C1 re-review must confirm package completeness, source binding,
manual/API validation parity, transport ownership, admin authorization and
zero execution authority before C2 is authorized.
