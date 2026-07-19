# NOVA C2F Secret Safety and Feature Deactivation Fix

## Status

```text
C2_SECURITY_AND_FLAG_FIX_IMPLEMENTED_PENDING_REREVIEW
```

This focused correction addresses blockers B1, B2, and B3 from
`NOVA_C2_TRADINGVIEW_INTEGRATION_REVIEW.md`. It does not redesign C2, enable
Claude, change Pine conversion, modify Prompt V3.1 or Transport V2, grant Live
authority, or alter migration 0016.

## Central untrusted-text sanitizer

`backend/app/services/untrusted_text_sanitizer.py` provides
`sanitize_untrusted_operator_text()`. C2 applies it before persisting:

- TradingView compiler-error summaries;
- manual compile setup notes;
- administrator installation-suspension reasons.

The data flow is:

```text
untrusted input
-> normalize invalid Unicode and unsafe control characters
-> redact secrets
-> normalize safe whitespace
-> truncate to the existing field bound
-> persist
-> audit identifier-only metadata
-> return the already-sanitized value
```

Raw operator text is not persisted for later response-time cleanup.

### Covered secret classes

The sanitizer conservatively covers:

- Authorization, Proxy-Authorization, WWW-Authenticate, X-API-Key, and Api-Key
  header values, including Bearer and Basic forms;
- Cookie and Set-Cookie headers;
- access/refresh tokens, sessions, CSRF values, credentials, passwords,
  secrets, API keys, client/private/webhook secrets, and auth tokens using
  common separators and quoted or unquoted values;
- OTP, TOTP, one-time passwords, and TOTP secrets;
- PostgreSQL/Postgres, MySQL/MariaDB, MongoDB, Redis, and AMQP sensitive URLs;
- PEM/OpenSSH private-key blocks;
- absolute Windows drive, UNC, and common Unix filesystem paths;
- Anthropic-, Dhan-, and private-webhook-labelled keys, tokens, and secrets.

Ordinary diagnostics such as `line 21 column 8` remain readable. Output is
deterministic, idempotent, safe for malformed Unicode, and bounded to 1,000
characters for compile fields. Empty input retains the existing `None`/required
field behavior.

## Feature flag as an operational kill switch

`C2_TRADINGVIEW_INSTALLATION_ENABLED` is now the first authoritative C2
readiness gate.

When false:

- existing installation, compile, credential, and HOLD rows remain stored;
- `paper_eligible` evaluates false;
- installation reads report `FEATURE_DISABLED`;
- engine entries remain historical/visible but are non-selectable;
- the stable engine blocker is `C2_FEATURE_DISABLED`;
- new selection returns a controlled conflict;
- C2 webhook verification and C2 write routes remain unavailable;
- the persisted selection pointer is not silently changed;
- no engine is started or stopped.

When re-enabled, readiness becomes true only if C1 approval, compile evidence,
installation state, owner/instance binding, signal-only mode, active/current
credential, verified HOLD, candidate/source/layer integrity, and suspension
gates all still pass. Legacy non-C2 strategies do not receive the C2 gate.

Unexpected readiness-evidence evaluation errors fail closed through
`C2_READINESS_UNAVAILABLE`.

## Readiness gate decomposition

The server now exposes and evaluates independent gates for:

- feature enabled;
- readiness evaluation available;
- effective C1 approval;
- exact compile success;
- active installation;
- strategy instance presence;
- owner/instance binding;
- active credential;
- current credential binding;
- verified HOLD;
- candidate integrity;
- original-source integrity;
- strategy-layer integrity;
- non-suspension;
- Paper-safe execution mode.

This decomposition preserves the existing aggregate decision while making
fail-closed reasons deterministic and independently testable. Live eligibility
remains hard-coded false.

## Mutation evidence

At both compile recording and installation creation, focused dynamic tests
independently mutate:

1. original source content;
2. original source artifact SHA;
3. strategy-layer content;
4. strategy-layer SHA;
5. candidate content;
6. candidate SHA;
7. validation eligibility;
8. validation candidate binding;
9. review decision;
10. review candidate binding;
11. Prompt V3.1 SHA binding;
12. Transport V2 SHA binding;
13. conversion candidate reference.

Every mutation returns a controlled C1/C2 integrity conflict. Compile-boundary
cases create no compile, installation, instance, or credential row.
Installation-boundary cases preserve the already valid compile evidence but
create no installation, instance, or credential.

## Concurrency behavior

Concurrent same-owner installation requests produce one fully bound
installation, one `TradingViewSetup`, and one `StrategyInstance`; a losing
request receives an idempotent success or controlled `INSTALLATION_EXISTS`
conflict. Different owners succeed independently.

Concurrent credential generation is protected by the installation/instance row
locks and the partial unique active-credential index. A database uniqueness
loser is translated to `CREDENTIAL_EXISTS`, never exposed as a raw
`IntegrityError`. Generation raced with rotation leaves exactly one active
credential; only the plaintext whose SHA-256 matches that active row remains
current.

The repository has no running local PostgreSQL service in this workspace.
Concurrency tests execute on isolated file-backed SQLite here and are written
against the database-independent service/API path. PostgreSQL row locks and
partial indexes remain authoritative and must be included in the focused
independent re-review when a disposable PostgreSQL database is available.

## Paper gate evidence

Focused tests start from a real private-webhook HOLD and a Paper-eligible
installation, then independently invalidate every gate. Each case proves:

- Paper eligibility false;
- a stable blocker;
- engine non-selectability or owner-safe absence;
- selection failure;
- Live eligibility false;
- zero execution job, order intent, and position rows.

The flag-off test also proves historical-row preservation and non-destructive
restoration after re-enable.

## Frontend

The engine picker renders a historically selected C2 strategy as disabled when
the server returns `C2_FEATURE_DISABLED`. It shows `Feature disabled`, provides
no selection action, and does not imply Paper readiness. Managed/SELF controls
and permanent Live unavailability are otherwise unchanged.

The setup package continues to use the relative
`/api/webhooks/private` path. The operator/UI combines it with the configured
backend origin and keeps the credential in the JSON body.

## Migration and rollback

Migration 0016 is unchanged because no schema defect was found.

Operational rollback:

1. Set `C2_TRADINGVIEW_INSTALLATION_ENABLED=false`.
2. Confirm C2 readiness and selection return `C2_FEATURE_DISABLED`.
3. Leave historical C2 rows intact.
4. Roll back application code only if separately required.

Database downgrade to `0015_r1b_persistence` remains appropriate only when C2
compile and installation data may be discarded.

## Rereview boundary

This fix requires one focused independent re-review. A real TradingView HOLD is
still unauthorized. The rereview must confirm sanitizer coverage through HTTP
and persistence, kill-switch behavior, mutation/gate matrices, concurrency on
PostgreSQL when available, full regression accounting, and unchanged
Prompt V3.1/Transport V2 hashes.
