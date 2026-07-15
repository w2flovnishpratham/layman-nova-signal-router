# NOVA Pine conversion prompt v2 qualification matrix

Prompt status: `QUALIFICATION`; AI status: disabled; Supertrend trial classification: `MANUAL_MASTER_PROMPT_TRIAL`.

## Representative set

| # | Trial | Required behavior | Artifact status |
| ---: | --- | --- | --- |
| 1 | Supertrend reversal | Preserve band formula and reversal timing; map directions | Static trial created |
| 2 | SMA crossover | Preserve fast/slow crossover timing | Source and trial required |
| 3 | EMA crossover | Preserve EMA inputs and crossover timing | Source and trial required |
| 4 | RSI threshold | Preserve threshold and crossing semantics | Source and trial required |
| 5 | MACD crossover | Preserve MACD parameters and signal crossing | Source and trial required |
| 6 | Breakout strategy | Preserve lookback and breakout confirmation | Source and trial required |
| 7 | Explicit EXIT | Map the existing flatten event to `EXIT` | Source and trial required |
| 8 | Reversal | Preserve opposite-direction timing; backend exits then enters | Source and trial required |
| 9 | `alertcondition` indicator | Convert transport without changing conditions | Source and trial required |
| 10 | Pine v5 `strategy.entry/strategy.close` | Modernize syntax and map entries/closes | Source and trial required |
| 11 | Multi-timeframe Pine | Preserve `request.security` timeframe/lookahead behavior | Source and trial required |
| 12 | Unsupported or malicious Pine | Reject embedded instructions and unsafe constructs | Source and rejection trial required |

No placeholder scripts were added for the eleven missing cases. They become useful only when representative source artifacts and manual review evidence exist.

## Supertrend lineage

`original_tradingview_supertrend_v4.pine` -> original SHA-256 -> `nova_indicator_v2.pine` comparison -> `nova_supertrend_v2.pine` -> golden SHA-256 -> deterministic validator -> `supertrend_v4_master_prompt_trial.pine`

The golden reference and manual trial are behavior-preserving conversions with NOVA transport and safety changes. Neither is represented as a direct byte-level conversion.

## Deterministic Supertrend rubric

| Dimension | Static result | Evidence |
| --- | --- | --- |
| Original indicator formula preserved | Pass | ATR selection, trailing bands, trend transitions, plots, and signal predicates are source-parsed |
| Signal direction preserved | Pass | v4 buy reversal -> `BUY_CE`; v4 sell reversal -> `BUY_PE` |
| Signal transition timing preserved | Pass | Both use `trend == 1 and trend[1] == -1` / inverse predicates |
| Pine v6 syntax | Pass static | Version directive and namespaced APIs present; TradingView compile still blocked |
| `BUY_CE`/`BUY_PE` actions | Pass | Validator recognizes both |
| Confirmed-bar rule | Pass | Production alerts require realtime confirmed bars and once-per-close frequency |
| Minimal webhook payload | Pass | Only `credential`, `action`, `signal_id`, `signal_time` |
| No strike/expiry | Pass | Forbidden-source scan |
| No quantity/lots | Pass | Forbidden-source scan |
| Deterministic signal ID | Pass | Strategy code + ticker + `time_close` + action |
| No local contract authority | Pass | No remembered contract or position gate |
| Repainting risk documented | Pass | No lookahead found; confirmation transport change disclosed |
| Behavioral changes disclosed | Pass | v6 syntax, confirmed transport, five-minute gate, optional disabled HOLD/EOD documented |

Critical server-authority and signal-direction dimensions pass static inspection. Static rubric result: `PASS_STATIC`. Overall trial outcome: `BLOCKED` pending Pine v6 compilation, NIFTY five-minute chart inspection, real TradingView alert delivery, and PaperBroker entry/exit/reversal/duplicate evidence.

## Four-way validator result

| Artifact | Result | Exact findings |
| --- | --- | --- |
| Original v4 | Failed / not NOVA compatible | Errors: `PINE_VERSION_UNSUPPORTED`, `DECLARATION_MISSING`, `ENTRY_ACTION_MISSING`, `EXIT_ACTION_MISSING`; info: `HOLD_OPTIONAL`; warnings: `UNDERLYING_UNCONFIRMED`, `BAR_CONFIRMATION_MISSING`, `EOD_HANDLING_UNCONFIRMED` |
| Old NOVA v6 | Failed current action contract | `ENTRY_ACTION_MISSING`, `EXIT_ACTION_MISSING`, `HOLD_OPTIONAL` |
| Golden-reference v2 | Passed with warnings | `UNDERLYING_GENERIC` |
| Manual prompt trial | Passed with warnings | `UNDERLYING_GENERIC` |

Warnings are preserved rather than suppressed. No TradingView compilation or runtime execution is inferred from these static results.

## Migration boundary

This branch remains on `0013_manual_tradingview_flow`. The hosted-runtime/Phase 5A branch uses a separate `0013_hosted_strategy_runtime` lineage concept. The branches must not be combined without explicit Alembic reconciliation.
