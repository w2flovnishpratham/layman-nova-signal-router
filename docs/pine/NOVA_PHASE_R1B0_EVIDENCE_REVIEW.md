# NOVA Phase R1B-0 — Independent Evidence-Hardening Review

## 1. Executive verdict

**APPROVED_WITH_NON_BLOCKING_FINDINGS**

Every blocking criterion passes: no migration or persistence writer exists, the
legacy adapter import is pure, the secret-taint policy is safe and unwired, the
three-state confidence model is proven against the real analyzer, the five
registry fixture references are genuinely corrected, all 33 fixture
expectations are exhaustive with exact hashes, the expectation updater is
dry-run-safe and never invoked by CI, webhook and execution behavior are
unchanged, and the full regression matrix passes. Two non-blocking findings
are recorded in section 15. This approval authorizes only R1B-1 schema and
immutable semantic-analysis provenance implementation with all persistence
flags disabled by default. It does not authorize merge or deployment.

## 2. Reviewed commits and scope

- Design/starting SHA: `8d31468d6d0f2646a359158171274eb4888100af`
- R1B-0 implementation SHA: `cb7fe52240853541d11d907ec38c11d7f50cdbb4`
- Review branch: `phase-r1b0-evidence-review` (created from a clean
  `phase-r1b0-prerequisite-hardening` worktree at the implementation SHA)
- R1A comparison SHA for webhook equivalence:
  `f2b8ef0e45140dd153b685f97569053e0b5d400b`
- References reviewed: `NOVA_PHASE_R1A_EQUIVALENCE_REVIEW.md`,
  `NOVA_PHASE_R1B_PERSISTENCE_DESIGN.md`,
  `NOVA_PHASE_R1B0_PREREQUISITE_HARDENING.md`

The review was read-only. All probes ran from a session scratchpad against
temporary copies; no repository file other than this report was created or
modified.

## 3. Changed-file classification

51 files changed between the design SHA and the implementation SHA.

| Category | Count | Files |
| --- | --- | --- |
| PURE_DOMAIN | 2 | `backend/app/domain/trading_constants.py` (new), `backend/app/domain/legacy_signal_adapter.py` (import source swap only) |
| SECRET_TAINT | 1 | `backend/app/domain/secret_taint.py` (new) |
| ANALYZER | 1 | `backend/app/services/pine_semantic_preanalyzer.py` |
| REGISTRY | 3 | `backend/app/pine_capabilities/registry.v1.json` (five fixture refs), `backend/app/pine_capabilities/registry.schema.json` (fixture-ID pattern F66→F67), `backend/app/domain/pine_capabilities.py` (same pattern widening in the loader) |
| FIXTURE | 5 | `F15_pending_order_cancellation.pine`, `F28_pine_controlled_entry_quantity.pine`, `F34_position_state_reference.pine`, `F39_process_orders_on_close.pine`, `F67_strategy_order_semantics.pine` (all new) |
| EXPECTED_FIXTURE | 33 | all `*.expected.json` under `backend/app/tests/pine_fixtures/r1a/` (28 rewritten to the exact closed schema, 5 new) |
| TEST | 3 | `test_canonical_signal_adapter.py`, `test_pine_capability_registry.py`, `test_pine_semantic_preanalyzer.py` |
| CONFIG_COMPATIBILITY | 1 | `backend/app/config.py` (constant moved out, re-imported for compatibility) |
| DEVELOPER_TOOL | 1 | `backend/scripts/update_pine_fixture_expectations.py` (new) |
| DOCUMENTATION | 1 | `docs/pine/NOVA_PHASE_R1B0_PREREQUISITE_HARDENING.md` (new) |
| UNEXPECTED | 0 | — |

None of the blocked files changed in either range (design→implementation or
R1A→implementation): `backend/app/db/models.py`, `backend/alembic/`,
`execution_router.py`, `risk_manager.py`, `paper_broker.py`,
`private_webhook_execution.py`, Dhan adapters, prompt files, transport files,
and frontend runtime files are all untouched.

## 4. Pure import result

**PURE_IMPORT_PASS**

- `DEFAULT_EXCHANGE_SEGMENT` in `backend/app/domain/trading_constants.py:3`
  equals exactly `NSE_FNO`.
- `legacy_signal_adapter.py` imports the constant only from
  `app.domain.trading_constants` (line 14 of the module).
- A fresh-process probe imported `app.domain.legacy_signal_adapter` and
  confirmed `app.config`, `pydantic_settings`, and `dotenv` were absent from
  `sys.modules`, and that no application environment variable was read during
  import (only interpreter/site and pydantic plugin bookkeeping variables such
  as `PYTHONUSERBASE` and `PYDANTIC_DISABLE_PLUGINS`, which fire on any
  import). No `.env` file is opened because `Settings` is never constructed;
  the committed test `test_adapter_import_does_not_construct_settings` proves
  construction is impossible during import.
- `app.config` still exports `DEFAULT_EXCHANGE_SEGMENT == "NSE_FNO"`
  (re-imported at `backend/app/config.py:7`); all existing service callers
  (`signal_parser`, `signal_validator`, `execution_router`,
  `private_webhook_service`, `security_id_resolver`, and others) are unchanged
  and continue to resolve the same value.
- BUY_CE, BUY_PE, and EXIT compatibility outputs are field-equivalent: the
  adapter body is byte-identical except for the import line, and the
  compatibility tests in `test_canonical_signal_adapter.py` pass unchanged.

## 5. Secret-taint review

**SECRET_TAINT_PASS_WITH_FINDINGS** (findings are non-blocking; section 15)

`backend/app/domain/secret_taint.py` imports only `json`, `math`, `re`,
`secrets`, `enum`, `types`, and `typing`. A fresh-process probe confirmed no
`app.config`, SQLAlchemy, FastAPI, broker, persistence, logging-config, or
environment-loader module is loaded by importing it.

Credential-shaped detection (`is_credential_shaped`, lines 38–48):

| Input | Detected |
| --- | --- |
| `nwk_abcdefghijklmnopqrstuvwxyz` | yes |
| `NWK_ABCDEFGHIJKLMNOPQRSTUVWXYZ` | yes (case-insensitive) |
| `NwK_MixedCase123` | yes |
| `text-before-nwk_token` | yes (hyphen is a boundary) |
| `ordinary-text` | no |
| `nwk_` | no (bare prefix, no token body) |
| `nwk-short` | no (dash, not underscore) |
| exact known request secret | yes (constant-time `secrets.compare_digest`) |
| different value from known secret | no |

Matched secrets can never appear in output or exception text: `redact` returns
the constant `[REDACTED]`, `validate_safe_detail` returns only members of a
closed four-code set, `project_safe_signal_id` returns the constant
`credential-shaped-id`, and no helper raises with input embedded in the
message. A probe confirmed a `nwk_` secret placed in every input position
never survives into any return value.

Safe metadata (`build_safe_metadata`, lines 79–119): only `strategy_version`
(≤40 chars, closed pattern), `timeframe` (≤12 chars, closed pattern), and
finite positive non-bool `reference_price` survive. A probe passed a poisoned
mapping containing `comment`, `signal_id`, `credential`, `user_id`,
`instance_id`, `broker`, `lots`, `qty`, `quantity`, `strike`, `expiry`,
`security_id`, `sl`, `tp`, `risk`, `order_type`, `execution_mode`, and a
nested authority object: none survived, the input was not mutated, the output
is a `MappingProxyType` that raises `TypeError` on write, the canonical JSON
encoding is bounded to 2 KiB (line 116–118, and by construction cannot exceed
it), NaN/Infinity/zero/negative/bool reference prices are omitted, and a
recursive input mapping neither crashed the builder nor leaked nested data.

Signal-ID projection (`project_safe_signal_id`, lines 68–76): normal IDs pass
through, credential-shaped IDs become `credential-shaped-id`, and empty,
oversized, non-string, or control-bearing IDs become `invalid-signal-id`. A
repo-wide search confirmed `secret_taint` is imported only by its own test
file — it is not wired into webhook acceptance or legacy persistence.

Safe details (`validate_safe_detail`, lines 122–136): output is one of exactly
four closed codes, each ≤160 characters, lowercase, and free of newlines,
controls, and credential-shaped text; any other input (including raw
exceptions and non-strings) collapses to the constant
`canonical-evidence-unavailable`, which itself satisfies the closed pattern.

## 6. Confidence-model review

**CONFIDENCE_MODEL_PASS**

The aggregation algorithm at
`backend/app/services/pine_semantic_preanalyzer.py:406-425` computes severity
(`most_restrictive` over matched registry entries) and confidence
(indeterminate > partial > high) independently, then applies exactly two
adjustments: indeterminate forces L4, and partial clamps L0/L1 up to L2.

All ten required proofs ran against the real analyzer in an isolated probe:

1. High-confidence L0 base plus partial unsafe evidence → L2, not L0. PASS
2. High-confidence L1 base plus partial unsafe evidence → L2, not L1. PASS
3. Partial L3 (computed entry limit) remains L3. PASS
4. Partial L4 (dynamic request context) remains L4. PASS
5. Indeterminate plus proven L4 lookahead keeps L4 and retains the proven
   blocker codes (`BLK_FUTURE_LEAK`, `BLK_MALFORMED`). PASS
6. Bare indeterminate (empty source, protected-source marker) fails closed at
   L4 with `ANALYSIS_INDETERMINATE`. PASS
7. High-confidence L4 exists (clean lookahead source), proving confidence and
   severity are independent fields. PASS
8. Reordering constructs across the source produces identical results. PASS
9. Duplicating constructs with no distinct semantics produces identical
   results (a second same-side entry legitimately adds
   `SAME_SIDE_STATE_NO_OP`, which is a real capability, not order
   sensitivity). PASS
10. API calls inside comments and string literals produce no executable
    matches and leave a plain indicator at high-confidence L0. PASS

All five committed PARTIAL_MATCH scenarios were reviewed in
`test_pine_semantic_preanalyzer.py` and are genuinely partial: computed entry
stop/limit (construct proven, level unresolvable), dynamic request context
(call proven, symbol/timeframe unresolvable), computed exit quantity,
partially constructed custom JSON (concatenated fragments), and unresolved
fill-dependent indexing (`strategy.opentrades` with a non-literal index). None
is deterministic, and none is indeterminate, because in each case the
construct itself is proven. Repeated analysis of the same fixture is
deterministic.

## 7. Registry provenance

**REGISTRY_PROVENANCE_PASS**

Independently recomputed with `canonical_registry_json` (capabilities sorted
by `capability_id`, sorted keys, compact separators):

- Old registry (at `8d31468`):
  `c1a255f3d7a2bce84a8c95f38dac52d2fcf35ee1474e3ecf28289b0708dfe00f` — exact.
- New registry (at `cb7fe52`):
  `a237f33a6795d06e371c0e4719d7e581056893b30b1e60fd3768a511aa4b0594` — exact,
  and the loader's own `sha256` agrees.

A field-level diff of all 38 entries proved `registry_id`,
`registry_version`, entry count (38), every capability level, temporal class,
conversion policy, blocker code, disclosure set, backend-capability string,
and priority are unchanged. The only changed field anywhere is `fixtures`, on
exactly these five entries:

| Capability | Old | New |
| --- | --- | --- |
| `PROCESS_ORDERS_ON_CLOSE` | F05 | F39 |
| `STRATEGY_ORDER_SEMANTICS` | F13 | F67 |
| `PINE_CONTROLLED_ENTRY_QUANTITY` | F31 | F28 |
| `POSITION_STATE_REFERENCE` | F36 | F34 |
| `PENDING_ORDER_CANCELLATION` | F13 | F15 |

Canonicalization determinism: five random shuffles of entry order and
per-entry key order all produced the identical canonical hash.

Each new fixture's Pine source and analyzer output were inspected directly:
F39 declares `process_orders_on_close=true` and matches
`PROCESS_ORDERS_ON_CLOSE`; F67 calls `strategy.order` and matches
`STRATEGY_ORDER_SEMANTICS`; F28 passes `qty=2` to `strategy.entry` and
matches `PINE_CONTROLLED_ENTRY_QUANTITY`; F34 reads
`strategy.position_size` outside an exit-while-flat guard and matches
`POSITION_STATE_REFERENCE`; F15 calls `strategy.cancel_all()` and matches
`PENDING_ORDER_CANCELLATION`. The committed test
`test_every_registry_fixture_resolves_and_proves_the_cited_capability`
verifies every fixture reference of all 38 entries resolves to an existing
fixture whose analysis contains the citing capability; it passes.

## 8. Exhaustive fixture evidence

**EXHAUSTIVE_FIXTURE_PASS**

Exactly 33 `.pine` files and 33 `.expected.json` files exist, with unique
fixture IDs matching their filenames. An independent probe verified, for all
33 pairs: `source_sha256` equals the SHA-256 of the exact committed UTF-8
bytes (no newline-translation drift; files are LF), `registry_sha256` equals
`a237f33…0594`, `analyzer_version` is `nova.pine-semantic-preanalyzer.v1`,
`registry_id`/`registry_version` are `nova.pine-capabilities`/`v1`, and every
expected JSON parses.

The committed test compares the full closed 13-key document exactly
(`_assert_expected_matches` in `test_pine_semantic_preanalyzer.py`), so
matched capabilities, effective level, temporal classes, blockers,
disclosures, admin review points, and confidence are all exact; every array
field is asserted to equal `sorted(set(values))` (deduplicated,
lexicographic); unknown keys and missing keys are rejected by
`set(expected) == EXPECTED_KEYS`.

All 16 required failure modes are proven to fail: nine by the committed
parametrized mutation test (wrong source SHA, wrong registry SHA,
extra/missing capability, extra temporal class, missing blocker, extra
disclosure, confidence mismatch, admin-point mismatch), three by dedicated
committed tests (filename/ID mismatch, duplicate fixture ID, unknown/missing
key), and the remaining symmetric variants (missing temporal class, extra
blocker, missing disclosure — plus effective-level, analyzer-version, and
registry-ID mismatches) by an isolated review probe that ran each mutation
through the committed test logic; every one raised `AssertionError`.

The fixture test globs `*.pine`, asserts the count is exactly 33, and
analyzes every file, so no fixture can be present but unexecuted; none is.

## 9. Updater safety

**UPDATER_SAFE** (one non-blocking finding; section 15)

`backend/scripts/update_pine_fixture_expectations.py`:

- Dry-run is the default; writing requires explicit `--write` (lines 39–44,
  69–71).
- No network, AI, TradingView, Dhan, or database import exists in the script
  or its import chain (argparse/difflib/json/pathlib plus the analyzer).
- Complete unified diffs are printed before any write, plus a deterministic
  one-line summary.
- Source and registry hashes are recomputed on every run via
  `analyze_source`.
- It writes only `*.expected.json` paths derived from `.pine` filenames; an
  isolated probe on a scratch copy confirmed `.pine` files and the registry
  are byte-identical after `--write`.
- On the committed tree the dry-run reports
  `dry-run: 0 expectation file(s) changed; 33 fixture(s) reviewed`.
- No CI configuration exists in the repository (`.github/` absent) and no
  test, script, or config references the updater, so it cannot run
  automatically.

Finding (non-blocking): the updater exits 0 even when expectations diverge or
an expected file is malformed — it is loud (full diff) but never nonzero. See
section 15.

## 10. Webhook equivalence

**WEBHOOK_EQUIVALENT**

`git diff --name-only f2b8ef0..cb7fe52` contains no webhook, schema,
credential, replay, execution, broker, prompt, or transport runtime file. The
accepted actions, request schema, credential parsing and hashing, replay
fingerprint and provider, duplicate and conflicting-duplicate behavior,
timestamp validation, owner isolation, lifecycle validation, HOLD behavior,
compatibility `NormalizedSignal` output, execution job payload, and response
codes are therefore bit-identical to the approved R1A implementation.

`test_private_webhook.py` and `test_private_webhook_execution.py` were run
twice — with `CANONICAL_SIGNAL_SHADOW=false` and `=true`: 81 passed both
times. Shadow-mismatch failure isolation is covered by the unchanged
`test_canonical_signal_shadow.py`, which passes.

## 11. Failure isolation

All forced-failure probes ran in isolated processes or scratch copies:

- Secret-taint helpers given non-string, `None`, bytes, and numeric inputs
  return safe constants or omit values; invalid entries inside the
  known-secrets iterable are skipped without breaking detection of valid
  ones.
- Oversized, NaN, Infinity, recursive, and nested-authority metadata are all
  omitted; the 2 KiB encoded bound holds.
- Missing, malformed, and schema-tampered registries make `analyze_source`
  fail closed: L4, `ANALYSIS_INDETERMINATE`, `BLK_REGISTRY_UNAVAILABLE`,
  registry identity `UNAVAILABLE` — no exception escapes.
- The loaded registry is a frozen dataclass; mutation attempts raise.
- Hostile sources (NUL bytes, 5000 unbalanced parens, 100 kB of noise,
  emoji runs, truncated calls) never raise and always fail closed at L4.
- The updater given malformed expected JSON shows a diff and, in dry-run,
  writes nothing.

None of these paths can affect webhook startup or acceptance (the taint
module is unwired; the analyzer is not on the webhook path), create an
execution job, call a broker, leak a credential, mutate the registry, or
rewrite fixtures without `--write`.

## 12. Scope-creep result

**SCOPE_CLEAN**

The complete design→implementation diff was searched for `session.add`,
`session.execute`, `commit(`, `flush(`, `INSERT`, `UPDATE`, `DELETE`,
`alembic`, `op.create_table`, `create_task`, `broker`, `dhan`,
`place_order`, `route_signal`, `send_order`. Exactly two matches, both inert
text: the F50 expected manifest's admin-review string "Confirm source does
not require atomic broker reversal." (registry-authored prose carried into a
fixture expectation) and the word "broker" in the R1B-0 documentation's list
of *excluded* metadata fields. No SQLAlchemy model, migration, database
write, persistence writer, background worker, public endpoint, webhook or
replay field, execution route, PaperBroker/Dhan behavior, prompt, transport,
AI call, or TradingView call was added.

## 13. Test results

All executed at `cb7fe52` on the review branch:

| Suite | Result |
| --- | --- |
| Focused R1B-0 (adapter, shadow, registry, preanalyzer) | 108 passed |
| Webhook + execution, `CANONICAL_SIGNAL_SHADOW=false` | 81 passed |
| Webhook + execution, `CANONICAL_SIGNAL_SHADOW=true` | 81 passed |
| Pine regressions (personal, conversion, prompt v3, transport v2, v3.1 binding, TradingView setup) | 64 passed |
| Migration metadata | 2 passed |
| Full backend suite (`app/tests -q`) | 867 passed |
| `alembic heads` | `0014_verify_select (head)` — unchanged |
| Frontend `tsc -b --noEmit` | clean |
| Frontend vitest | 20 passed (3 files) |
| Frontend `vite build` | success (pre-existing chunk-size warning only) |
| `git diff --check 8d31468..cb7fe52` | clean |
| `py_compile` of all 10 changed Python files | clean |
| JSON parse of all 35 changed JSON files | all valid |

## 14. Blocking findings

None.

## 15. Non-blocking findings

1. **Updater always exits 0.**
   - File: `backend/scripts/update_pine_fixture_expectations.py`
   - Function: `main`, lines 37–72
   - Observed: `main()` returns 0 unconditionally — including when
     expectations diverge, an expected file is malformed, or the registry is
     unavailable (in which case `analyze_source` fails closed and the
     proposed documents would carry `UNAVAILABLE` identity, shown fully in
     the diff).
   - Risk: low. The tool is developer-only, dry-run by default, never invoked
     by CI (no CI config exists), prints complete diffs, and the exhaustive
     fixture tests independently fail on any drift. A scripted caller could
     however misread exit 0 as "no pending changes".
   - Verdict: non-blocking. Suggest returning nonzero when
     `changes` is non-empty in dry-run or when any proposed document carries
     `registry_id == "UNAVAILABLE"`, in a future developer-tool phase.

2. **Known-secret detection is exact-match only.**
   - File: `backend/app/domain/secret_taint.py`
   - Function: `is_credential_shaped`, lines 38–48
   - Observed: request-local known secrets are compared with
     `secrets.compare_digest` full-string equality; a known secret embedded
     as a substring of a longer string is not detected unless it is also
     `nwk_`-shaped. Real NOVA request secrets are `nwk_`-prefixed and are
     caught anywhere in text by the pattern, so the practical exposure is a
     hypothetical non-`nwk_` secret embedded in composite text.
   - Risk: low; the pattern path covers the production secret format, and the
     helpers are not yet wired to any runtime flow.
   - Verdict: non-blocking. Consider substring containment for known secrets
     when the helpers are wired in R1B-1.

## 16. R1B-1 authorization decision

R1B-0 evidence hardening is **approved**. R1B-1 may proceed with exactly the
previously designed scope: the additive `0015_r1b_persistence` migration,
four empty additive tables, ORM models and constraints, the immutable
semantic-analysis provenance writer, and all persistence flags default false
— with no canonical intent, outcome, or rejection writes. This review
authorizes nothing else: no merge, no deployment, no webhook change, no
execution change, no prompt/transport change, and no AI conversion.
