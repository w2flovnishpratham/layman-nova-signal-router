# RSI threshold manual prompt v2 trial
1. Original behavior: RSI crosses above 30 for bullish intent and below 70 for bearish intent.
2. Intended action semantics: Recovery above the lower threshold is `BUY_CE`; rejection below the upper threshold is `BUY_PE`.
3. Preserved formulas: `ta.rsi(close, 14)` remains unchanged.
4. Preserved parameters: Length 14 and thresholds 30/70 remain inputs.
5. Preserved signal timing: Threshold crossings remain crossing events, not ordinary comparisons.
6. Pine syntax modernization: Pine v5 was updated to v6.
7. NOVA action mapping: Threshold events map directionally; optional disabled EOD maps to `EXIT`.
8. Removed authority fields: None existed; none were added.
9. Confirmed-bar or intrabar classification: `BAR_CLOSE`.
10. Repainting review: No lookahead or multi-timeframe request exists.
11. Multi-timeframe review: N/A.
12. Behavioral changes: Confirmed transport and an off-by-default EOD EXIT were added and disclosed.
13. Unsupported constructs: None found statically.
14. Assumptions requiring approval: Direction semantics, NIFTY use, and optional EOD EXIT require review.
15. Static-validation result: `PASSED_WITH_WARNINGS` (`OVERLAY_UNCONFIRMED`, `HOLD_OPTIONAL`, `UNDERLYING_GENERIC`).
16. TradingView compilation status: `BLOCKED`.
17. Overall trial result: `PASS_STATIC`; TradingView, alert, and paper evidence remain blocked.
