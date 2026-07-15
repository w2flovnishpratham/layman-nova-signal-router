# Price breakout manual prompt v2 trial
1. Original behavior: Close crossing the prior 20-bar high/low produces directional breakouts.
2. Intended action semantics: Upside breakout is `BUY_CE`; downside breakout is `BUY_PE`.
3. Preserved formulas: `ta.highest(high[1], 20)` and `ta.lowest(low[1], 20)` remain intact.
4. Preserved parameters: Lookback 20 remains configurable.
5. Preserved signal timing: Current-bar exclusion and crossover timing are unchanged.
6. Pine syntax modernization: Source was already Pine v6.
7. NOVA action mapping: Breakouts map directionally; optional disabled EOD maps to `EXIT`.
8. Removed authority fields: None existed; none were added.
9. Confirmed-bar or intrabar classification: `BAR_CLOSE`.
10. Repainting review: Prior-range indexing excludes the current bar; no lookahead exists.
11. Multi-timeframe review: N/A.
12. Behavioral changes: Confirmed transport and an off-by-default EOD EXIT were added and disclosed.
13. Unsupported constructs: None found statically.
14. Assumptions requiring approval: NIFTY use and optional EOD EXIT require review.
15. Static-validation result: `PASSED_WITH_WARNINGS` (`HOLD_OPTIONAL`, `UNDERLYING_GENERIC`).
16. TradingView compilation status: `BLOCKED`.
17. Overall trial result: `PASS_STATIC`; TradingView, alert, and paper evidence remain blocked.
