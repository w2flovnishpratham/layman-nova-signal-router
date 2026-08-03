# Supertrend v4 canonical-prompt manual trial report

## 1. Converted Pine v6 candidate

Artifact: `supertrend_v4_master_prompt_trial.pine`; classification: `BEHAVIOR_PRESERVED_WITH_APPROVED_TRANSPORT_CHANGES`.

## 2. Strategy behavior summary

The indicator trails ATR-derived upper and lower bands. A transition from bearish to bullish is the buy event; the inverse transition is the sell event.

## 3. Preserved logic

ATR period, `hl2` source default, multiplier, alternative ATR selection, band continuation, trend transition predicates, reversal signals, plots, signal markers, and highlighting are retained.

## 4. Syntax changes

`study` became `indicator`; typed v6 inputs replaced v4 inputs; `sma`, `atr`, and `tr` use `ta`; `max`/`min` use `math`; timestamp formatting uses `str.format_time`. The invisible middle plot uses linewidth 1 because v6 does not accept the original zero-width declaration.

## 5. NOVA action mapping

Bullish reversal maps to `BUY_CE`. Bearish reversal maps to `BUY_PE`. Optional EOD flatten maps to `EXIT`. Deliberate connectivity testing maps to `HOLD`.

## 6. Removed authority-bearing fields

The candidate contains no strike, expiry, security ID, broker identity, lots, quantity, execution mode, order type, product type, risk parameter, access token, or TOTP field.

## 7. Repainting review

The original contains no lookahead or multi-timeframe request. Signal predicates use closed-series transitions. Production transport additionally requires a confirmed realtime bar; TradingView inspection is still required.

## 8. Bar-close or intrabar classification

Production alert transport is bar-close only: `barstate.isrealtime and barstate.isconfirmed` with `alert.freq_once_per_bar_close`. Historical and forming bars cannot produce production webhook alerts.

## 9. Behavioral changes

NOVA transport, credential-placeholder blocking, deterministic IDs, a five-minute alert gate, optional one-shot HOLD, and optional disabled EOD EXIT are added. No daily reset or fresh daily entry was added. Visual signal behavior remains independent of production alert eligibility.

## 10. Unsupported constructs

None identified statically. Pine v6 compilation remains unverified.

## 11. Assumptions requiring approval

The manual trial inputs specify NIFTY five-minute use, bullish -> `BUY_CE`, bearish -> `BUY_PE`, no required intrabar transport, no daily reset, and optional disabled EOD EXIT. These inputs and the transparent-middle-plot modernization require human confirmation in TradingView.

## 12. TradingView compilation checklist

- [ ] Pine v6 compiles.
- [ ] Script loads on a NIFTY five-minute chart.
- [ ] Historical plots and reversal markers match the original.
- [ ] No historical production alerts fire.

## 13. NOVA static-validation checklist

- [x] Required normalized actions detected.
- [x] Confirmed-bar guard detected.
- [x] No server-authority payload fields detected.
- [x] Only `UNDERLYING_GENERIC` remains as a warning.

## 14. Alert-template checklist

- [ ] Real `BUY_CE` JSON delivered and authenticated.
- [ ] Real `BUY_PE` JSON delivered and authenticated.
- [ ] Optional `EXIT` JSON delivered when enabled.
- [ ] Deliberate `HOLD` creates audit evidence and no execution job.
- [ ] Duplicate event reuses one deterministic signal ID.
