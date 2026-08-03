# MACD crossover manual prompt v2 trial
1. Original behavior: MACD line crossing its signal line produces directional events.
2. Intended action semantics: Bullish MACD crossover is `BUY_CE`; bearish is `BUY_PE`.
3. Preserved formulas: `ta.macd` and its MACD, signal, and histogram outputs remain intact.
4. Preserved parameters: Fast 12, slow 26, and signal 9 are unchanged.
5. Preserved signal timing: Line crossover and crossunder predicates are unchanged.
6. Pine syntax modernization: Pine v5 was updated to v6.
7. NOVA action mapping: MACD events map directionally; optional disabled EOD maps to `EXIT`.
8. Removed authority fields: None existed; none were added.
9. Confirmed-bar or intrabar classification: `BAR_CLOSE`.
10. Repainting review: No lookahead or multi-timeframe request exists.
11. Multi-timeframe review: N/A.
12. Behavioral changes: Confirmed transport and an off-by-default EOD EXIT were added and disclosed.
13. Unsupported constructs: None found statically.
14. Assumptions requiring approval: NIFTY use and optional EOD EXIT require review.
15. Static-validation result: `PASSED_WITH_WARNINGS` (`OVERLAY_UNCONFIRMED`, `HOLD_OPTIONAL`, `UNDERLYING_GENERIC`).
16. TradingView compilation status: `BLOCKED`.
17. Overall trial result: `PASS_STATIC`; TradingView, alert, and paper evidence remain blocked.
