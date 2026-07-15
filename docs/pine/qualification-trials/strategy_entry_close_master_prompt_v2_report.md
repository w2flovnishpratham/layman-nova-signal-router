# Strategy entry/close manual prompt v2 trial
1. Original behavior: Price/SMA crossings open long or short; separate 0.99/1.01 conditions close each side.
2. Intended action semantics: Long is `BUY_CE`, short is `BUY_PE`, and either close-only event is `EXIT`.
3. Preserved formulas: SMA and both close thresholds remain unchanged.
4. Preserved parameters: SMA length 20 and constants 0.99/1.01 remain unchanged.
5. Preserved signal timing: All four crossing predicates remain intact.
6. Pine syntax modernization: Pine v5 strategy calls became Pine v6 normalized intent alerts.
7. NOVA action mapping: Entries remain directional; close-long and close-short remain flatten-only.
8. Removed authority fields: Local TradingView position execution was removed.
9. Confirmed-bar or intrabar classification: `BAR_CLOSE`.
10. Repainting review: No lookahead or multi-timeframe request exists.
11. Multi-timeframe review: N/A.
12. Behavioral changes: Strategy simulation became confirmed NOVA transport.
13. Unsupported constructs: None found statically.
14. Assumptions requiring approval: One-position backend semantics are required.
15. Static-validation result: `PASSED_WITH_WARNINGS` (`HOLD_OPTIONAL`, `UNDERLYING_GENERIC`).
16. TradingView compilation status: `BLOCKED`.
17. Overall trial result: `PASS_STATIC`; TradingView, alert, and paper evidence remain blocked.
