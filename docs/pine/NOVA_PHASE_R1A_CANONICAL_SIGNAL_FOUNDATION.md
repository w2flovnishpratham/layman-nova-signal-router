# NOVA Phase R1A — Canonical Signal Foundation

## Scope and authority

R1A introduces an internal canonical target-state contract, a pure four-action compatibility adapter, opt-in shadow comparison, a versioned Pine capability registry, and a deterministic semantic pre-analyzer. It makes no database or external-contract change.

- R1A does not change execution authority.
- R1A does not change the external webhook.
- R1A does not persist canonical fields.
- R1A does not enable new Pine capabilities.
- R1A does not approve Prompt V4.

The existing execution router and per-user JSON position files remain execution-read authority. PostgreSQL position records remain shadow-only.

## Canonical contract

Contract version: `nova.canonical-signal.v1`.

`CanonicalSignalEvent` is a frozen, slotted dataclass with immutable, defensively copied metadata. It represents either a strategy target-state signal or a connectivity test:

| Wire action | Event type | Desired state | Intent reason |
|---|---|---|---|
| `BUY_CE` | `STRATEGY_SIGNAL` | `BULLISH` | `DIRECTIONAL_SIGNAL` |
| `BUY_PE` | `STRATEGY_SIGNAL` | `BEARISH` | `DIRECTIONAL_SIGNAL` |
| `EXIT` | `STRATEGY_SIGNAL` | `FLAT` | `EXPLICIT_EXIT` |
| `HOLD` | `CONNECTIVITY_TEST` | `NONE` | `CONNECTIVITY_TEST` |

Metadata cannot supply identity, broker, execution mode, sizing, contract selection, order, risk, or credential authority. Credential-like plaintext is rejected. Entry, no-op, and reversal remain runtime decisions after the existing position-state evaluation.

## Legacy adapter and placement

Adapter version: `nova.legacy-signal-adapter.v1`.

The adapter accepts exactly `BUY_CE`, `BUY_PE`, `EXIT`, and `HOLD`; it performs no trimming, case normalization, authentication, persistence, position reads, routing, or broker access. Its compatibility edge produces field-equivalent existing `NormalizedSignal` objects for the three trading actions and no signal for `HOLD`.

When `CANONICAL_SIGNAL_SHADOW=true`, the private webhook constructs and compares the canonical compatibility result after parsing, normalization, credential/instance/freshness validation, and durable replay claim, but before the unchanged HOLD or execution-job persistence branch. The existing mapping remains authoritative. A mismatch or adapter error emits only sanitized classification metadata and cannot alter the response, signal, or job.

The flag defaults to `false`; invalid environment values fail closed to `false`. The replay key and payload fingerprint are unchanged.

## Capability Registry V1

- Registry ID: `nova.pine-capabilities`
- Registry version: `v1`
- Registry SHA-256: `c1a255f3d7a2bce84a8c95f38dac52d2fcf35ee1474e3ecf28289b0708dfe00f`
- Entries: 38

The JSON registry is validated against a closed Draft 2020-12 JSON Schema and loaded into frozen typed records. Its hash uses canonical, key-sorted JSON with entries sorted by capability ID, so file entry order does not affect identity. Duplicate IDs, unknown levels or temporal classes, invalid fixture IDs, L4 entries without blocker codes, and attempted execution-enablement declarations fail validation. Multiple matches resolve to the most restrictive capability level.

The registry classifies conversion support; it grants no runtime capability. Pending orders, cancellations, partial exits, pyramiding, generic order semantics, Pine-controlled sizing, fill-dependent state, unsafe lookahead, external data, synthetic prices, and authority-bearing payloads remain blocked or require unavailable backend capability.

## Deterministic pre-analyzer

Analyzer version: `nova.pine-semantic-preanalyzer.v1`.

The analyzer lexically masks comments and strings where API matching requires it, while separately inspecting hostile instruction-like content and custom alert payloads. It detects the R1A strategy/order/exit, sizing, temporal, multi-timeframe, external-data, synthetic-chart, and transport-security foundations. It returns immutable source and registry provenance, matched capabilities, the effective capability level, temporal classes, blocker/disclosure codes, review points, and confidence.

It is not a Pine parser or compiler. It does not execute Pine, call AI, TradingView, Dhan, or any broker, and does not claim complete semantic understanding. Syntactically obvious matches use `HIGH_CONFIDENCE_MATCH`; ambiguous dynamic constructs use `ANALYSIS_INDETERMINATE`. Registry load failure returns a fail-closed indeterminate L4 result and is isolated from webhook processing.

## Fixture coverage

The focused corpus contains 28 Pine fixtures and 28 expected-result artifacts:

| Effective classification | Fixtures |
|---|---|
| `L0_DIRECTLY_SUPPORTED` | F01, F02, F03, F05 |
| `L1_NORMALIZED_WITHOUT_MATERIAL_CHANGE` | F06, F07, F08, F42, F59 |
| `L2_SUPPORTED_WITH_DISCLOSED_CHANGE` | F19, F45 |
| `L3_REQUIRES_BACKEND_CAPABILITY` | F11, F12, F13, F30, F31, F33, F36, F37 |
| `L4_BLOCKED_UNSAFE_OR_UNREPRESENTABLE` | F38, F40, F44, F46, F49, F50, F60, F61, F62 |

F46, F50, and malformed F62 are deliberately indeterminate; all other focused fixtures are high-confidence lexical classifications. Unsupported fixtures are classified only and are not converted or enabled.

## Rollback

Set `CANONICAL_SIGNAL_SHADOW=false` (the default) to bypass all webhook shadow work. The registry and analyzer are internal, unpersisted services and can be removed without data rollback. No migration, public route, prompt, transport, broker, risk, or execution-router rollback is required.

## Remaining work

R1B remains responsible for any separately reviewed additive persistence, source/registry/analyzer provenance records, and rejection workflow. It must not promote PostgreSQL shadow positions to execution authority.

R1C remains responsible for reviewed UI presentation, disclosure acceptance, admin review surfaces, and broader fixture-corpus qualification. Prompt V4, Transport V3, new order capabilities, live-order enablement, and any authority changes remain outside both this phase and this approval.
