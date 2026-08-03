# Phase 4A registry and call-flow inventory

## Existing schema

- `strategy_catalog`: shared registry for NOVA-owned and owner-scoped personal strategies. Personal rows already support `owner_user_id`, `visibility=private`, and per-owner codes.
- `strategy_versions`: immutable-after-approval version records. Instances already enforce that a selected version belongs to their selected catalog strategy through a composite foreign key.
- `strategy_source_artifacts`: inert text evidence with SHA-256 hashes. It is the repository's existing private source-storage abstraction; there is no object-store abstraction.
- `strategy_validation_reports`: structured JSON findings linked to a version.
- `strategy_admin_reviews`: append-only human decision history linked to a version and reviewer.
- `strategy_instances`: owner-scoped configured strategies linked structurally to catalog and version.

Phase 4A extends these tables. It does not create another strategy registry.

## Existing authorization and infrastructure

- Owner identity comes from the signed session through `get_current_user`.
- Admin access uses `require_admin`; there is no separate reviewer role.
- Foreign owner resources conventionally return 404 to resist enumeration.
- Audit events use the existing database audit log and must contain identifiers and hashes only, never Pine source.
- Pagination uses bounded `limit` and non-negative `offset` query parameters.
- The durable strategy job worker is execution-specific and carries normalized trading signals. It is not a safe or clean fit for static source validation.
- There is no generic upload component, file-storage service, source-encryption facility, or validation worker.

## Existing Pine and alert contract inputs

- `nova_indicator_v2.pine` is Pine v6 and demonstrates bar-close alert discipline, NIFTY intraday behavior, CE/PE entry intent, and EXIT behavior, but its legacy payload includes fields now owned by the server.
- `tradingview_entry_only_server_exit.pine` is Pine v5 and demonstrates the older entry-only payload.
- Phase 3 private webhooks accept only `BUY_CE`, `BUY_PE`, `EXIT`, and `HOLD` and derive owner, instance, mode, lots, quantity, contract, strike, expiry, and broker fields server-side.
- The Personal Strategies UI generates the credential-bearing TradingView JSON outside Pine source. The public webhook URL itself contains no credential.

## Phase 4A call flow

```text
authenticated owner
  -> create private catalog row or immutable version
  -> canonicalize bounded untrusted text
  -> store inert source artifact + source hash
  -> run deterministic static validator (never execute source)
  -> persist/reuse immutable validation report
  -> submit passing exact report
  -> admin starts review under row lock
  -> admin records one append-only decision and status transition
  -> owner may download approved canonical source
  -> owner may link the approved version to an owner-matched paper instance
```

Static validation is synchronous because it is bounded text-only work and the existing worker is execution-specific. A durable validation worker should be added only if measured validation latency or future compiler integration requires it.

## Required schema gaps

- Canonical source hash and Pine contract version on the immutable version identity.
- One canonical Pine artifact per version plus a safe original filename.
- Unique validation identity and report metadata/counts.
- Exact validation-report/source-hash references and previous/new states on review history.

No hosted runtime, Pine interpreter, compiler, backtester, marketplace, or live-execution field is required.
