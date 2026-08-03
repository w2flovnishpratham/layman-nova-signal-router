# Reversal strategy manual prompt v2 trial
1. Original behavior: Opposite EMA crossovers reverse long and short direction.
2. Intended action semantics: Bullish direction is `BUY_CE`; bearish direction is `BUY_PE`.
3. Preserved formulas: EMA 8/34 calculations remain unchanged.
4. Preserved parameters: Fast 8 and slow 34 are unchanged.
5. Preserved signal timing: Opposite crossover events remain intact.
6. Pine syntax modernization: The Pine v6 strategy became a Pine v6 intent indicator.
7. NOVA action mapping: Direction maps to `BUY_CE`/`BUY_PE`; backend performs exit-then-enter.
8. Removed authority fields: Pine strategy position sequencing was removed.
9. Confirmed-bar or intrabar classification: `BAR_CLOSE`.
10. Repainting review: No lookahead or multi-timeframe request exists.
11. Multi-timeframe review: N/A.
12. Behavioral changes: Confirmed transport and an off-by-default EOD EXIT were added and disclosed.
13. Unsupported constructs: None found statically.
14. Assumptions requiring approval: Backend reversal semantics and optional EOD EXIT require review.
15. Static-validation result: `PASSED_WITH_WARNINGS` (`HOLD_OPTIONAL`, `UNDERLYING_GENERIC`).
16. TradingView compilation status: `BLOCKED`.
17. Overall trial result: `PASS_STATIC`; TradingView, alert, and paper evidence remain blocked.
