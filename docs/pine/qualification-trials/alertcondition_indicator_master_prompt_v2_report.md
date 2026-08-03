# Alertcondition indicator manual prompt v2 trial
1. Original behavior: Price recovering above the lower channel or rejecting below the upper channel emits indicator alerts.
2. Intended action semantics: Lower recovery is `BUY_CE`; upper rejection is `BUY_PE`.
3. Preserved formulas: SMA channel and standard-deviation bands remain unchanged.
4. Preserved parameters: Channel length 20 remains unchanged.
5. Preserved signal timing: Existing crossover/crossunder alert predicates remain intact.
6. Pine syntax modernization: Pine v5 was updated to v6; no strategy entries were invented.
7. NOVA action mapping: Existing indicator events map directionally; optional disabled EOD maps to `EXIT`.
8. Removed authority fields: None existed; none were added.
9. Confirmed-bar or intrabar classification: `BAR_CLOSE`.
10. Repainting review: No lookahead or multi-timeframe request exists.
11. Multi-timeframe review: N/A.
12. Behavioral changes: Confirmed NOVA transport and an off-by-default EOD EXIT were added and disclosed.
13. Unsupported constructs: None found statically.
14. Assumptions requiring approval: Event direction and optional EOD EXIT require review.
15. Static-validation result: `PASSED_WITH_WARNINGS` (`HOLD_OPTIONAL`, `UNDERLYING_GENERIC`).
16. TradingView compilation status: `BLOCKED`.
17. Overall trial result: `PASS_STATIC`; TradingView, alert, and paper evidence remain blocked.
