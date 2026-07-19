# NOVA C2 TradingView Integration

## Scope and safety boundary

C2 connects an approved C1 Pine candidate to a manual TradingView installation,
an owner-bound NOVA strategy instance, a private webhook credential, one real
TradingView `HOLD`, and server-derived Paper eligibility.

C2 does not automate TradingView. C2 does not grant Live eligibility. C2 does
not place orders. C2 does not expose Claude to normal users. `HOLD` verifies
routing only. Paper eligibility does not start the engine.

The feature is controlled by
`C2_TRADINGVIEW_INSTALLATION_ENABLED`, which defaults to `false`. No C2 write
operation is available while the flag is disabled. The same flag is an
operational kill switch inside authoritative readiness: existing C2 rows,
compile evidence, credentials, and HOLD evidence remain stored, but Paper
eligibility and engine selection immediately fail with
`C2_FEATURE_DISABLED`. Re-enabling restores readiness only if every other gate
still passes. Existing C1 reads and the legacy private-webhook lifecycle remain
independent.

## Reused components

C2 deliberately reuses these existing boundaries:

- the C1 strategy catalog, immutable source artifact, strategy layer,
  conversion request, candidate version, validation report, provenance, and
  admin approval;
- `StrategyInstance` for owner binding and the existing owner-only engine
  picker;
- `TradingViewSetup` for installation state;
- `StrategyInstanceWebhookCredential` for cryptographically random,
  hash-only, revocable credentials;
- `POST /api/webhooks/private` for authenticated TradingView ingress;
- `StrategySignal` for the no-op `HOLD` evidence;
- the existing audit log and authenticated admin/owner route dependencies.

It does not add a second webhook, credential system, engine selector, or
execution path.

## Lifecycle

1. An admin opens an approved C1 conversion. The server revalidates the current
   source hash, strategy-layer hash, exact candidate content hash, validation
   report, approved review, usage provenance, Prompt V3.1 hash, and Transport V2
   hash.
2. The admin downloads or copies the exact approved Pine and compiles it
   manually in TradingView.
3. The admin records exactly one terminal compile result. Success pins all
   relevant hashes. Failure stores only a bounded, sanitized compiler summary
   and blocks that candidate; retry requires a new candidate.
4. After a successful compile, the admin creates either a `MANAGED` or `SELF`
   installation for a real database user. The server creates an owner-bound,
   inert `StrategyInstance` in `signal_only` mode.
5. A credential is generated. The plaintext is returned once only; NOVA stores
   its hash. Rotation invalidates the old credential and clears prior `HOLD`
   evidence and Paper eligibility. Revocation does the same without issuing a
   replacement.
6. TradingView sends one real `HOLD` to `/api/webhooks/private`, with the
   credential in the JSON body. The request is accepted only when the
   credential, owner, instance, installation, selected candidate, compile
   evidence, C1 approval, and safe execution mode still match.
7. The existing webhook persists the no-op signal and C2 binds that committed
   evidence to the installation. It creates no execution job, order intent,
   order, position, or broker call.
8. Paper eligibility becomes true only while every server-derived gate remains
   true. The owner may then select the strategy in the existing engine picker.
   Selection only changes the pointer; it does not start the engine.

Duplicate delivery of the same valid `HOLD` is idempotent. A non-`HOLD` action
on a C2 instance fails closed. The local synthetic webhook-test route cannot
satisfy C2 verification.

## Installation modes

### SELF

The owner can view the exact approved Pine, webhook path, credential controls,
and `HOLD` alert JSON template. The generated setup package is manual guidance
only. It contains no execution fields and tells the user to keep the credential
in the JSON body, not the URL.

### MANAGED

An admin performs the manual TradingView setup. The owner can view lifecycle and
Paper-readiness state, but cannot retrieve Pine, alert-package details, or
generate, rotate, or revoke the credential. Credential plaintext remains
display-once to the admin who generated or rotated it.

## Exact binding and invalidation

Every compile record and installation pins:

- conversion request;
- approved candidate version and current candidate SHA-256;
- original source SHA-256;
- strategy-layer SHA-256;
- Prompt V3.1 SHA-256;
- Transport V2 SHA-256;
- owner and strategy instance;
- installation mode;
- current credential and, after verification, the exact credential that sent
  the accepted `HOLD`.

The service rechecks C1 approval instead of trusting copied status fields. A
changed candidate, stale approval, mismatched source, mismatched strategy layer,
invalid compile evidence, owner mismatch, instance mismatch, unsafe execution
mode, rotated or revoked credential, or suspended installation denies webhook
verification and Paper eligibility.

## Credential handling

Credentials use the existing secure random generator and hash-only database
storage. Plaintext is returned in one API response and held only in transient UI
state. It is not written to browser local storage or session storage, returned
by later reads, included in URLs, or placed in audit metadata.

Rotation and revocation run under row locks. The active-credential uniqueness
constraint remains authoritative under concurrency. Audit records contain only
identifiers and short hash references.

Compiler errors, compile setup notes, and administrator suspension reasons use
one backend untrusted-operator-text sanitizer before persistence. It removes
unsafe control characters, bounds output to the field limit, and
conservatively redacts authentication headers, cookies/sessions, token and
credential assignments, OTP/TOTP, sensitive database/service URLs, private-key
blocks, absolute filesystem paths, and NOVA broker/provider/webhook secret
labels. Persisted values are already safe; response serialization is not the
security boundary.

## API surface

Owner routes:

- `GET /api/strategy-installations/config`
- `GET /api/strategies/my-installations`
- `GET /api/strategies/my-installations/{installation_id}`
- `POST /api/strategies/my-installations/{installation_id}/self-credential`
- `POST /api/strategies/my-installations/{installation_id}/credential/rotate`
- `POST /api/strategies/my-installations/{installation_id}/credential/revoke`

Admin routes:

- approved conversion status and exact-Pine download routes under
  `/api/admin/c2/conversions`;
- terminal compile success/failure routes;
- installation list, create, and detail routes;
- credential generate, rotate, and revoke routes;
- installation suspension route.

Webhook:

- `POST /api/webhooks/private`

The setup package intentionally emits this relative webhook path. The
operator/UI must combine it with the configured NOVA backend origin; a
credential must remain in the JSON body and must never be placed in that URL.

All resource reads are owner-scoped or admin-scoped. Request schemas reject
unknown fields, including attempts to smuggle execution or order controls.

## Database changes

Alembic revision `0016_c2_tv_installation`:

- creates `tradingview_compile_evidence`;
- adds nullable C2 binding, hash, credential, webhook-evidence, Paper-eligibility,
  revocation, and suspension columns to `tradingview_setups`;
- adds approved-candidate hash and installation mode to
  `strategy_instances`;
- adds foreign keys, uniqueness constraints, and lookup indexes required by
  the lifecycle.

The migration is additive. Legacy TradingView rows retain null C2 markers and
continue through the legacy lifecycle. The migration does not change R1B
decision tables, broker tables, orders, positions, risk controls, or strategy
execution semantics.

## UI

The admin C1 conversion detail shows C2 only when the feature flag is enabled
and the selected candidate is approved. It provides exact-Pine review/download,
terminal compile recording, owner and mode selection, installation creation,
credential lifecycle controls, suspension, and explicit Paper/Live state.

The owner Personal Strategies page adds a My Strategies tab only while the flag
is enabled. Refresh and navigation rehydrate lifecycle state from the server.
One-time credentials appear in a dismiss-only panel and disappear permanently
from the client after dismissal or reload.

## Verification coverage

The C2 backend integration suite covers:

- feature and admin authorization;
- exact C1 binding and compile evidence;
- terminal sanitized compile failure;
- `MANAGED` and `SELF` owner isolation;
- inert instance creation and rejected execution-field smuggling;
- display-once, hash-only credential handling and secret-free audit logs;
- a real private-webhook `HOLD`, duplicate delivery, and zero execution
  artifacts;
- rejection of synthetic, non-`HOLD`, foreign, revoked, rotated, suspended, and
  candidate-mutated cases;
- Paper eligibility, owner-only engine selection, and no implicit engine start.

The focused C2F suite additionally covers route/database/log secret-marker
removal, deterministic/idempotent sanitization, flag-off deactivation and
flag-on restoration, 13 independent C1 mutation cases at both compile and
installation boundaries, concurrent installation and credential requests, and
independent fail-closed evidence for every Paper-readiness gate. The database
constraints and row-lock paths remain PostgreSQL-authoritative; the same tests
also run on the isolated SQLite path used by the repository.

Frontend coverage verifies flag gating, compile controls, owner/mode selection,
installation creation, one-time credential behavior, state rehydration, managed
secret hiding, Paper readiness, and permanent Live unavailability.

Regression verification includes C1 conversion, private webhook, TradingView
setup, strategy-instance, readiness/selection, auth, database migration, Python
compilation/lint, TypeScript compilation, frontend unit tests, and production
frontend build.

## Manual local smoke procedure

Use only a local database and keep Live/broker services disabled.

1. Set `C2_TRADINGVIEW_INSTALLATION_ENABLED=true`, start the backend and
   frontend, and sign in as an admin.
2. Open an approved C1 conversion and confirm the Pine displayed/downloaded is
   byte-for-byte the approved candidate.
3. Compile that Pine manually in TradingView, record success, and create a
   `SELF` installation for a test owner.
4. Sign in as the owner, generate the credential, copy the display-once alert
   JSON, dismiss it, refresh, and confirm the credential cannot be recovered.
5. Configure a TradingView alert with `/api/webhooks/private` and the JSON-body
   credential. Send one real `HOLD`.
6. Refresh My Strategies and confirm `HOLD` verified and Paper ready while Live
   remains unavailable.
7. Confirm execution jobs, order intents, orders, positions, and broker calls
   remain zero; select the strategy and confirm the engine remains stopped.
8. Rotate the credential and confirm readiness clears, the old credential
   fails, and a new real `HOLD` is required.
9. Suspend the installation and confirm readiness clears and webhook
   verification fails closed.

This manual TradingView smoke is intentionally performed after independent
review; automated browser control is outside C2.

## Rollback

Application rollback:

1. Set `C2_TRADINGVIEW_INSTALLATION_ENABLED=false`.
2. Confirm existing C2 entries become non-selectable with
   `C2_FEATURE_DISABLED`; historical rows are retained.
3. Roll back the application to the prior reviewed release if required.
4. Leave the additive migration in place if any C2 records must be retained.

Database rollback, only when C2 data can be discarded:

1. Back up the database.
2. Confirm no required C2 compile evidence or installation state remains.
3. Run `alembic downgrade 0015_r1b_persistence`.

Downgrade removes C2-only columns and compile evidence. It does not undo C1,
R1B, broker, order, position, risk, or execution data. Disabling the flag is the
preferred operational rollback.

## Known limitations and next boundary

- TradingView installation and compilation are manual and externally asserted
  by an admin.
- NOVA cannot prove TradingView editor contents after compile beyond the pinned
  workflow and the authenticated real `HOLD`.
- C2 grants Paper selection eligibility only. It does not qualify a strategy
  for Live or start the engine.
- Managed TradingView operations remain an admin process.
- Normal users still have no Claude API surface.

Any future Paper execution or Live-eligibility phase requires separate
authorization, design, migration review, risk review, and execution-boundary
tests.
