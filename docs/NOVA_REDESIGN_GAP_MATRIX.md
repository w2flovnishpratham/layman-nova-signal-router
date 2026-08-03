# NOVA redesign — routes / data / backend / database gap matrix

Status: **notes only — nothing in this document has been implemented.**
Branch: `feat/nova-chatbot-and-landing-integration`. No backend file or migration
has been added by the redesign work so far.

This records what each redesigned page needs, what the backend **already**
provides (verified against the code, not the mockups), and what would genuinely
have to be built. It exists so we add APIs and migrations **only where evidence
requires them** — the mockup screens are a visual reference, not a contract.

## Verified existing surface

**Routers** (endpoint counts): `strategies` 17, `strategy_instances` 16,
`engine` 12, `setup` 11, `personal_pine` 10, `control` 9, `pine_conversion` 9,
`orders` 8, `market` 7, `debug` 7, `dashboard` 6, `c2_tradingview` 6,
`tradingview_setup` 6, `live` 5, `admin`/`broker`/`payments`/`user_credentials` 3,
`positions`/`webhook`/`user_webhook`/`private_webhook` 1.

**Dashboard router**: `/api/dashboard/summary`, `/api/dashboard/portfolio`,
`/api/dashboard/live-flow`, `/api/dashboard/chat-feed`, `/api/logs`,
`/api/system/webhook-url`.

**DB models**: `User`, `UserSession`, `UserCredentialVault`, `UserRun`,
`AuditLog`, `WebhookEvent`, `WebhookNonce`, `UserRiskControl`,
`UserStrategyRiskControl`, `UserStrategyDailyRiskCounter`, `StrategySubscription`,
`UserEntitlement`, `PaymentEvent`, `StrategySignal`, `StrategyExecutionJob`,
`LiveOrderIntent`, `UserEgress`, `PortfolioTrade`, `StrategyCatalog`,
`StrategyVersion`, `StrategySourceArtifact`, `StrategyValidationReport`,
`StrategyAdminReview`, `PineConversionRequest`, `TradingViewCompileEvidence`.

## Per-route matrix

| Route | Needs | Backend today | Gap |
|---|---|---|---|
| `/app/trading` | runtime, chart, engine, position, quotes | **Exists** — engine/orders/market/positions routers + runtime status | none |
| `/app/dashboard` | KPIs, wallet, equity curve, daily P&L, CE/PE split, symbol breakdown, trades, open position | **Exists** — `/api/dashboard/portfolio` returns all of it | **per-strategy performance breakdown** is absent |
| `/app/strategies` | catalog, setup schema, saved setup, instances | **Exists** — `strategies` + `strategy_instances` | none for read |
| `/app/strategies/new` | Pine import, validation, conversion evidence | **Exists** — `personal_pine`, `pine_conversion`, `StrategyValidationReport`, `TradingViewCompileEvidence` | none for read; do **not** fake compilation |
| `/app/signals` | list of received alerts + routing outcome + latency | **Partial** — `StrategySignal`, `WebhookEvent` models exist; no list/filter endpoint | **new read endpoint** (paginated, filterable). Latency needs a stored ingest→fill duration |
| `/app/webhooks` | endpoints, delivery history, alert template | **Partial** — `user_webhook`, `/api/system/webhook-url`, `WebhookEvent` | **new delivery-list endpoint**. Secrets must stay non-revealable |
| `/app/credentials` | broker status, masked values, verification log | **Exists** — `user_credentials` (3), `UserCredentialVault`, pre-market health check | expose verification history read-only |
| `/app/risk` | limits + current usage | **Exists** — `UserRiskControl`, `UserStrategyRiskControl`, `UserStrategyDailyRiskCounter` | **read endpoint for usage-vs-limit**; in-flight edits need verified semantics first |
| `/app/reports` | daily sessions, monthly summary, export | **Partial** — `PortfolioTrade` + dashboard analytics | **session-level aggregation endpoint** + CSV/PDF export |
| `/app/automations` | user-editable routing rules & guardrails | **Missing as data** — guardrails exist in code (daily-loss cap, cooldown, square-off, stale-feed) but are **not** user-editable records | **largest gap**: needs a rule model + evaluation contract. High risk — do not ship editable safety automations without a safety review |
| `/app/settings` | account, notifications, preferences | **Partial** — session/runtime data exists | notification preferences have no store |

## Database changes that would be required

Only these are genuinely unbacked today:

1. **Automation rules** — new table(s) for user-defined routing/guardrail rules
   plus an execution/audit trail. Safety-critical; needs design review before any
   migration.
2. **Notification preferences** — small per-user settings table (or a JSON column
   on an existing user row).
3. **Signal latency** — if ingest→fill latency is to be displayed, it must be
   persisted on `StrategySignal` (or derived from `AuditLog`); confirm before
   adding a column.
4. **Report sessions** — daily session rollups can be *computed* from
   `PortfolioTrade`; only add a table if aggregation proves too slow.

Everything else on the redesign maps to existing models.

## Explicitly out of scope / must not be built from the mockups

The mockup screens imply behaviour the current architecture deliberately does not
allow. None of these may be implemented without a separate verified review:

- direct strategy activation that bypasses the setup flow
- webhook payload controlling strike / expiry / quantity
- exits that bypass the durable exit operation
- in-flight risk-limit changes without verified semantics
- fake Pine compilation results
- revealable webhook secrets
- multiple simultaneous positions
- editable safety automations

## Sequencing

1. Dashboard + Trading redesign on **existing** data (no new APIs).
2. Strategies + Signals — Signals needs the first genuinely new read endpoint.
3. Then, and only then, evaluate Automations/Reports/Settings writes with a
   safety review before any migration.

## Setup-conversation mockup (7-step wizard) — audit 2026-07-24

A mockup was supplied showing a `Setup` screen: top horizontal nav, a 7-step
rail, a chat transcript, and a live "CONFIGURATION" panel with a progress
percentage and an "Arm Engine (Paper)" CTA. The shipped chatbot was built from
the earlier flow and does **not** match it. Delta:

| Mockup element | Reality today | Gap |
| --- | --- | --- |
| Top nav: Dashboard / Trading / Strategies / **Setup** / Signals / Risk / Settings | Left sidebar, 11 routes, **no `/app/setup`** | nav model decision + a new route |
| 7-step numbered rail with tick marks | Machine has *phases*, not a fixed 7-step model | new derivation from the schema |
| Right-hand live "CONFIGURATION" cards | No such panel | new component; must project from the machine, not a second source |
| "Setup progress 71%" | Not modelled | derivable once steps are fixed |
| Step 1 Broker `Dhan ••••1097` | `UserCredentialVault` + masked display exist | read-only step, no new data |
| Step 2 `NOVA Supertrend v2.4.1` | Registry says `nova-supertrend`, version **1.0.0** | version must come from the API; the mockup string is fictional |
| Step 3 Mode Paper/Live | `MODE_SELECTION` exists in the machine | matches |
| Step 4 Sizing `1 lot · CE+PE · 75 qty` | `lots` + `direction` exist. Lot size resolves per instrument; `DEFAULT_NIFTY_LOT_SIZE = 65` | qty must come from instrument resolution, never hardcoded |
| Step 5 `₹25,000 · max 6 trades` | `UserRiskControl.max_loss_per_day_paise` / `max_orders_per_day` exist and are enforced — but in the **strategy fan-out** subsystem, not in `setup_schema` | wiring decision: setup writes risk controls |
| Step 6 Cooldown after a losing trade | **Does not exist anywhere.** The only cooldowns are the REST-fallback and rate-limiter ones | new column **and** new enforcement in the execution path — safety-critical |
| Step 7 Review & arm | `SETUP_REVIEW` / `ENGINE_READY` exist | matches |
| Mic / voice input | Not built | new |
| Missing from mockup: `stop_loss_percent`, `take_profit_percent` | Both are **required** fields in `_standard_setup_schema()` and drive real exits | do not drop them to match the mockup |

Rules that still apply: no setup-bypassing activation, and the mockup's own
promise ("nothing routes until you arm the engine") must remain literally true.
