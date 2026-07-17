# NOVA Phase R0 — Pine Capability Research Codebase Verification

**Audit date:** 2026-07-18  
**Required baseline:** `phase-4h-master-prompt-v3` at `6bad201e570e420d3442f9ce6dc44d0fb2780dd6`  
**Audit branch:** `phase-r0-pine-capability-audit`  
**Research input:** `NOVA_Pine_Strategy_Semantics_Research_Report.md` from the supplied research archive  
**Scope:** read-only runtime audit plus this documentation artifact; no application, prompt, transport, or migration changes

## 1. Executive verdict

**VERDICT: READY_FOR_STAGED_PHASE_R1, NOT READY_FOR_PROMPT_V4 OR A BROADER CONVERSION CLAIM.**

The research's central recommendation is compatible with the real codebase. NOVA can introduce an internal target-state model without changing the public `BUY_CE / BUY_PE / EXIT / HOLD` wire contract or current order behavior:

| Existing wire action | Safe internal event | Safe desired state | Current behavior preserved |
|---|---|---|---|
| `BUY_CE` | `STRATEGY_SIGNAL` | `BULLISH` | Yes: resolves a CE contract, enters from flat, no-ops when already CE, and exit-first reverses from PE. |
| `BUY_PE` | `STRATEGY_SIGNAL` | `BEARISH` | Yes: symmetric PE behavior. |
| `EXIT` | `STRATEGY_SIGNAL` | `FLAT` | Yes: exits the current tracked position and no-ops while flat. |
| `HOLD` | `CONNECTIVITY_TEST` | `NONE` | Yes: persists audit evidence and never creates an execution job. |

The recommended adapter is a **new pure domain normalization service (Option D), invoked by the webhook service after credential/lifecycle resolution and before persistence**. This combines Option D's testability and rollback isolation with Option B's safe position in the existing flow. It must not be placed in the request schema, because authentication, replay identity, and raw-wire audit must remain separable from internal semantics; it must not be deferred to the execution router, because `HOLD` and durable rejections must be normalized before job creation.

The highest-risk current limitation is not the four-action wire contract. It is the split authority model: per-user JSON position files remain execution-read authority, while PostgreSQL position rows and events are explicitly shadow-only (`backend/app/db/models.py:1051-1063`). Phase R1 must not silently promote the shadow tables or redesign execution ownership.

The current deterministic Pine validator is a useful transport and basic safety validator, but it is **not** the semantic pre-analyzer described by the research. It recognizes version/declaration/action/authority basics, literal pyramiding, generic `strategy.order` risk, multiple `request.security` calls, and `lookahead_on`; it does not reliably classify pending orders, exits, partial quantities, fill-dependent behavior, calc modes, dynamic requests, synthetic charts, or protected/non-compiling Pine.

The repository does not contain the F01-F66 semantic fixture corpus. It has strong webhook/execution tests, two representative converted Pine fixtures, stable validator-rule tests, and a 25-case prompt-policy record, but most research fixtures remain missing or policy-only.

### Architecture freeze

1. Keep the public webhook and Frozen Transport V1/V2 actions unchanged.
2. Normalize to target state inside NOVA, not with TradingView strategy placeholders.
3. Keep Pine as canonical booleans plus `alert()` transport; do not depend on `{{strategy.market_position}}` or `{{strategy.prev_market_position}}`.
4. Ship R1 in three stages: R1A domain/registry/shadow comparison; R1B additive persistence/rejections; R1C UI and corpus expansion.
5. Do not create Prompt V4 until the registry and pre-analyzer govern capability decisions.
6. Keep pending orders, partial exits, pyramiding, multiple positions/legs, Pine-owned sizing, and Pine-owned risk outside R1.

### Phase 4G to Phase 4H history check

`git diff --name-status dbf7be6ae5e0a05deff3968624f8786ec6cdcb1d..6bad201e570e420d3442f9ce6dc44d0fb2780dd6` shows changes only in Pine configuration, prompts/transports, Pine conversion/validation/setup services, Pine tests/qualification records, and imported-Pine UI/API files. The private webhook, execution router, risk manager, PaperBroker, position state, and persistence models were unchanged. Therefore the verified trading behavior below is inherited from the Phase 4G baseline; Phase 4H adds conversion-package and qualification controls, not a new execution model.

## 2. Verified current webhook contract

### 2.1 Contract and parsing

| Finding | Repository evidence | Current behavior | Risk and recommendation |
|---|---|---|---|
| Accepted actions are exactly four strings. | `backend/app/services/private_webhook_service.py:34-39`, `normalize_action()` at `87-89` | `BUY_CE`, `BUY_PE`, `EXIT`, `HOLD`. Input is trimmed and uppercased, so case-only variants and surrounding whitespace are accepted. No semantic aliases such as LONG, SHORT, CALL, PUT, BUY, or SELL are accepted. | Preserve the wire vocabulary. The future adapter should accept only the already-normalized four-value set. |
| Request body is closed. | `PrivateWebhookPayload` and `PrivateWebhookRequest`, `backend/app/services/private_webhook_service.py:51-72` | Pydantic `extra="forbid"`; credential is ingress-only and excluded from serialization. Optional audit metadata is limited to strategy version, timeframe, reference price, and comment. | Good authority boundary. Do not add desired state or risk/order fields to the external request in R1. |
| Unknown actions fail before persistence. | `ingest()`, `backend/app/services/private_webhook_service.py:316-325` | `normalize_action()` returning `None` produces HTTP 422 / `INVALID_ACTION`; no replay claim, signal row, or execution job is created. | R1B may add a separate safe rejection record after credential resolution, but must not turn unknown input into a strategy signal. |
| Arbitrary actions cannot reach execution. | `_ACTION_MAP`, `backend/app/services/private_webhook_service.py:259-263`; `NormalizedSignal`, `backend/app/schemas/signal.py:6-25` | Only three trading commands map to the closed internal `ENTRY/EXIT` model. `NormalizedSignal.action` is a literal `ENTRY|EXIT`, side is `BUY|SELL`, and option side is `CE|PE|None`. | The target-state adapter can replace `_ACTION_MAP` conceptually while producing the same `NormalizedSignal` at the compatibility boundary. |
| HTTP behavior is explicit and safe. | `private_instance_webhook()`, `backend/app/routers/private_webhook.py:53-84` | 403 disabled; 415 wrong content type; 413 oversized body; 400 malformed/non-object JSON; 422 schema/action/time errors; 401 uniform invalid/revoked credential; 409 lifecycle/conflicting duplicate; 429 per-credential rate limit; 503 unavailable store; 202 fresh accepted; 200 duplicate. Response errors are `{ok:false,error,reason}`. | Preserve status/reason compatibility. Add new reason codes only as additive documented outcomes. |
| Credential binds instance and owner from server state. | `authenticate_credential()`, `backend/app/services/private_webhook_service.py:162-211`; `StrategyInstanceWebhookCredential`, `backend/app/db/models.py:970-1006` | SHA-256 token lookup resolves one instance, then its owner. Unknown and revoked tokens receive the same 401. Token plaintext is never stored; ownership is derived through the instance FK. | Adapter must run after this resolution and may receive only trusted instance/user context. |
| Instance lifecycle is rechecked at ingress and claim. | `validate_instance_lifecycle()`, `backend/app/services/private_webhook_service.py:230-252`; `_revalidate_instance()`, `backend/app/services/private_webhook_execution.py:58-94` | Personal-TradingView only; active or paused instances accepted; controlled verification may accept non-active paper instances; verification can never use real orders. Worker re-reads owner, status, lots, mode under lock. | Preserve both checks. Shadow comparison must not cache lifecycle fields from ingestion. |
| Deduplication is instance-scoped and durable. | `payload_fingerprint()` and `ingest()`, `backend/app/services/private_webhook_service.py:139-155,327-370`; `WebhookEvent`, `backend/app/db/models.py:191-219`; `claim_webhook_event()`, `backend/app/services/webhook_replay_store.py:48-105` | Key is `(provider="instance-webhook:{instance_id}", event_id=signal_id)`. Identical payload retries are duplicates; same ID with a different canonical fingerprint is `CONFLICTING_DUPLICATE`. Strategy signal and job tables also have uniqueness guards. | Target-state mapping must not change the existing replay fingerprint in R1A. A future canonical hash may be stored separately, never substituted in-place. |
| Timestamp validation is bounded and timezone-aware. | `validate_signal_time()`, `backend/app/services/private_webhook_service.py:121-136` | Naive time is rejected; stale and excessive-future-skew signals are rejected according to server settings. | Preserve validation before normalization/persistence. Temporal classification is Pine-analysis metadata, not a substitute for ingress freshness. |
| HOLD is already a connectivity event in behavior, but not in type. | `_persist_hold()`, `backend/app/services/private_webhook_service.py:432-469`; setup evidence validation at `backend/app/services/tradingview_setup_service.py:281-290` | Creates `StrategySignal(status="completed", result_summary={status:"NO_OP",reason:"HOLD",action:"HOLD"})`; creates no execution job and no broker call. It can become verified TradingView setup evidence. | Formalize as `CONNECTIVITY_TEST/NONE`; do not route through `NormalizedSignal`. |
| HOLD cannot reach the worker. | Branch in `ingest()`, `backend/app/services/private_webhook_service.py:372-375`; tests `backend/app/tests/test_private_webhook_execution.py:255-265` | Trading actions call `_persist_execution_job`; HOLD calls `_persist_hold`. | Add a domain invariant test that connectivity events cannot produce jobs. |
| Transport V2 is not an ingress protocol version. | Pine validation at `backend/app/services/pine_validation.py:317-346`; server request schema at `backend/app/services/private_webhook_service.py:51-72` | Source validation enforces the frozen V2 artifact and its one-HOLD gate when reviewing Pine. The runtime webhook accepts any valid four-action request and cannot prove it came from Transport V2 or that HOLD was exclusive on a bar. | Correct separation. Do not add brittle source/transport assertions to the HTTP route. If needed later, persist an approved installation/transport version as trusted setup metadata. |

Tests: `backend/app/tests/test_private_webhook.py:389-419,433-583,604-674` cover actions, case normalization, unknown actions, time validation, privileged fields, duplicate/tamper/concurrency isolation, HOLD duplicates, server authority, and owner-scoped history.

## 3. Verified position and execution model

**Execution-read authority:** per-user JSON (`get_open_position()` / `set_open_position()`) as stated in `backend/app/services/private_webhook_execution.py:141-164,193-205` and `backend/app/db/models.py:1051-1063`. PostgreSQL positions/events are shadow writes and parity evidence.

| Capability | Current behavior | Evidence and tests | Failure mode | Research level |
|---|---|---|---|---|
| One active position | Supported per user/execution mode. JSON has one open-position document; DB shadow has a partial unique active-slot index. | `backend/app/db/models.py:1035-1088`; private tests `166-189`. | Existing open position blocks a non-opposite entry. | L0/L1 foundation |
| Multiple active positions | Not supported by the current private strategy path. | `risk_manager.evaluate_entry()`, `backend/app/services/risk_manager.py:130-135`; DB active-slot uniqueness. | Second non-opposite position is blocked. | L3 |
| Same-side repeated entry | State-based no-op, independent of signal-ID replay. | `_make_enricher()`, `backend/app/services/private_webhook_execution.py:146-162`; tests `191-212`. | `NO_OP / ALREADY_IN_CE|PE`; no risk reservation or broker order. | L1 |
| Exit while flat | State-based no-op. | `backend/app/services/private_webhook_execution.py:149-152,202-205`; tests `235-253`. | `NO_OP / ALREADY_FLAT`. | L1 |
| Opposite-direction reversal | Supported as exit-first sequencing. | `route_reversal_signal()`, `backend/app/services/execution_router.py:1929-2116`; tests `214-233`. | Entry is never sent until exit is confirmed `TRADED`; pending/failed exit leaves no opposite entry. | L1 |
| Atomic reversal | Not supported. Two broker orders are explicitly used. | `backend/app/services/execution_router.py:1933-1935,1968-2046`. | Gap risk exists between completed exit and failed/blocked new entry. | L3 if atomicity required |
| Partial quantity requested by Pine | Not supported. Private payload forbids quantity and worker derives lots. | `PrivateWebhookPayload`, `backend/app/services/private_webhook_service.py:51-66`; build signal at `289-305`. | Client quantity is 422; server determines full current qty. | L3 |
| Broker partial fills | Operationally handled for entry/exit, but not exposed as Pine scale semantics. | `backend/app/services/execution_router.py:1602-1686,1824-1858,2287-2302`; typed shadow fields `models.py:1111-1125`. | Remaining qty stays open; reversal entry is withheld after partial exit. | Execution resilience, not F31 support |
| Multiple entry IDs | Not represented in the private contract or active JSON authority. | `NormalizedSignal` has no entry ID; DB position row has one entry order ID. | Pine trade-level identity collapses to one position. | L3 |
| Scale-in / pyramiding | Not supported. | Risk blocks an existing non-opposite position; validator rejects literal `pyramiding>1` at `pine_validation.py:359-365`. | Block/no-op; no stacked fills. | L3 |
| Scale-out | Not requestable from Pine. Broker partial fill handling is not an intentional scale-out interface. | Same partial-fill evidence above. | Full EXIT requested; accidental partial remains tracked. | L3 |
| Pending entry lifecycle | Private Pine path always constructs MARKET. Router/reconciler can observe pending broker status/open orders, but there is no Pine pending instruction engine. | `private_webhook_service.py:300-305`; `execution_router.py:115-136`; `position_reconciler.py:78-90,204-250`. | Non-terminal order is retained/reconciled; no replace/cancel policy from Pine. | L3 |
| Stop / limit / stop-limit entry | Not supported by the private wire path. | `NormalizedSignal` permits MARKET/LIMIT generally, but private build fixes MARKET; Dhan builder uses zero trigger/price at `execution_router.py:115-135`. | Pine cannot submit parameters. | L3 |
| Cancellation / replacement | Not available to Pine strategy signals. Live Dhan super-order leg cancellation is backend exit cleanup, not Pine cancellation semantics. | `_cancel_super_order_exit_legs()`, `execution_router.py:600-650`. | No canonical cancel/replace action. | L3 |
| Bracket orders | Live router has optional Dhan Super Order support; PaperBroker rejects super orders and uses server monitor exits. Pine does not own bracket levels. | `execution_router.py:378-586`; `paper_broker.py:206-227`. | Paper uses server-managed exits; unsupported broker mode fails closed. | L2 backend normalization |
| Trailing stops / break-even | Typed trailing exit source exists and live/manual super-order modification exists; no general Pine trailing/break-even semantic contract. | `position_operations.py:51-98`; `execution_router.py:652-724`; option monitor. | Must be backend policy; Pine behavior cannot be claimed preserved. | L2 disclosure or L3 rule engine |
| Multiple option legs | Not supported by the private instance path; active position model is one CE or PE contract. | `NormalizedSignal.option_side`; `StrategyInstancePosition` single contract fields `models.py:1098-1125`. | Multi-leg source must be blocked or redesigned. | L3 |
| Broker rejection recovery | Job attempts, live idempotency, pending-order reconciliation, and safe failure exist. Retrying a money-mode job after side effects fails safe in tests. | `StrategyExecutionJob` attempts `models.py:439-448`; live intent `455-484`; tests `267-283,501-525`; reconciler. | Rejection/pending can end job failed or waiting; no automatic semantic compensation trade. | Operational control, not a Pine capability |

### Direction coupling assessment

CE/PE is coupled at the final compatibility edge, not throughout the entire execution model. `_ACTION_MAP` maps `BUY_CE/BUY_PE` to `ENTRY + option_side` (`private_webhook_service.py:259-305`); `_resolve_entry_contract()` resolves that option side (`private_webhook_execution.py:97-138`); reversal compares CE/PE (`risk_manager.py:142-187`). This does **not** prevent target-state normalization: `BULLISH` can adapt to `ENTRY/CE` and `BEARISH` to `ENTRY/PE` immediately before the existing `NormalizedSignal` boundary. No router rewrite is required for R1A.

## 4. Verified risk and exit ownership

| Exit/risk concern | Verified owner and behavior | Evidence | Classification gap |
|---|---|---|---|
| Stop loss / take profit | Backend runtime settings and server option monitor own paper exits; live may use Dhan Super Order levels. | `backend/app/services/option_position_monitor.py:111-155,716-770`; `execution_router.py:378-586`. | Current signal row does not have a closed reason column. Typed shadow events distinguish SL/TP. |
| EOD square-off | Server worker routes an EXIT; Pine EOD is optional additional intent. | `backend/app/workers/eod_squareoff.py`; tests `backend/app/tests/test_paper_mode.py:518-553`; validator warning `pine_validation.py:374-375`. | Persisted strategy signal reason is mostly JSON/source-derived. |
| Strategy EXIT | Pine sends `EXIT`; router reconstructs exit details from trusted open position. | `execution_router.route_exit_signal()`, `2246-2328`. | Can be typed as `EXPLICIT_EXIT` internally. |
| Opposite signal | Router performs exit-first reversal. | `execution_router.py:1929-2116`. | Current result status conveys reversal; canonical reason field is missing. |
| Manual exit | Manual order router emits server-owned `manual_panel` source; typed shadow event maps to `manual_exit`. | `backend/app/routers/orders.py:150-180`; `position_operations.py:83-98`. | Reason exists in typed shadow history, not consistently on `StrategySignal`. |
| Pause | Paused instances block entries/reversals but allow EXIT; HOLD stays audit-only. | `_revalidate_instance()`, `private_webhook_execution.py:76-94`; tests `375-425`. | Correct behavior to preserve. |
| Stop | Stop is rejected while exposure remains; after pause + exit, stop succeeds. | `strategy_instance_service.py:405-455,512-530`; tests `427-449`. | Correct safety behavior. |
| Verification exit | Verification is paper-only and accepts genuine entry/exit evidence. | `private_webhook_execution.py:79-89`; `tradingview_setup_service.py:291-309`. | Preserve in adapter. |
| Duplicate exit | Replay duplicate returns prior status; a new ID while flat state-no-ops. | webhook dedup plus private execution precheck. | Canonical `no_op_reason=ALREADY_FLAT` should be durable/queryable in R1B. |
| Position reconciliation | JSON is reconciled with Dhan positions/open orders in live mode; unknown broker state keeps local exposure fail-closed. | `position_reconciler.py:155-260`. | DB shadow must not become execution authority in R1. |
| Exit precedence | First successful close wins in typed DB operations; reversal waits for confirmed exit; server monitor uses exit cooldown/confirmation. | `position_operations` tests `test_position_operations.py:210-242`; `execution_router.py:1996-2033`; option monitor `483-500`. | A single documented precedence policy is not yet represented as domain data. |

### Existing reason distinguishability

| Requested reason | Current status |
|---|---|
| `ENTRY` | Derivable from normalized action and typed source `STRATEGY_ENTRY`. |
| `REVERSAL` | Exists as typed source `STRATEGY_REVERSAL` and router status/reversal object. |
| `EXPLICIT_EXIT` | Derivable from `STRATEGY_EXIT`, but not a closed persisted signal reason. |
| `STOP_LOSS` | Exists in typed shadow event mapping (`stop_loss_exit`). |
| `TAKE_PROFIT` | Exists in typed shadow event mapping (`take_profit_exit`). |
| `TRAILING_STOP` | Exists in typed shadow event mapping (`trailing_exit`). |
| `SESSION_EXIT` | EOD exists; generic session exit is not a separate closed code. |
| `TIMED_EXIT` | Not a closed server code; may be an ordinary strategy EXIT with metadata. |
| `MANUAL_EXIT` | Exists in typed shadow event mapping. |
| `RISK_EXIT` | Generic code not present; individual server sources exist. |
| `BROKER_REJECTION` | Error/status data exists in job/order results; not a canonical signal reason. |

Recommendation: define the reason enum in R1A, map existing trusted sources in shadow mode, and persist it only in R1B. Never accept these authority-bearing reasons directly from the private webhook metadata.

## 5. Verified database readiness

### 5.1 Field readiness matrix

| Requested concept | Classification | Existing representation / evidence | R1 action |
|---|---|---|---|
| `desired_state` | MISSING | No column or closed model. CE/PE/EXIT are derivable from signal payload/action. | Nullable closed column in R1B after R1A shadow proof. |
| `event_type` | DERIVABLE | HOLD versus trading branch; `PositionEvent.event_type` is unrelated position history and open string. | Add closed signal event type; do not reuse position event type. |
| `reason` | DERIVABLE / fragmented | `result_summary.reason`, router status, raw payload, typed position source. | Add server-derived `reason_code`; optional safe detail separately. |
| `signal_id` | EXISTS | `WebhookEvent.event_id`, `StrategySignal.signal_id`, job, live intent, position event. | Preserve keys. |
| strategy version | DERIVABLE / partial | Request metadata may include `strategy_version`; instance points to immutable `strategy_version_id`; source artifact/version hashes exist. | Persist trusted instance version ID for decisions, not client text as authority. |
| source timeframe | DERIVABLE | Optional webhook `timeframe` lives inside raw/job JSON. | Add nullable source timeframe only if ledger/query needs it. |
| chart timeframe | DERIVABLE / MISSING distinction | TradingView setup `requested_timeframe` and installation metadata; webhook `timeframe` not proven snapshot. | Treat installed/requested/chart-observed values separately. |
| temporal classification | MISSING | Manifest has free-form timing/repainting strings, no T class. | Add to validation/capability manifest, not execution authority. |
| capability manifest | MISSING as durable typed artifact | Admin manifest is validated text/output; validation findings and conversion fields are durable but no complete registry result. | Add JSON manifest to validation analysis record in R1B. |
| advisory metadata | DERIVABLE | Private payload allowlist is stored in job raw payload; conversion warnings/assumptions exist. | Keep as non-authoritative JSON with schema/size limits. |
| rejection code/detail | MISSING for pre-persistence webhook rejection | HTTP reasons and audit logs exist; failed jobs store errors. | Add separate safe rejection table in R1B. |
| current state before signal | DERIVABLE transiently | JSON position read and typed position previous state. | Capture in decision record; never trust Pine. |
| requested state | MISSING | Can derive from action. | Add canonical desired state. |
| routing result | DERIVABLE | Job/result summaries and router status. | Normalize to closed result code plus existing detailed JSON. |
| no-op reason | DERIVABLE | `ALREADY_FLAT`, `ALREADY_IN_*`, `HOLD`, pause reasons in result JSON. | Add nullable closed no-op reason. |
| connectivity event | DERIVABLE | HOLD signal with no job; TradingView setup pins HOLD signal ID/time. | Add event type; no new execution entity. |
| pending-order lifecycle | EXISTS partially / UNSAFE_TO_GENERALIZE | Position shadow metadata, order statuses, reconciliation, live order intent. | Out of R1; do not advertise as strategy pending engine. |
| partial fills | EXISTS operationally | Position quantity columns/events and router handling. | Keep operational; intentional partial strategy semantics out of R1. |
| multiple legs | MISSING / UNSAFE_TO_ADD_WITHOUT_DESIGN | One active position row contains one option contract. | Out of scope; requires new position/leg aggregate. |

### 5.2 Relevant models and migrations

- Durable webhook/replay and strategy jobs: `backend/app/db/models.py:191-236,391-484`; migration `backend/alembic/versions/20260623_0002_durable_webhook_risk.py`.
- Strategy version/source/validation/review: `backend/app/db/models.py:610-797`; migrations `20260711_0006_strategy_registry.py`, `20260714_0011_personal_pine_validation.py`, `20260715_0012_pine_conversion_requests.py`.
- Strategy instances and hashed credentials: `backend/app/db/models.py:891-1006`; migrations `0007_strategy_instances.py`, `0008_structural_consistency.py`.
- Shadow positions and typed events: `backend/app/db/models.py:1035-1191`; migrations `0009_position_shadow.py`, `0010_position_ownership.py`.
- TradingView acceptance/setup evidence: `backend/app/db/models.py:800-888`; migration `0013_manual_tradingview_flow.py`.
- Verification mode: migration `20260716_0014_verify_select.py`.

### 5.3 Minimum migration recommendation

**R1A: no migration.** Produce canonical decisions in memory, compare them with existing action mapping, emit metrics/tests, and continue persisting exactly the current structures.

**R1B required additive changes:**

1. Add nullable `event_type`, `desired_state`, `reason_code`, `adapter_version`, `current_state`, `routing_result`, and `no_op_reason` to `strategy_signals`. Backfill is optional and must be deterministic from existing result/job data; no NOT NULL conversion in R1.
2. Add a bounded `canonical_metadata` JSON field only if existing `result_summary` is not retained as the detail bag. Do not duplicate secrets or user-supplied authority fields.
3. Add a `strategy_signal_rejections` append-only table for post-credential/pre-job failures: user ID, instance ID, signal ID if valid, webhook-event reference/fingerprint, rejection code, safe detail, adapter/contract version, received time. Never store credential plaintext or full hostile payload by default.
4. Add `capability_manifest` JSON and `temporal_class` to the Pine validation result (or a new immutable Pine semantic-analysis table linked to source hash and analyzer version). Prefer a new immutable analysis table if registry/analyzer identity needs independent reruns.

**Optional later:** normalized source/chart/installed timeframe columns for analytics; a materialized signal ledger projection; stronger enum/check constraints after production evidence.

**Explicitly out of scope:** pending-order tables, order replacement/cancellation schema, partial-exit intent, multi-leg aggregates, multiple active positions, Pine sizing/risk columns.

## 6. Verified static-validator coverage

The current validator is regex/call-span based (`backend/app/services/pine_validation.py:139-208,211-393`), not a Pine parser or TradingView compiler.

| Construct | Verdict | Evidence / limitation |
|---|---|---|
| indicator versus strategy | DETECTED_INCOMPLETELY | Finds exactly one `indicator()` or `strategy()` declaration (`283-294`) but does not return declaration type or apply strategy-specific policy. |
| Pine version | DETECTED_CORRECTLY for declared integer v4/v5/v6+ | `272-281`; v5 warning, <5 and >6 error. No version-specific API semantic analysis. |
| `strategy.entry` | DETECTED_INCOMPLETELY | Only counts more than two calls (`362-363`); parameters, IDs, direction, stop/limit, and replacement are not parsed. |
| `strategy.order` | DETECTED_INCOMPLETELY | Generic `POSITION_MODEL_UNSUPPORTED` regex (`364-365`); no semantics. |
| `strategy.exit` | NOT_DETECTED | No direct rule. |
| `strategy.close` / `close_all` | NOT_DETECTED | No direct rule. |
| stop entry / limit entry / stop-limit entry | NOT_DETECTED | No argument analysis. |
| pyramiding | DETECTED_INCOMPLETELY | Literal integer `pyramiding>1` is blocked (`359-361`); expressions/defaults and effective behavior are not analyzed. |
| partial exit | NOT_DETECTED | No `qty`/`qty_percent` analysis in exit calls. |
| multiple entry IDs | DETECTED_INCOMPLETELY | Call-count warning only; IDs are not extracted. |
| `strategy.position_size` | NOT_DETECTED | No rule. |
| `strategy.position_avg_price` | NOT_DETECTED | No rule. |
| `calc_on_every_tick` | NOT_DETECTED | Missing declaration-argument analysis. |
| `calc_on_order_fills` | NOT_DETECTED | Missing. |
| `process_orders_on_close` | NOT_DETECTED | Missing. |
| `varip` | NOT_DETECTED | Missing. |
| `request.security` | DETECTED_INCOMPLETELY | More than one call produces `MULTI_SYMBOL_RISK` (`366-367`); timeframe, symbol, offset, and dataflow are not classified. |
| `request.security_lower_tf` | NOT_DETECTED | Missing. |
| `lookahead_on` | DETECTED_INCOMPLETELY / FALSE_POSITIVE_RISK | Any occurrence warns (`369-371`); cannot distinguish safe confirmed HTF `expr[1]+lookahead_on` from future leakage. |
| dynamic symbols/timeframes | NOT_DETECTED | No argument type/dataflow analysis. |
| multiple execution symbols | DETECTED_INCOMPLETELY | Multiple requests warn, but analytical requests versus execution authority are not distinguished. |
| non-standard charts / synthetic prices | NOT_DETECTED | No chart-type or ticker transformation analysis. |
| protected source | NOT_DETECTED | Empty/missing source fails, but invite-only/protected availability is a workflow condition, not detected from supplied text. |
| malformed/non-compiling Pine | DETECTED_INCOMPLETELY | UTF-8, null/control chars, size/line length, version/declaration are checked (`239-287`); syntax/type/compile correctness is not. |
| custom webhook JSON | DETECTED_INCOMPLETELY | Alert calls are scanned for authority keys; frozen transport payload fields are allowlisted (`296-305,334-343`). Arbitrary string construction/dataflow can evade regex. |
| authority-bearing fields | DETECTED_CORRECTLY for literal keys, incomplete semantically | Closed list at `37-41`; literal JSON keys in alert calls are blocked. Computed keys or non-alert transport are not fully parsed. |
| prompt injection content | NOT_DETECTED as a capability | Comments are removed for code scanning and package source is delimited, but hostile instructions are not classified/reason-coded. Conversion tests prove isolation, not semantic injection detection. |
| frozen transport integrity | DETECTED_CORRECTLY for registered templates | Exact normalized block match plus required fragments (`125-136,317-346`). |
| action vocabulary | DETECTED_CORRECTLY for literal alert calls | Supported/unsupported literal actions and required entry/exit/HOLD rules (`296-315`). Dynamic action construction is not fully semantic. |
| bar-close gating | DETECTED_INCOMPLETELY | Presence heuristic for `barstate.isconfirmed` or once-per-bar-close (`372-373`); no control-flow proof. |

Recommendation: keep this validator as the transport/basic-safety layer. Add a separate versioned pre-analyzer rather than expanding one regex function into an implicit parser.

## 7. Verified conversion-manifest coverage

The manifest is a closed dictionary validated by `MANIFEST_KEYS` and `validate_admin_manifest()` (`backend/app/services/pine_validation.py:27-33,75-122`).

| Requirement | Current coverage |
|---|---|
| capability level L0-L4 | MISSING |
| temporal class T0-T9 | MISSING; `signal_timing` and free-form repainting text are insufficient |
| behavior preservation | PARTIAL: `behavior_classification` free-form string |
| disclosed behavior changes | EXISTS: `behavior_changes[]` |
| blocked capability | PARTIAL: `status=BLOCKED`, `unsupported_constructs[]` |
| blocker code | PARTIAL: `blocked_reasons[]` is untyped strings |
| strategy exit ownership | MISSING; only `explicit_exit_present` and fixed `BACKEND_EXIT_FIRST` reversal |
| risk ownership | MISSING |
| sizing ownership | MISSING |
| pending-order incompatibility | Only possible as free-form unsupported/blocker text |
| partial-exit incompatibility | Only free-form |
| MTF/repainting warning | PARTIAL: `timeframe_policy`, `repainting_classification` free-form |
| source version | EXISTS: `source_pine_version` |
| source hash | MISSING from manifest; exists outside it in version/package records |
| prompt version | MISSING from manifest; exists in package/request/acceptance records |
| transport version | EXISTS: `frozen_transport_version` |
| admin review requirements | EXISTS as untyped `admin_review_points[]` |
| user acceptance requirements | MISSING from manifest; enforced separately through `PineUserAcceptance` and setup service |

The V3/V3.1 package service pins source, prompt, and transport hashes outside the manifest (`backend/app/services/pine_conversion_service.py:222-289`). That is strong artifact provenance, but it does not replace a capability manifest.

## 8. Verified UI readiness

| User/admin need | Current UI | Gap |
|---|---|---|
| Converted without changes | Conversion candidate summary and deterministic validation are displayed in `frontend/src/strategies/ImportedPinePage.tsx:227`. | No formal capability-preserved badge. |
| Converted with disclosed changes | Assumptions, unsupported/removed features, AI warnings, and validation findings are shown. | No registry-backed disclosure codes or per-capability acceptance. |
| Unsupported / blocked unsafe strategy | Safe conversion error and static ERROR findings are shown; setup has blocking state. | No unified capability blocker panel. |
| Source-version mismatch | Admin installation rejects mismatched hash server-side (`tradingview_setup_service.py:241-252`); admin UI displays approved hash (`ImportedPinePage.tsx:290`). | User-facing drift/snapshot mismatch lifecycle is incomplete. |
| Temporal/repainting warning | Generic validator findings render with severity, code, explanation, remediation (`ImportedPinePage.tsx:215-216`). | No T-class, HTF provenance, or standard/synthetic chart warning view. |
| Backend-managed SL/TP | General runtime/setup UI exposes risk settings elsewhere. | Imported-Pine review does not clearly disclose that Pine SL/TP semantics may be replaced by backend policy. |
| Pending/partial incompatibility | Can appear only as free-form unsupported text or findings. | Needs typed incompatibility cards from registry. |
| Connectivity receipt | Personal page sends durable HOLD and displays history/readiness; managed setup shows HOLD verification. | Already useful; should relabel internally as connectivity event without changing public text abruptly. |
| Signal ledger | Personal strategy history table shows action, signal ID, status/job status, contract/no order (`PersonalStrategiesPage.tsx:566-664`; API types `frontend/src/api.ts:557-565`). | Missing canonical desired state, routing result code, current/requested state, normalized reason, snapshot version. |
| No-op signal | HOLD and job statuses are displayed; helper labels HOLD as no action. | Same-side/flat no-op reason is not a first-class column. |
| Exit reason | Not consistently displayed in private signal history. | Add after R1B reason persistence. |
| Alert snapshot/version mismatch | Approved/installed source hash and requested timeframe exist. | No ongoing alert snapshot drift/expiry/disabled-alert monitor. |

## 9. Target-state compatibility result

**COMPATIBLE_WITH_NO_EXECUTION_BEHAVIOR_CHANGE.**

The adapter should produce a closed domain object such as:

```text
CanonicalSignalEvent(
  event_type = STRATEGY_SIGNAL | CONNECTIVITY_TEST,
  desired_state = BULLISH | BEARISH | FLAT | NONE,
  reason = finite server-owned enum,
  signal_id,
  signal_time,
  metadata = non-authoritative allowlist
)
```

Compatibility conversion to the current router remains:

```text
BULLISH -> NormalizedSignal(action=ENTRY, side=BUY, option_side=CE)
BEARISH -> NormalizedSignal(action=ENTRY, side=BUY, option_side=PE)
FLAT    -> NormalizedSignal(action=EXIT,  side=SELL, option_side=None)
NONE/CONNECTIVITY_TEST -> persist only; never build NormalizedSignal
```

State transition decisions remain where they are today: flat entry, same-side no-op, opposite-side exit-first reversal, and flat exit no-op. R1A must assert byte/field-equivalent `NormalizedSignal` output for all three trading actions and no-job equivalence for HOLD.

## 10. Legacy-adapter placement recommendation

**Recommend Option D: a new pure domain normalization service, called from Option B's location.**

Proposed call sequence:

```text
HTTP parse -> credential resolution -> lifecycle validation -> freshness/replay claim
-> pure legacy_action_adapter.normalize(action, trusted context, safe metadata)
-> connectivity persistence OR existing execution-job persistence
```

Properties:

- public request schema and response codes unchanged;
- credential/owner binding unchanged;
- replay key/fingerprint unchanged in R1A;
- unknown actions still rejected before canonical construction;
- metadata cannot set event type, desired state, reason, quantity, contract, mode, or risk;
- verification/paper/live gates remain in current services;
- existing PaperBroker/reversal behavior receives the same `NormalizedSignal`;
- pure unit tests do not need FastAPI, DB, broker, or files;
- feature flag can compare adapter output then fall back to `_ACTION_MAP` for rollback.

## 11. Capability-registry specification

Use **version-controlled JSON documents validated by JSON Schema and loaded into typed Python dataclasses/Pydantic models**.

Why JSON first:

- deterministic standard-library parsing and canonical hashing;
- reviewable diffs and no YAML coercion/anchor ambiguity;
- language-neutral for future frontend/tooling;
- no database deployment or mutable-policy risk;
- typed Python constants alone would make external tooling harder; DB rows would make policy mutable and migration-dependent.

Suggested artifact layout:

```text
backend/app/pine_capabilities/registry.v1.json
backend/app/pine_capabilities/registry.schema.json
backend/app/domain/pine_capabilities.py       # typed loader only
```

Each entry must contain the requested fields: `capability_id`, `family`, `pine_patterns`, `pine_apis`, `minimum_pine_version`, `maximum_tested_pine_version`, `capability_level`, `temporal_class`, `required_nova_backend_capability`, `conversion_policy`, `allowed_normalization`, `mandatory_disclosure`, `blocker_code`, `admin_review_points`, `user_facing_message`, `fixtures`, `effective_version`, and `deprecated_version`.

Additional controls:

- Registry ID and content SHA-256 in every analysis result.
- Closed enums for level, policy, T class, blocker/disclosure codes.
- Overlapping pattern matches are allowed but resolution order must be explicit and tested.
- The most restrictive matched capability wins unless a registry rule explicitly defines composition.
- Registry cannot enable execution features; it classifies conversion support only.

## 12. Temporal-class specification

| Class | Final recommended name | Current validator relationship | Policy |
|---|---|---|---|
| T0 | `CONFIRMED_BAR_CLOSE_DETERMINISTIC` | Heuristic warning absence only. | Convertible. |
| T1 | `REALTIME_FLUCTUATION_NORMALIZED_TO_CLOSE` | `BAR_CONFIRMATION_MISSING` may hint at it, not prove it. | L2 disclosure if safely normalized; otherwise block. |
| T2 | `HTF_DEVELOPING_SERIES` | Generic `request.security` awareness only. | L2 disclosure and admin review. |
| T3 | `HTF_CONFIRMED_OFFSET_SERIES` | Currently falsely shares `lookahead_on` warning with T4. | L1 if exact offset pattern is proven. |
| T4 | `FUTURE_LEAKAGE_LOOKAHEAD` | Current `POTENTIAL_REPAINTING` is too broad. | L4 blocker `BLK-FUTURE-LEAK`. |
| T5 | `LOWER_TIMEFRAME_ARRAY_AGGREGATION` | Not detected. | L2 only for deterministic bar-close aggregation; otherwise block. |
| T6 | `FILL_DEPENDENT_RECALCULATION` | Not detected. | L3 blocker until backend fill-feedback design. |
| T7 | `SYNTHETIC_CHART_PRICE_BASIS` | Not detected. | L4 blocker. |
| T8 | `INTRABAR_NONREPRODUCIBLE_STATE` | Not detected (`varip`, realtime branches, tick modes). | L4 blocker. |
| T9 | `EXTERNAL_OR_UNAVAILABLE_DATA` | Multi-security warning is insufficient. | Advisory-only L2 where execution-independent; otherwise L4. |

T class is analysis/disclosure data. It must never change order quantity, contract selection, or risk limits.

## 13. Fixture-gap matrix F01-F66

Legend: **E** executable equivalent exists (possibly at a different layer); **P** partial or policy-only evidence; **M** missing. “Current” describes what today's repository would classify or exercise, not the desired research outcome.

| ID | Scenario | Repo | Current result | Proposed result | Required layers |
|---|---|---:|---|---|---|
| F01 | Market long from flat | E | Webhook/PaperBroker CE entry succeeds | L0/T0 | WEBHOOK_NORMALIZATION, EXECUTION_ROUTER, PAPERBROKER |
| F02 | Market short from flat | E | PE entry succeeds | L0 | same |
| F03 | Full market exit | E | EXIT closes tracked position | L0 | normalization/router/PaperBroker |
| F04 | No-trade/wait | P | HOLD exists, but true no-emission Pine fixture absent | L0 | STATIC_PRE_ANALYZER, TRANSPORT_VALIDATOR |
| F05 | Confirmed boolean trigger | E | Representative validator Pine uses confirmed close | L0/T0 | STATIC_PRE_ANALYZER, TRANSPORT_VALIDATOR |
| F06 | Opposite-entry reversal | E | Exit-first, non-atomic reversal | L1 with disclosed non-atomic routing | normalization/router/PaperBroker |
| F07 | Same-side repeat | E | State no-op | L1 | router/PaperBroker |
| F08 | Exit while flat | E | State no-op | L1 | router |
| F09 | `strategy.close(id)` | M | API not detected; would rely on conversion prose | L1 collapse ID | pre-analyzer/manifest/external qualification |
| F10 | Immediate close | M | Not detected | L1 with timing disclosure | pre-analyzer/manifest/TradingView compile |
| F11 | Limit entry | P | 25-case policy only; validator does not parse limit | L3 `BLK-PENDING-ENGINE` | pre-analyzer/manifest |
| F12 | Stop entry | P | Policy only; no semantic detection | L3 blocker | pre-analyzer/manifest |
| F13 | Stop-limit | M | Not detected | L3 blocker | pre-analyzer/manifest |
| F14 | Same-ID replacement | M | Not detected | L3 blocker | pre-analyzer/manifest |
| F15 | Cancel by ID/all | M | Not detected | L3 blocker | pre-analyzer/manifest |
| F16 | Cancel after N bars | M | Not detected | L3 blocker | pre-analyzer/manifest |
| F17 | OCA behavior | M | Not detected | L3 blocker | pre-analyzer/manifest |
| F18 | Mutually exclusive pending orders | M | Not detected | L3 blocker | pre-analyzer/manifest |
| F19 | Fixed SL/TP | P | Backend SL/TP exists; Pine `strategy.exit` not classified | L2 `DISC-BACKEND-RISK` | pre-analyzer/manifest/router |
| F20 | Absolute-price bracket | P | Backend levels exist; Pine semantics not detected | L2 disclosure | pre-analyzer/manifest |
| F21 | v6 relative+absolute exit | M | Not detected/version-differentiated | L2 version disclosure | pre-analyzer/manifest/compile |
| F22 | Built-in trailing | P | Backend typed trailing exit exists; Pine args not detected | L2 approximation disclosure | pre-analyzer/manifest/router |
| F23 | Custom close trailing | P | Server monitor exists; source behavior not classified | L2 disclosure | pre-analyzer/manifest |
| F24 | Break-even stop | M | No semantic rule | L2/L3 depending approved risk plan | pre-analyzer/manifest |
| F25 | Session/EOD exit | E | Server EOD tested; validator only heuristic | L1 `SESSION_EXIT` | pre-analyzer/router/PaperBroker |
| F26 | Timed exit | P | Ordinary EXIT supported; reason/timing fixture absent | L1 `TIMED_EXIT` | pre-analyzer/normalization |
| F27 | Boolean conditional exit | E | Existing valid Pine and EXIT routing | L0 | pre-analyzer/transport/router |
| F28 | Fixed quantity | E | Pine qty stripped; server lots own sizing | L1 with sizing disclosure | manifest/normalization/router |
| F29 | Percent equity/cash | M | Not detected | L2 `DISC-SIZING-BACKEND` | pre-analyzer/manifest |
| F30 | Pyramiding scale-in | P | Literal >1 blocked; complete behavior fixture absent | L3 `BLK-MULTI-FILL` | pre-analyzer/manifest |
| F31 | Partial exit | P | Broker accidental partial fills handled; Pine request not detected | L3 `BLK-PARTIAL-QTY` | pre-analyzer/manifest/router resilience |
| F32 | Multi-level scaled exits | M | Not detected | L3 blocker | pre-analyzer/manifest |
| F33 | Multiple entry IDs | P | More-than-two-call warning only | L3 blocker | pre-analyzer/manifest |
| F34 | Position-size gating | M | Not detected | L1 state rebasing | pre-analyzer/conversion manifest |
| F35 | Avg-price exits | M | Not detected | L2 `DISC-EMULATOR-DROP` | pre-analyzer/manifest |
| F36 | Trade/P&L feedback | P | `strategy.opentrades` generic error; closedtrades absent | L3 `BLK-FILL-DEPENDENT` | pre-analyzer/manifest |
| F37 | `calc_on_order_fills` | M | Not detected | L3 blocker/T6 | pre-analyzer/manifest |
| F38 | `calc_on_every_tick` | P | Bar-confirmation heuristic may warn, declaration flag not detected | L4 `BLK-INTRABAR-TIMING` | pre-analyzer/manifest |
| F39 | `process_orders_on_close` | M | Not detected | L2 disclosure | pre-analyzer/manifest/compile |
| F40 | `varip` | M | Not detected | L4 `BLK-NONREPRODUCIBLE` | pre-analyzer/manifest |
| F41 | realtime-only branch | P | Missing confirmation may warn, branch semantics not classified | L4 blocker | pre-analyzer/manifest |
| F42 | Confirmed HTF offset + lookahead | P | Current validator warns on all lookahead_on | L1/T3 | pre-analyzer/manifest |
| F43 | Developing HTF | P | Single request may receive no MTF warning | L2/T2 disclosure | pre-analyzer/manifest |
| F44 | Unsafe lookahead | E | `POTENTIAL_REPAINTING` warning only, not blocker | L4/T4 blocker | pre-analyzer/manifest |
| F45 | Lower-TF arrays | M | Not detected | L2/T5 disclosure | pre-analyzer/manifest |
| F46 | Dynamic symbol/timeframe | P | Multiple calls may warn; dynamic argument not detected | L3 analysis / L4 execution | pre-analyzer/manifest |
| F47 | Financial/economic data | M | Not detected | L2 advisory | pre-analyzer/manifest |
| F48 | `request.seed` | M | Not detected | L3 advisory / L4 authority | pre-analyzer/manifest |
| F49 | Footprint/bid-ask | M | Not detected | L4 external-data blocker | pre-analyzer/manifest |
| F50 | Synthetic chart | M | Not detected | L4 `BLK-SYNTHETIC-PRICE` | pre-analyzer/manifest/TradingView setup |
| F51 | Extended session | M | Generic EOD heuristic only | L2 `DISC-SESSION` | pre-analyzer/manifest |
| F52 | Adjusted/continuous series | M | Not detected | L2 advisory | pre-analyzer/manifest |
| F53 | Indicator-on-indicator input | M | Not detected | L4 external-data blocker | pre-analyzer/manifest |
| F54 | Legacy v1/v2 | P | Version <5 rejected, no lookahead-default explanation | L4 version blocker | pre-analyzer/manifest |
| F55 | v3-v5 semantics | P | v5 warning; v3/v4 blocked; API differences not classified | L2 version disclosure after upgrade | pre-analyzer/manifest/compile |
| F56 | Order-fill alert as signal | M | Placeholder provenance not classified | L2 event-provenance disclosure | pre-analyzer/manifest |
| F57 | `alert()` bar-close | E | Action/frequency path validated | L1/T0 | transport validator |
| F58 | `alertcondition()` placeholders | P | Action call found; placeholder semantics not classified | L1 | pre-analyzer/transport |
| F59 | Confidence/S-R metadata | P | Optional metadata exists, but frozen payload schema does not define rich advisory fields | L1 advisory disclosure | manifest/normalization/UI |
| F60 | Prompt injection source | P | Package delimiters and tests isolate source; no blocker classifier | L4 `BLK-INJECTION-SUSPECT` per research policy | pre-analyzer/manifest/external qualification |
| F61 | Protected source | P | Policy record exists; no executable/source-availability fixture | L4 blocker | workflow/manifest |
| F62 | Malformed Pine | P | Text/declaration checks only; no compile parser | L4 `BLK-MALFORMED` | pre-analyzer/TradingView compile |
| F63 | v2 lookahead default | P | Generic version block only | L4 version blocker | pre-analyzer/manifest |
| F64 | v4 `when=` | M | v4 rejected without conversion semantics | L2 version disclosure after upgrade | pre-analyzer/manifest/compile |
| F65 | v5 `qty_percent` remainder | M | v5 warning only | L2/L3 version + partial policy | pre-analyzer/manifest |
| F66 | v5-v6 exit differential | M | Not detected | L2 version disclosure | pre-analyzer/manifest/compile |

Summary: strong executable coverage exists for the current L0/L1 routing core (entries, exits, no-ops, reversal, HOLD) and transport integrity. The semantic Pine corpus itself is overwhelmingly absent. The 25-case qualification JSON is policy evidence, not a substitute for minimal Pine fixtures and expected classifier records.

## 14. TradingView dependency risk register

| Risk | Type | Current mitigation | Required before commercial live deployment |
|---|---|---|---|
| User-owned alert setup can use wrong script, inputs, symbol, timeframe, URL, or stale credential. | TECHNICAL / OPERATIONAL | Setup instructions, hashed credential, HOLD test, paper entry/exit evidence. | Snapshot attestation and drift/renewal workflow; support runbook. |
| NOVA-managed setup is a manual operational queue. | OPERATIONAL / BUSINESS_DECISION_REQUIRED | Admin-only installation metadata and exact approved source hash. | Ownership, staffing, audit retention, rotation, incident and access-control policy. |
| TradingView alerts are snapshots; later script/input/timeframe changes may not update the alert. | TECHNICAL / PLATFORM_POLICY_REVIEW_REQUIRED | Approved and installed source hashes plus requested timeframe are stored. | Explicit alert snapshot/version model and periodic re-verification. |
| Alerts can expire, be disabled, be deleted, or stop due to account/plan state. | OPERATIONAL / BUSINESS_DECISION_REQUIRED | No active liveness monitor beyond initial HOLD/readiness. | Heartbeat/age policy, user notification, managed-account monitoring. |
| Webhook delivery can be delayed, duplicated, reordered, or unavailable. | TECHNICAL | Freshness window, durable replay key/fingerprint, idempotent job uniqueness, state no-ops. | SLOs, observability, dead-letter/reconciliation policy, sequence expectations. |
| Rate limits or burst behavior can suppress valid signals. | TECHNICAL / OPERATIONAL | Per-credential in-process minute limiter. | Distributed limiter design, documented capacity, alert-volume qualification. |
| Pine condition alerts and strategy order-fill alerts have different event provenance. | TECHNICAL | Current converted indicators use `alert()` payloads; NOVA does not trust TradingView fill state. | Registry/pre-analyzer disclosure and F56 qualification. |
| Realtime versus historical Pine behavior can diverge. | TECHNICAL | Bar-close transport and generic repaint warnings. | T-class pre-analyzer, blocking unsafe modes, TradingView compile/replay qualification. |
| Platform terms/policies may constrain managed accounts, automation, or commercial service design. | PLATFORM_POLICY_REVIEW_REQUIRED / BUSINESS_DECISION_REQUIRED | No conclusion made by this audit. | Formal platform-policy review and business approval before commercial live offering. |

Paper-only engineering research can continue. None of these findings authorizes live-order enablement.

## 15. Phase R1 implementation scope

### R1A — domain model, adapter, registry, analyzer foundation (no migration)

1. Immutable canonical signal/event dataclasses and closed enums.
2. Pure legacy action adapter with the four mappings and server-owned reason defaults.
3. Explicit connectivity event separation.
4. Registry v1 JSON + JSON Schema + typed loader.
5. Deterministic pre-analyzer initially covering high-confidence L0/L1/L4 and T0/T3/T4/T8 signals.
6. Reason-coded semantic validation result in memory/API preview, not execution behavior.
7. Shadow comparison: old `_ACTION_MAP` output versus adapter compatibility output for every accepted trading action.
8. Initial fixtures focused on the classifier rules implemented in R1A.

Acceptance: current private webhook and execution suites pass unchanged; adapter output is equivalent; no new DB writes; no public wire change; no prompt/transport change.

### R1B — additive persistence and durable rejection records

1. Nullable canonical fields on `strategy_signals`.
2. Append-only safe rejection table.
3. Immutable capability-analysis persistence pinned to source hash, analyzer version, registry version/hash.
4. Closed routing/no-op/reason codes populated from trusted server decisions.
5. Backfill tooling only after deterministic mapping review; no destructive rewrite.

### R1C — UI and fixture expansion

1. Signal ledger with event type, desired state, current/requested state, routing result, no-op/exit reason, and source/install snapshot references.
2. Connectivity receipt/history separated visually from trading signals.
3. Registry-backed blocker/disclosure cards including temporal class, backend SL/TP ownership, pending/partial incompatibility.
4. Expand F01-F66 corpus in CI by supported analyzer family.
5. Admin comparison view for pre-analyzer versus conversion manifest.

Staging is strongly preferred. Combining all three slices would mix execution-contract proof, migration risk, and UI/corpus work unnecessarily.

## 16. Explicit out-of-scope items

No R1 slice should implement numeric Pine SL/TP authority, pending/limit/stop/stop-limit orders, cancel/replace, partial exits, scale-in/out, pyramiding, multiple positions, multiple option legs, Pine quantity/strike/expiry/risk authority, confidence-based execution, Prompt V4, Transport V3, AI conversion enablement, or live-order enablement.

## 17. File-by-file implementation plan

| Proposed file | R1 purpose | Safety boundary |
|---|---|---|
| `backend/app/domain/canonical_signal.py` | Closed event/state/reason/result types. | Pure data; no DB/broker imports. |
| `backend/app/domain/legacy_signal_adapter.py` | Four-action mapping and compatibility conversion. | Pure and exhaustively unit-tested. |
| `backend/app/pine_capabilities/registry.v1.json` | Versioned policy. | Reviewable data; cannot place orders. |
| `backend/app/pine_capabilities/registry.schema.json` | Closed registry validation. | Fail startup/test on invalid registry. |
| `backend/app/services/pine_semantic_preanalyzer.py` | Deterministic feature vector and registry matches. | Static-only; no AI/TradingView/broker. |
| `backend/app/services/private_webhook_service.py` | Invoke adapter after auth/freshness/replay; shadow compare; later persist canonical fields. | Preserve public parsing, fingerprint, and branch behavior. |
| `backend/app/services/private_webhook_execution.py` | Accept compatibility `NormalizedSignal` exactly as today. | Prefer no R1A logic change beyond assertions/typing. |
| `backend/app/services/pine_validation.py` | Compose existing transport/basic validator with semantic analyzer results. | Keep layers distinguishable in output. |
| `backend/app/db/models.py` + one new migration | R1B nullable fields/rejection/analysis records. | Additive only. |
| `backend/app/tests/test_canonical_signal_adapter.py` | Mapping/equivalence/unknown-action/HOLD invariants. | No external services. |
| `backend/app/tests/pine_fixtures/...` | Minimal F-series source + expected JSON. | Immutable fixture IDs. |
| `frontend/src/strategies/PersonalStrategiesPage.tsx` | R1C ledger/connectivity fields. | Read-only display. |
| `frontend/src/strategies/ImportedPinePage.tsx` | R1C capability/disclosure/temporal views. | No client-side policy authority. |

Prompt and transport files are intentionally absent from this plan.

## 18. Migration recommendation

Create no migration in R1A. For R1B, use one additive migration from the then-current head, with nullable columns and new tables only. Upgrade/downgrade must be tested on PostgreSQL and SQLite test paths where supported. Do not alter `0014_verify_select`, reuse its revision ID, or combine the change with a position-authority migration. A later, separately reviewed phase may consider PostgreSQL position authority after parity evidence; that is not part of canonical-signal R1.

## 19. Test plan and audit test results

### R1 implementation test plan

1. Pure adapter truth table, invalid input, immutability, metadata non-authority.
2. Golden equivalence against current `_ACTION_MAP`/`NormalizedSignal` serialization.
3. Existing private webhook ingress suite unchanged.
4. Existing private execution suite unchanged, including no-op/reversal/pause/verification/live blocks.
5. Registry schema/hash and duplicate/overlap policy tests.
6. Pre-analyzer fixture tests by capability/T class, with 100% L4 blocker precision for implemented families.
7. Manifest/pre-analyzer disagreement tests.
8. Migration upgrade/downgrade and old-row null compatibility in R1B.
9. Ledger/connection UI tests in R1C.
10. No Dhan credentials or network dependence; no TradingView compile claim unless run externally and recorded honestly.

### Existing tests run for this audit

The final audit run and outcomes are recorded below after execution:

```powershell
$env:PYTHONPATH='backend'
& 'C:\Users\anubh\OneDrive\Desktop\Layman-nova-signal-route\.venv\Scripts\python.exe' -m pytest `
  backend/app/tests/test_private_webhook.py `
  backend/app/tests/test_private_webhook_execution.py -q
```

Result: **81 passed**, one third-party Starlette/httpx deprecation warning, 156.47 seconds.

```powershell
$env:PYTHONPATH='backend'
& 'C:\Users\anubh\OneDrive\Desktop\Layman-nova-signal-route\.venv\Scripts\python.exe' -m pytest `
  backend/app/tests/test_personal_pine.py `
  backend/app/tests/test_pine_conversion.py `
  backend/app/tests/test_pine_master_prompt_v3.py `
  backend/app/tests/test_pine_transport_v2.py `
  backend/app/tests/test_pine_v31_source_binding.py `
  backend/app/tests/test_tradingview_setup.py -q
```

Result: **64 passed**, the same third-party deprecation warning, 58.53 seconds.

**Focused audit total: 145 passed, 0 failed.** No Dhan baseline suite or unrelated full-backend suite was run or used in the conclusions.

## 20. Rollback plan

### R1A rollback

- Feature flag disables adapter shadow comparison/use.
- Existing public action parsing, `_ACTION_MAP`, persistence, jobs, and router remain intact.
- Registry/analyzer can be disabled without data rollback.

### R1B rollback

- Application first stops writing/reading new nullable fields and rejection/analysis tables.
- Downgrade removes only newly added nullable columns/tables; existing signal/job/webhook/position data is untouched.
- Do not backfill destructively or make new fields mandatory in the same release.

### R1C rollback

- UI hides new projections and continues using existing signal history/status fields.
- No execution behavior depends on the UI.

## Safety confirmation

This audit did not change runtime code, Prompt V2/V3/V3.1, Transport V1/V2, or any migration. It did not call an external AI, enable AI conversion, enable live orders, place an order, merge a branch, or deploy. The only intended repository change is this report.
