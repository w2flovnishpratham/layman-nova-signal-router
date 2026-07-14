# Phase 4B inventory and architecture

## Reused foundation

- Phase 4A `strategy_catalog`, immutable `strategy_versions`, private `strategy_source_artifacts`, deterministic `strategy_validation_reports`, admin review, audit log, and owner-scoped authorization remain authoritative.
- Source size and credential checks are reused and extended before every provider call.
- The money-moving strategy execution worker is intentionally not reused. Its retry semantics and payloads are specific to broker execution.
- No AI client, provider abstraction, cost ledger, generic external-request queue, diff component, or consent pattern existed.

## Phase 4B flow

```text
owner version -> explicit per-request consent -> durable conversion row
  -> dedicated SKIP LOCKED claimant -> source/hash/secret recheck
  -> configured provider -> strict structured output validation
  -> immutable private candidate -> Phase 4A deterministic validator
  -> inert owner diff -> explicit accept or reject -> normal admin review
```

Manual packages are generated on demand from the same owner-scoped artifact and make no provider call. AI is disabled by default. Enabling it requires one explicit provider, HTTPS URL, API credential, model, timeout, retry policy, and the dedicated durable worker.

## Privacy and identity

Raw input and candidate source exist only in the existing artifact table and owner-authorized detail responses. Conversion rows contain hashes, lineage, consent, provider/model/prompt identity, bounded summaries, usage counts, and safe error codes. List/admin-usage endpoints contain no source.

The conversion identity hashes owner, immutable input version/hash, contract, prompt version, provider, model, and canonical options. Pending and successful identities are reused. Explicit retries create append-only attempts. Provider/model/prompt/option changes create a new identity.

## Deliberate limits

The sole production adapter targets a configured OpenAI-compatible HTTPS JSON endpoint through `httpx`; NOVA never silently changes provider or model. Cost is stored only when an adapter can report it; token usage is provider-reported and non-authoritative. The UI uses an inert side-by-side source view rather than introducing a diff dependency.
