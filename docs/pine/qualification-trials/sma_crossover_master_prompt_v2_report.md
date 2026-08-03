# SMA crossover manual prompt v2 trial
1. Original behavior: Fast SMA 9 crossing slow SMA 21 produces directional events.
2. Intended action semantics: Bullish crossover is `BUY_CE`; bearish crossover is `BUY_PE`.
3. Preserved formulas: `ta.sma` is preserved; no EMA, ATR, or Supertrend formula was added.
4. Preserved parameters: Fast 9 and slow 21 inputs are unchanged.
5. Preserved signal timing: `ta.crossover` and `ta.crossunder` are unchanged.
6. Pine syntax modernization: Pine v5 syntax was updated to v6 without formula changes.
7. NOVA action mapping: Bullish/bearish map to `BUY_CE`/`BUY_PE`; optional disabled EOD maps to `EXIT`.
8. Removed authority fields: None existed; none were added.
9. Confirmed-bar or intrabar classification: `BAR_CLOSE`.
10. Repainting review: No lookahead or multi-timeframe request exists.
11. Multi-timeframe review: N/A.
12. Behavioral changes: Confirmed transport and an off-by-default EOD EXIT were added and disclosed.
13. Unsupported constructs: None found statically.
14. Assumptions requiring approval: NIFTY chart use and optional EOD EXIT require setup review.
15. Static-validation result: `PASSED_WITH_WARNINGS` (`HOLD_OPTIONAL`, `UNDERLYING_GENERIC`).
16. TradingView compilation status: `BLOCKED`.
17. Overall trial result: `PASS_STATIC`; TradingView, alert, and paper evidence remain blocked.
