# Multi-timeframe manual prompt v2 trial
1. Original behavior: Price crossing a 20-period EMA from a configurable 60-minute series produces direction.
2. Intended action semantics: Cross above is `BUY_CE`; cross below is `BUY_PE`.
3. Preserved formulas: The higher-timeframe EMA expression remains unchanged.
4. Preserved parameters: Timeframe `60` and EMA length 20 remain configurable.
5. Preserved signal timing: Crossings against the requested series remain intact.
6. Pine syntax modernization: Pine v5 was updated to v6.
7. NOVA action mapping: MTF direction maps to `BUY_CE`/`BUY_PE`; optional disabled EOD maps to `EXIT`.
8. Removed authority fields: None existed; none were added.
9. Confirmed-bar or intrabar classification: `MULTI_TIMEFRAME_BAR_CLOSE`.
10. Repainting review: `barmerge.lookahead_off` is preserved; higher-timeframe confirmation still requires TradingView review.
11. Multi-timeframe review: Symbol context, timeframe input, request expression, and lookahead setting are preserved.
12. Behavioral changes: Confirmed outer-chart transport and an off-by-default EOD EXIT were added and disclosed.
13. Unsupported constructs: No dynamic symbol or lookahead-on construct exists.
14. Assumptions requiring approval: Higher-timeframe bar merge timing requires visual comparison.
15. Static-validation result: `PASSED_WITH_WARNINGS` (`HOLD_OPTIONAL`, `UNDERLYING_GENERIC`).
16. TradingView compilation status: `BLOCKED`.
17. Overall trial result: `PASS_STATIC`; TradingView, alert, and paper evidence remain blocked.
