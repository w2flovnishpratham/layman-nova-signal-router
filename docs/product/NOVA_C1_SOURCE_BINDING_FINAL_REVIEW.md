# NOVA C1 Source-Binding Final Review

## Review identity

- Review branch: `phase-product-claude-pine-source-binding-final-review`
- Reviewed fix branch: `phase-product-claude-pine-source-binding-fix`
- Reviewed commit: `316e9ff151bed6da3d235fdce10c086eaa8b1678`
- Pre-fix baseline: `19272880bda9d868c42e5be3ad84a365c9e02b34`
- Review scope: C1F2 final source-binding integrity correction
- Review method: independent static trace, exact-byte runtime probes, old/new defect reproduction, focused and complete regression matrices
- Runtime, test, prompt, transport, provider, execution, migration, and frontend code modified by this review: none

## Final verdict

```text
APPROVED_FOR_C2_TRADINGVIEW_INTEGRATION
```

C1 is closed permanently. The reviewed correction fails closed when the stored
source bytes, stored source digest, conversion-request digest, or submitted
response digest cease to describe the same private source artifact. It blocks
candidate creation before any derived strategy version, validation report, or
admin-review row can survive.

The known Dhan validation failure is not caused by C1F2. It reproduces
identically at the pre-fix baseline, and the C1F2 correction does not change the
Dhan path.

## Scope and correction-diff audit

The correction diff from the baseline to the reviewed commit contains:

| Path | Classification |
| --- | --- |
| `backend/app/services/admin_pine_conversion_service.py` | C1 source-binding implementation |
| `backend/app/tests/test_admin_claude_pine_conversion.py` | C1 source-binding regression evidence |
| `docs/product/NOVA_C1_CLAUDE_PINE_VERTICAL_SLICE.md` | C1 documentation |
| `docs/product/NOVA_C1_SOURCE_BINDING_FIX.md` | C1 correction documentation |

No correction-diff changes exist in:

- Prompt V3.1 or Transport V2
- the Anthropic provider or response schema
- API routers outside the reviewed service boundary
- webhook execution
- strategy engine selection
- `StrategyInstance`
- credentials
- jobs
- brokers, positions, Dhan, or Paper execution
- R1B decision integration
- database models or Alembic migrations
- frontend implementation

`git diff --check 19272880bda9d868c42e5be3ad84a365c9e02b34..316e9ff151bed6da3d235fdce10c086eaa8b1678`
completed successfully.

## Exact source-byte integrity

The implementation computes source digests from the exact stored string encoded
as UTF-8:

```text
sha256(value.encode("utf-8")).hexdigest()
```

No trimming, newline normalization, Unicode normalization, reserialization, or
other content transformation occurs before hashing.

An independent runtime probe submitted a Pine source containing:

- leading spaces before the Pine version directive
- trailing spaces on source lines
- trailing spaces at the end of the source
- a Unicode `Ω` character

The request completed with HTTP 200 and `READY_FOR_ADMIN_REVIEW`. The submitted
digest equalled the SHA-256 of the exact UTF-8 bytes, the retrieved original
source matched exactly, and the generated package contained the exact source.

```text
EXACT_SOURCE_BYTES_CONFIRMED
```

## Four-way source binding

The reviewed service verifies one consistent identity across:

1. the conversion request's `source_sha256`
2. the original strategy version's `source_sha256`
3. the source artifact's stored `source_sha256`
4. a fresh SHA-256 computed from the current artifact content

When a response digest is supplied, it must also equal the conversion request
digest. The source verifier additionally binds the records by:

- conversion request owner
- personal/private catalog strategy
- input strategy version
- version creator
- Pine source artifact kind
- artifact submitter

The query rejects missing, foreign, substituted, or mismatched source records.

```text
FOUR_WAY_SOURCE_BINDING_CONFIRMED
```

## Manual pre-ingestion binding

Manual response submission:

1. parses the strict C1 response schema
2. locks and reloads the owned conversion request
3. locks and reloads the selected private strategy, original version, and source
   artifact
4. validates the four-way binding and response digest
5. only then permits the request to enter the conversion-running state

An integrity failure records a generic safe reason and returns an error without
accepting the response as a candidate.

```text
MANUAL_PRE_INGESTION_BINDING_CONFIRMED
```

## Shared persistence binding

The shared candidate-persistence path performs the same locked verification
again before:

- assembling candidate content
- creating the exact derived strategy version
- assigning a candidate version to the request
- creating validation output
- creating or exposing admin review state

Manual, API, and cache-backed responses converge on this persistence boundary.
The final binding check therefore protects every supported C1 ingestion path,
including changes occurring after an earlier check.

```text
SHARED_PERSISTENCE_BINDING_CONFIRMED
```

## TOCTOU review

The regression suite mutates committed source state immediately before invoking
the real shared persistence function. Both manual and API paths then:

- return HTTP 409
- record `SOURCE_ARTIFACT_INTEGRITY_MISMATCH`
- enter `manual_conversion_required`
- leave candidate and validation-report identifiers unset
- create no admin-review row

This directly exercises the transaction boundary rather than approximating it
with static source inspection.

```text
SOURCE_TOCTOU_FAILS_CLOSED
```

## Original defect reproduction

The reported defect was independently reproduced using the same sequence at the
baseline and reviewed commits:

1. generate a package from a valid source artifact
2. mutate only the artifact content while leaving its stored digest unchanged
3. submit the previously valid response

Results:

| Commit | HTTP | Workflow status | Candidate | Validation report |
| --- | ---: | --- | --- | --- |
| `19272880bda9d868c42e5be3ad84a365c9e02b34` | 200 | `ready_for_admin_review` | created | created |
| `316e9ff151bed6da3d235fdce10c086eaa8b1678` | 409 | `manual_conversion_required` | absent | absent |

At the reviewed commit, no admin-review row was created.

```text
REPORTED_SOURCE_MUTATION_FIXED
```

## Source-mutation and reference matrix

Committed tests cover:

- source-content-only mutation
- stored-artifact-digest-only mutation
- simultaneous source-content and stored-digest mutation
- a response presenting the newly mutated digest
- manual-path TOCTOU mutation
- API-path TOCTOU mutation
- API mutation before provider invocation
- foreign conversion request
- foreign input version
- foreign source artifact
- foreign owner
- missing source artifact
- conversion-request/strategy mismatch
- a valid unchanged manual response
- a valid unchanged API response
- LF source bytes
- CRLF source bytes
- no trailing newline
- two trailing newlines
- Unicode source content

The tests use the real route/service and database transaction behavior. They are
not source-text searches.

An independent literal foreign-artifact substitution probe deleted the target
artifact and reassigned a donor artifact to the target input version. Manual
response ingestion returned:

```text
HTTP 409
reason: SOURCE_ARTIFACT_INTEGRITY_MISMATCH
workflow: manual_conversion_required
candidate: absent
validation report: absent
admin reviews: 0
StrategyInstance rows: 0
credential rows: 0
execution-job rows: 0
```

```text
SOURCE_MUTATION_MATRIX_CONFIRMED
```

## Candidate atomicity

Integrity-failure assertions verify:

- workflow status is `manual_conversion_required`
- candidate version identifier is null
- validation report identifier is null
- no extra target-strategy version survives
- no strategy validation report survives
- no strategy admin-review row survives
- no execution resources are created

The service records the safe failure state and commits that state before
returning HTTP 409, without retaining a partial candidate transaction.

```text
CANDIDATE_ATOMICITY_CONFIRMED
```

## Failure state and information safety

The externally visible integrity failure is generic. It does not disclose:

- private Pine source
- source or response digests
- filesystem paths
- database details
- SQL
- credentials
- provider payloads
- raw internal exceptions

The reviewed path does not log private source content. The durable failure state
uses:

```text
status: manual_conversion_required
reason: SOURCE_ARTIFACT_INTEGRITY_MISMATCH
validation: NOT_RUN
review: PENDING
structured output accepted: false
```

```text
SOURCE_FAILURE_STATE_CONFIRMED
```

## Valid C1 behavior

Unchanged valid source artifacts continue through both supported paths:

- manual response ingestion
- API/provider response ingestion

Exact-byte variants, including LF, CRLF, missing final newline, multiple final
newlines, and Unicode, retain their byte identity and pass when all bound
digests match.

```text
VALID_C1_PATHS_UNCHANGED
```

## Frozen C1 contract verification

The correction did not modify Prompt V3.1 or Transport V2.

| Frozen artifact | Git blob at baseline | Git blob at reviewed commit | SHA-256 |
| --- | --- | --- | --- |
| Prompt V3.1 | `62e670dd61817fbdbb5fd543821cd64ad02470da` | `62e670dd61817fbdbb5fd543821cd64ad02470da` | `4fc31dbd5a94429806227754a32959716e87f68cbb20b952c746d8a11e8785b7` |
| Transport V2 | `e4cebfb4263d218ee25dcf45a0b2191cec69a4db` | `e4cebfb4263d218ee25dcf45a0b2191cec69a4db` | `18a3247c93c0c17e2bb70847a635c721bacf6e231d8d14c14db7871da56ef96f` |

The manual package implementation block is unchanged by the correction.
Regression coverage continues to verify:

- the real `ClaudePineConversionOutput` JSON schema
- exactly one raw JSON response contract
- `strategy_layer` output only
- exact selected source inclusion
- no model-controlled transport
- admin-only access

`CLAUDE_CONVERSION_ENABLED` remains false in application defaults and the
environment example. No live provider key was used during review.

```text
FROZEN_C1_CONTRACTS_UNCHANGED
```

## Zero execution authority

The correction introduces no execution imports or execution calls. Source
mutation and substitution probes produce zero:

- `StrategyInstance` rows
- strategy credential rows
- execution-job rows

The C1 output remains an inert candidate awaiting admin review. It cannot place
orders, select a broker, create execution credentials, invoke webhooks, or grant
runtime strategy authority.

```text
C1_ZERO_EXECUTION_AUTHORITY_CONFIRMED
```

## Independent regression evidence

### Focused source-binding tests

Selection:

```text
-k "source_mutations or toctou or source_mutation_fails_before_provider or owner_and_reference or byte_variants"
```

Result:

```text
10 passed, 13 deselected
```

### Complete C1 backend module

```text
23 passed
```

### C1 and frozen-contract backend matrix

The matrix included C1 conversion, Pine, Prompt V3/V3.1, package, Transport,
analyzer, registry, and admin-authorization coverage.

```text
131 passed
```

### Complete backend suite

All 82 backend test modules were run in deterministic contiguous shards.

```text
1,137 collected
1,136 passed
1 failed
```

The only failure was:

```text
test_dhan_payload_validation_passes_after_security_id_resolution
```

Observed assertion:

```text
expected: validation["ok"] is True
actual:   False
security_id resolution: ok=True, securityId=999999
```

The identical test, assertion, expected value, actual value, and resolution
output reproduce at baseline commit
`19272880bda9d868c42e5be3ad84a365c9e02b34`. No Dhan implementation or test is
changed by the C1F2 correction. This is therefore a known baseline failure and
not a C1 source-binding regression.

### Frontend verification

```text
Vitest: 4 files, 26 tests passed
TypeScript: tsc -b passed
Production build: passed
```

The production build emitted only pre-existing chunk-size and plugin-timing
advisories.

### Additional verification

```text
python -m alembic heads
0015_r1b_persistence (head)

Python compileall
passed

Ruff on changed implementation and test files
passed
```

```text
SOURCE_BINDING_TEST_EVIDENCE_SUFFICIENT
```

## Section verdicts

```text
EXACT_SOURCE_BYTES_CONFIRMED
FOUR_WAY_SOURCE_BINDING_CONFIRMED
MANUAL_PRE_INGESTION_BINDING_CONFIRMED
SHARED_PERSISTENCE_BINDING_CONFIRMED
SOURCE_TOCTOU_FAILS_CLOSED
REPORTED_SOURCE_MUTATION_FIXED
SOURCE_MUTATION_MATRIX_CONFIRMED
CANDIDATE_ATOMICITY_CONFIRMED
SOURCE_FAILURE_STATE_CONFIRMED
VALID_C1_PATHS_UNCHANGED
FROZEN_C1_CONTRACTS_UNCHANGED
C1_ZERO_EXECUTION_AUTHORITY_CONFIRMED
SOURCE_BINDING_TEST_EVIDENCE_SUFFICIENT
```

## C1 closure and C2 authorization

C1 is permanently closed at reviewed commit
`316e9ff151bed6da3d235fdce10c086eaa8b1678`.

This review authorizes design and implementation work only for the defined C2
TradingView-integration scope:

- TradingView compilation tracking
- Managed/Self strategy installation
- `StrategyInstance`
- private strategy credentials
- HOLD verification
- owner-only engine visibility
- Paper eligibility

This review does not authorize:

- normal-user Claude access
- live-trading eligibility
- broker or order-path changes
- browser automation
- deployment
- merge of this review branch

No runtime, test, migration, prompt, transport, provider, execution, frontend, or
application configuration change was made as part of this final review.
