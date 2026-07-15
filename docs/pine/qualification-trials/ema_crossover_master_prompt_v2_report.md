# EMA crossover manual prompt v2 trial
1. Original behavior: Fast EMA 12 crossing slow EMA 26 produces directional events.
2. Intended action semantics: Bullish crossover is `BUY_CE`; bearish crossover is `BUY_PE`.
3. Preserved formulas: Both `ta.ema` calculations remain EMA; no SMA, ATR, or Supertrend logic was added.
4. Preserved parameters: Fast 12 and slow 26 inputs are unchanged.
5. Preserved signal timing: Crossover and crossunder predicates are unchanged.
6. Pine syntax modernization: Source was already Pine v6; no syntax rewrite was needed.
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
