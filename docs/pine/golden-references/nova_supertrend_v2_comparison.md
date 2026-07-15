# NOVA Supertrend v2 golden-reference comparison

Status: `QUALIFICATION`. Static inspection is not a TradingView compilation test.

## Source evidence

- Current NOVA source: [`nova_indicator_v2.pine`](../../../nova_indicator_v2.pine), SHA-256 `7595fe7e20e63f8a03eb4d95cdc7442415d34602426d4e32328dc03f4a223c7f`.
- Original TradingView Pine v4 file: [`original_tradingview_supertrend_v4.pine`](original_tradingview_supertrend_v4.pine), SHA-256 `0ea1efffd67ad43b002e2ff8be0c378623224b8752e208269c68792df97ad9ea`, classified `EXTERNAL_REFERENCE_SOURCE`.
- Phase 4D history: the artifact was unavailable to that repository run and was recorded as `SOURCE_NOT_SUPPLIED`. Phase 4E resolves that blocker without erasing it from the qualification record.
- Original formula baseline: the registered v4 ATR, band continuation, trend transition, reversal, plots, and highlighting. The corrected default preserves that behavior while adding approved NOVA transport and safety changes; it is not a byte-level conversion.
- Corrected candidate hash: pinned in `nova_supertrend_v2_qualification.json`.

## Comparison

| Area | Original TV Supertrend | Old NOVA Supertrend | Corrected NOVA v2 |
| --- | --- | --- | --- |
| Pine version | v4 | v6 | v6 |
| Strategy formula | Original ATR/band reversal | Daily reset and fresh entry enabled by default | Original preserved by default |
| Bullish mapping | Buy | CE entry | `BUY_CE` |
| Bearish mapping | Sell | PE entry | `BUY_PE` |
| Webhook credential | None | Old `secret` field | Current `credential` body field |
| Strike | None | Pine-calculated ATM | Backend controlled |
| Expiry | None | Pine-calculated/manual | Backend controlled |
| Quantity/lots | None | Pine input | Backend controlled |
| Bar confirmation | Alert condition | Confirmed realtime | Confirmed realtime five-minute bar |
| EOD | None | Enabled Pine EOD | Optional and disabled by default |
| Position authority | Not applicable | Local Pine variables | Backend JSON authority |
| SL/TP | None | S/R values sent by Pine | Backend controlled |

## Exact behavior

With both NOVA extension defaults off, bullish signals occur only when `trend` changes from `-1` to `1`; bearish signals occur only when it changes from `1` to `-1`. ATR period, source, multiplier, built-in/alternative ATR selection, trailing bands, plots, markers, and highlighting remain configurable.

`Reset Supertrend each IST day` is off by default. Enabling it restarts the bands and selects the day's initial trend from `close >= source`. `Fresh entry after daily reset` is also off and has an effect only when reset is enabled; enabling both may add one direction signal at the daily boundary. These are explicit NOVA extensions, not original behavior.

The optional EOD `EXIT` is off by default. When enabled it uses the confirmed candle close in `Asia/Kolkata`, emits at most once per IST date, and does not consult local Pine position state. It is a duplicate safety signal only; backend mandatory square-off remains authoritative.

Production alerts require `barstate.isrealtime`, `barstate.isconfirmed`, a five-minute chart, and a non-placeholder `nwk_` credential. Historical and forming bars cannot send production alerts. The orange chart background warns on a non-five-minute chart; the red background warns when the credential is not configured. Neither warning displays the credential.

Signal IDs use `NOVA-ST-V2:<ticker>:<time_close>:<action>`. Retries of one confirmed event reproduce the same ID, while actions at the same timestamp remain distinct. The value contains no user identifier or credential and remains below the backend's 128-character limit for ordinary TradingView ticker symbols.

## Removed client authority

The corrected source contains no strike/ATM calculation, expiry calculation or input, quantity/lots, security ID, broker/client identity, exchange segment, instrument type, order/product type, paper/live mode, access token, TOTP, remembered option contract, or local position gate. `EXIT` asks the backend to flatten its authoritative position for the strategy instance.

## Static validation

The repository validator result and findings are asserted by `backend/app/tests/test_supertrend_golden_reference.py`. Pine v6 compilation, TradingView chart loading, and real TradingView alert delivery remain `BLOCKED` until tested in TradingView.
