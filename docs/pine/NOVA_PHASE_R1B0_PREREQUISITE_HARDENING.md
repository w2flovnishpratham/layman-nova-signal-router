# NOVA Phase R1B-0 — Prerequisite Evidence Hardening

## Scope

R1B-0 hardens the evidence boundary approved by the R1B persistence design. It does not implement persistence.

- R1B-0 creates no migration.
- R1B-0 persists no canonical evidence.
- R1B-0 changes no webhook behavior.
- R1B-0 changes no execution authority.

## Pure domain constant

`DEFAULT_EXCHANGE_SEGMENT` now lives in the pure domain module
`app/domain/trading_constants.py`. The legacy signal adapter imports the value
from that module, so importing the adapter does not load `app.config`, construct
`Settings`, read environment variables, or inspect an `.env` file. `app.config`
re-exports the same `NSE_FNO` value for compatibility with all existing callers.

## Secret-taint policy

`app/domain/secret_taint.py` defines the closed taint classes
`SERVER_TRUSTED`, `USER_IDENTIFIER`, `USER_METADATA`, `SECRET`, and
`DERIVED_SAFE`. Its pure helpers:

- detect case-insensitive `nwk_` credential-shaped text and exact request-local
  known secrets;
- redact credential-shaped diagnostics without returning the matched value;
- project credential-shaped signal identifiers to `credential-shaped-id`;
- validate closed server-authored detail codes, bounded to 160 characters and
  free of controls, newlines, and credential-shaped text;
- build immutable safe metadata containing only valid `strategy_version`,
  `timeframe`, and finite positive `reference_price`.

The metadata builder omits comments, signal identifiers, credentials, identity,
broker, sizing, risk, stop, target, contract, and nested authority fields. Its
canonical JSON representation is bounded to 2 KiB. These helpers are not wired
into webhook acceptance or a persistence path.

## Three-state analyzer confidence

The analyzer retains:

- `HIGH_CONFIDENCE_MATCH` for deterministic lexical or structural evidence;
- `PARTIAL_MATCH` for a proven relevant construct whose parameters or data flow
  cannot be resolved completely;
- `ANALYSIS_INDETERMINATE` for unavailable, malformed, or otherwise unsafe-to-
  determine source.

Tested partial cases cover computed entry stop/limit values, dynamic request
symbol/timeframe context, computed exit quantity, partially constructed custom
JSON, and unresolved fill-dependent trade-state indexing.

Severity is aggregated independently from confidence. The most restrictive
matched registry level wins. Partial evidence is clamped to at least L2, while
L3/L4 matches remain L3/L4. Indeterminate analysis fails closed at L4. Thus a
high-confidence safe base cannot cancel incomplete unsafe evidence, and
incomplete evidence cannot reduce a proven blocker.

## Registry provenance

No capability level or policy changed. Only these five fixture references were
corrected:

| Capability | Previous fixture | Dedicated fixture |
| --- | --- | --- |
| `PROCESS_ORDERS_ON_CLOSE` | F05 | F39 |
| `STRATEGY_ORDER_SEMANTICS` | F13 | F67 |
| `PINE_CONTROLLED_ENTRY_QUANTITY` | F31 | F28 |
| `POSITION_STATE_REFERENCE` | F36 | F34 |
| `PENDING_ORDER_CANCELLATION` | F13 | F15 |

- Previous registry SHA-256:
  `c1a255f3d7a2bce84a8c95f38dac52d2fcf35ee1474e3ecf28289b0708dfe00f`
- R1B-0 registry SHA-256:
  `a237f33a6795d06e371c0e4719d7e581056893b30b1e60fd3768a511aa4b0594`
- Registry entries: 38
- Fixture pairs: 33

Every registry fixture reference resolves and the cited fixture analysis
contains the cited capability.

## Exhaustive fixture evidence

All 33 expected manifests use the closed schema:

`fixture_id`, `source_sha256`, `analyzer_version`, `registry_id`,
`registry_version`, `registry_sha256`, `matched_capabilities_exact`,
`effective_capability_level`, `temporal_classes_exact`,
`blocker_codes_exact`, `disclosure_codes_exact`,
`admin_review_points_exact`, and `confidence`.

All array fields are deduplicated lexicographical lists. Tests compare the full
manifest exactly, including the analyzer/registry identity and both hashes.
Unknown keys, missing keys, filename/ID mismatches, duplicate fixture IDs, hash
mismatches, and any diagnostic/confidence difference fail validation.

The developer-only expectation updater is dry-run by default, requires explicit
`--write`, prints complete diffs and a summary, and performs no network or AI
work. CI does not invoke it.

## Rollback

Revert the single R1B-0 commit. There is no schema rollback, data repair,
backfill, feature-flag change, or deployment action because this phase creates
no migration and writes no evidence.

## Remaining R1B-1 scope

R1B-1 remains unauthorized until an independent evidence review approves this
hardening. Only then may a separate phase consider the additive
`0015_r1b_persistence` migration, database models, disabled-by-default
persistence flags, evidence writers, rejection/outcome handling, and backfill
design described by the approved R1B persistence document.
