# Explicit EXIT strategy manual prompt v2 trial
1. Original behavior: SMA crossover opens long; RSI crossing below 50 closes long.
2. Intended action semantics: Long entry is `BUY_CE`; the close-only condition is `EXIT`, never `BUY_PE`.
3. Preserved formulas: SMA entry and RSI exit calculations remain unchanged.
4. Preserved parameters: SMA 10/30 and RSI 14 remain unchanged.
5. Preserved signal timing: Both original crossing predicates remain intact.
6. Pine syntax modernization: Pine v5 strategy syntax became a Pine v6 indicator transport.
7. NOVA action mapping: Entry maps to `BUY_CE`; explicit close maps to `EXIT`.
8. Removed authority fields: Local strategy position execution was replaced by intent transport.
9. Confirmed-bar or intrabar classification: `BAR_CLOSE`.
10. Repainting review: No lookahead or multi-timeframe request exists.
11. Multi-timeframe review: N/A.
12. Behavioral changes: TradingView order simulation was removed; confirmed NOVA transport was added.
13. Unsupported constructs: None found statically.
14. Assumptions requiring approval: The source close condition is flatten-only, not bearish entry.
15. Static-validation result: `PASSED_WITH_WARNINGS` (`HOLD_OPTIONAL`, `UNDERLYING_GENERIC`).
16. TradingView compilation status: `BLOCKED`.
17. Overall trial result: `PASS_STATIC`; TradingView, alert, and paper evidence remain blocked.
