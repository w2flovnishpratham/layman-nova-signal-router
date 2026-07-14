# NOVA Pine Compatibility Contract v1

```text
NOVA_PINE_CONTRACT_VERSION=1
```

Passing this contract means only that deterministic static checks found the source compatible with NOVA's current private TradingView signal boundary. It does **not** mean TradingView compiled it, NOVA executed or backtested it, or that it is profitable, reliable, safe, or suitable for live trading.

## Supported source

- Pine v6 is supported.
- Pine v5 is accepted with an upgrade warning when the remaining rules pass.
- Earlier or missing versions are unsupported.
- Exactly one `indicator()` or `strategy()` declaration is required. A script title is required. `overlay=true` is recommended and omission is reported.
- Source is canonical UTF-8 text with LF line endings, no null bytes or unsupported control characters, at most 262,144 bytes by default, and bounded line length.

## Signal interface

The supported emitted actions are `BUY_CE`, `BUY_PE`, `EXIT`, and optional `HOLD`. A tradable script must emit at least one entry action and `EXIT`. Actions must occur in an `alert()` message or an `alertcondition()` message/title; words in comments or unrelated strings are not proof of an emitted action.

TradingView alert configuration supplies NOVA's JSON envelope:

```json
{"credential":"<PRIVATE_WEBHOOK_CREDENTIAL>","action":"BUY_CE","signal_id":"{{ticker}}-{{interval}}-{{time}}-BUY_CE","signal_time":"{{timenow}}"}
```

The public webhook URL contains no secret. Pine source must not contain credentials, access tokens, database URLs, Dhan secrets, TOTP seeds, or authentication headers.

NOVA derives owner, strategy instance, broker account, execution mode, lots, quantity, strike, expiry, security ID, trading symbol, position, event source, exit reason, order type, and product type. Pine must not emit or control those fields.

## Trading constraints

- Current compatibility is NIFTY-only, intraday, one-position, no scale-in.
- Hard-coded BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, BANKEX, equity, crypto, or forex execution is unsupported. Generic `syminfo` use is allowed but may receive an unable-to-determine warning.
- `pyramiding > 1`, intentional same-direction additions, independent legs, martingale sizing, portfolio logic, direct option selection, strike/expiry calculation for execution, multi-symbol execution, broker logic, or external execution URLs are unsupported.
- The TradingView chart controls timeframe. NOVA does not infer or change it.
- Entry-capable scripts require an EXIT signal. NOVA's mandatory server-side EOD and protective exits remain independent safety layers.

## Static risk guidance

The validator warns on `lookahead_on`, future-data patterns, missing confirmed-bar gating, intrabar/repeated alert frequency, and missing visible EOD behavior. These findings say “potential repainting risk” because static checks cannot prove runtime behavior.

Stable signal IDs must be unique per TradingView alert event. The recommended template combines ticker, interval, time, and action. Same-direction signals never imply NOVA scale-in.

## Lifecycle

Source versions are immutable. Corrections create a new version. Static validation reports are pinned to source hash, validator version, and this contract version. Admin approval acknowledges compatibility findings only and creates no hosted execution or live-trading authorization.
