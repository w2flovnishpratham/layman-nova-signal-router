# NOVA C1 Claude Pine Vertical Slice

## Status and scope

C1 adds an authenticated administrator-only workflow for submitting Pine,
running deterministic analysis, generating a Claude or manual candidate,
appending frozen Transport V2, validating the result, comparing the source and
candidate, and recording a compile-only approval or rejection.

Claude generates a candidate only. Claude does not approve or execute
strategies. Claude output is untrusted. Transport V2 is server-owned. Manual
fallback is permanent and admin-only. Approval means ready for TradingView
compilation. No `StrategyInstance` or credential is created in this phase.

## Reused foundations

- `StrategyCatalog`, `StrategyVersion`, and `StrategySourceArtifact` preserve
  inert private source evidence.
- `PineConversionRequest` stores the workflow state and bounded provenance.
- `StrategyValidationReport` stores deterministic validation findings.
- `StrategyAdminReview` stores immutable compile-review decisions.
- `pine_semantic_preanalyzer` and the version-controlled capability registry
  run before any provider operation.
- Prompt V3.1 and its reviewed SHA remain unchanged and are included in the
  controlled conversion request.
- Transport V2 and its reviewed SHA remain unchanged and are rendered only by
  the backend.
- `pine_validation.validate_source` remains the final static qualification
  gate.

No database migration was needed. Existing free-form workflow status fields,
JSON provenance, source artifact types, and review records support C1 without a
parallel conversion system.

## Admin workflow

1. The administrator opens Strategies, then the existing Admin review queue.
2. Admin Pine Conversion accepts pasted text or a local `.pine`/`.txt` upload.
3. NOVA validates UTF-8 plain text, size, line count, version/declaration,
   filename, and credential-like content.
4. NOVA stores the exact string bytes and their SHA-256 without newline
   normalization.
5. The semantic pre-analyzer persists matched capabilities, registry identity,
   confidence, warnings, review points, and blockers in conversion provenance.
6. L3 backend-required or L4 unsafe/unrepresentable sources become
   `UNSUPPORTED_STRATEGY`; provider call count remains zero.
7. A permitted admin may run API conversion or use the manual package.
8. NOVA validates the strict response, source SHA, capability manifest, and
   strategy-layer boundary.
9. NOVA renders and appends exactly one frozen Transport V2 block.
10. Existing static validation gates the assembled candidate.
11. A passing candidate becomes `READY_FOR_ADMIN_REVIEW`.
12. Approval records exact source, layer, candidate, prompt, registry, options,
    model, schema, and transport hashes as
    `APPROVED_FOR_TRADINGVIEW_COMPILE`.

The candidate version intentionally remains outside the ordinary approved,
installable strategy state. C1 approval does not make it production enabled,
installed, paper-ready, or live-eligible.

## Provider architecture

`pine_conversion_provider.py` now provides:

- `AnthropicClaudePineConversionProvider`
- `FakePineConversionProvider`
- `DisabledPineConversionProvider`

The C1 service calls only `count_tokens`, `convert`, and at most one `repair`.
Routers and React never make provider calls. The Anthropic Python SDK uses the
official Messages token-count endpoint and Pydantic structured-output helper.
SDK automatic retries are disabled; NOVA owns the explicit workflow state and
repair limit. No Claude tools, browsing, MCP, execution, filesystem, or network
tools are enabled.

## Configuration

All values are backend environment settings:

```text
CLAUDE_CONVERSION_ENABLED=false
ANTHROPIC_API_KEY=
CLAUDE_CONVERSION_MODEL=
CLAUDE_CONVERSION_TIMEOUT_SECONDS=120
CLAUDE_CONVERSION_MAX_REPAIRS=1
CLAUDE_CONVERSION_MAX_INPUT_TOKENS=120000
CLAUDE_CONVERSION_MAX_OUTPUT_TOKENS=16000
CLAUDE_CONVERSION_DAILY_ADMIN_LIMIT=10
CLAUDE_CONVERSION_DAILY_GLOBAL_LIMIT=50
```

Conversion is disabled by default. A missing key or model fails closed into the
manual workflow. The API key is never stored, returned, logged, included in a
manual package, or sent as model content.

## Source and prompt security

The exact UTF-8 source is private strategy IP. Full source reads are admin-only
and audited. The source is never logged. Request schemas reject owner, model,
prompt, API key, transport body, credentials, broker settings, execution mode,
quantity, lots, strike, or expiry.

The stable system policy treats comments, strings, labels, identifiers, URLs,
and embedded instructions as untrusted data. It requires behavior preservation
and prohibits optimization, new filters, semantic timing changes, authority,
and transport output.

The dynamic request contains only:

- exact source and source SHA;
- approved deterministic options;
- matched capability IDs and relevant registry policies;
- deterministic analyzer findings;
- canonical Prompt V3.1 material.

No previous conversation, user email, broker state, position state, execution
state, server path, environment variable, database URL, or secret is included.

## Structured response and source binding

The strict `nova.claude-pine-conversion.v1` Pydantic response rejects unknown
fields and requires:

- exact `source_sha256`;
- `CONVERTED`, `MANUAL_REVIEW_REQUIRED`, or `BLOCKED`;
- one non-empty strategy layer;
- canonical signal mapping;
- explicit behavior-change declaration;
- complete matched-capability classification;
- user summary and admin review points.

NOVA rejects prose, missing fields, invalid enums, unknown fields, a source SHA
mismatch, incomplete/unknown capability manifests, unresolved placeholders,
credentials, alert transport in the strategy layer, duplicate/missing
canonical signals, and `logic_changed=true` without manual-review status.

## Cache and cost controls

The exact cache key includes source SHA, Prompt V3.1 version/SHA, registry
version/SHA, backend-selected model, response schema version, approved options
SHA, and Transport V2 version/SHA. Successful validated API candidates owned by
the same admin may be copied into a new private candidate version. Failures and
manual responses are never successful API cache entries.

Local blockers, feature configuration, and key/model configuration run before
token counting. Provider token counting runs before conversion. Input/output
limits, per-admin daily quota, global daily quota, timeout, and one explicit
repair cap fail closed without truncating Pine.

## Transport V2 and deterministic validation

Claude is forbidden from returning the transport. A model-produced transport
marker, credential placeholder, transport function, or direct `alert()` is
rejected at the strategy-layer boundary.

NOVA reads the hash-pinned Transport V2 file, substitutes only safe uppercase
strategy/version constants, appends one block, computes the candidate SHA, and
runs existing static validation. Validation checks Pine structure, canonical
actions, credential and authority fields, exact transport integrity, payload
shape, bar-close/HOLD contract, unsupported actions, and qualification
findings. The UI displays the model layer and server-added transport
separately.

## Restricted repair

At most one provider repair is permitted, and only for response formatting,
Pine syntax/declaration/type defects, built-in defects, unresolved
placeholders, balanced delimiters, or missing canonical boolean wiring.
Transport, entry/exit logic, timeframe, `request.security`, filters, risk, and
behavior redesign are non-repairable. A failed or forbidden repair ends in
`MANUAL_CONVERSION_REQUIRED`; there is no loop.

## Manual fallback

The admin-only manual package contains the exact source/SHA, stable policy,
Prompt V3.1 package, matched policies, analyzer findings, and strict response
instructions. It contains no API key or execution secret.

The administrator pastes only structured JSON back into NOVA. Manual output
passes the same source binding, capability, layer, Transport V2, and final
validation path. Provenance records
`MANUAL_ADMIN_COPY_PASTE`; manual candidates receive no weaker standard.

## API routes

All routes use the existing `require_admin` dependency:

```text
POST /api/admin/pine-conversions
GET  /api/admin/pine-conversions
GET  /api/admin/pine-conversions/{id}
POST /api/admin/pine-conversions/{id}/convert
POST /api/admin/pine-conversions/{id}/manual-package
POST /api/admin/pine-conversions/{id}/manual-response
POST /api/admin/pine-conversions/{id}/approve
POST /api/admin/pine-conversions/{id}/reject
```

Normal authenticated users receive `403`. Source and provider provenance are
not exposed through public routes.

## Frontend

The current Strategies admin workspace now contains:

- submission form and UTF-8 file picker;
- conversion list with analysis/conversion/provider state;
- deterministic analysis and blockers;
- API/manual actions and safe failures;
- exact source, strategy layer, final candidate, and line diff;
- separate server-added Transport V2 panel;
- validation findings, token/latency/cache/repair provenance;
- compile-only approval confirmation and reasoned rejection.

There is no chatbot, free-form system prompt, prompt editor, model selector,
temperature, credential field, tool control, or execution authority.
Refreshing rehydrates the list and first conversion from the backend.

## Tests and acceptance

Backend fake-provider tests cover admin authorization, exact CRLF/SHA
preservation, input rejection, deterministic pre-analysis, zero-call blockers,
disabled/missing-key behavior, structured conversion, transport ownership,
candidate SHA, source/manifest/logic rejection, cache hit/miss, token/quota
controls, one repair, manual parity, approval/rejection binding, mutation
detection, and absence of instances/credentials/jobs.

Frontend tests cover rehydration, submission/upload, analysis, blocked state,
loading/failure, manual fallback, validation/diff/transport/provenance, approval
gating/confirmation, rejection, and absence of model/prompt/secret controls.

The local fake-provider acceptance path exercises:

```text
submit -> analyze -> fake Claude -> append Transport V2 -> validate -> diff -> approve
```

Blocked, provider-disabled, manual, repair, and rejection paths are exercised
without a live Anthropic request or trading action.

## Rollback

1. Set `CLAUDE_CONVERSION_ENABLED=false`; API conversion makes no provider call.
2. Preserve submitted source and reviewed candidate evidence.
3. Keep the safe admin manual fallback available.
4. Revert the C1 implementation commit.
5. No migration downgrade is required.
6. Do not alter prompt/transport history, webhook data, execution data, or
   previously reviewed source artifacts.

## Known limitations and next milestone

Static validation does not prove TradingView compilation or semantic
equivalence. C1 approval only releases the exact candidate for a separate
TradingView compilation milestone. That later milestone may compile and
install an approved candidate, but must independently authorize any
`StrategyInstance`, private credential, HOLD verification, or paper path.
