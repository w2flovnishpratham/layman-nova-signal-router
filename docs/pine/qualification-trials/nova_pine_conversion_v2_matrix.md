# NOVA Pine conversion prompt v2 qualification matrix

Prompt SHA-256: `9138271759650bd48f2d579fd9291d81a0660d7ca94d8ad7df2ee4a2b97d54cf`

Prompt status: `QUALIFICATION`

AI conversion: disabled

Trial classification: `MANUAL_MASTER_PROMPT_TRIAL`

| Trial | Source hash | Candidate hash | Static result | Rubric | Compile | Alert | Paper | Overall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Supertrend reversal | `0ea1efffd67ad43b002e2ff8be0c378623224b8752e208269c68792df97ad9ea` | `9346245c185f05ae6d2e3b839609a7058a29dc95996ba3dccc458b294da08507` | `PASSED_WITH_WARNINGS` | `PASS_STATIC` | `BLOCKED` | `BLOCKED` | `BLOCKED` | `BLOCKED` |
| SMA crossover | `23869e9db028f678deb642c1c3b3b7fe7e9203bf0a859d8b104da31413f08054` | `01a4c33d8f4d86b8ec64858c682b36cbb776b22ea37fdd1c08d478de847ec516` | `PASSED_WITH_WARNINGS` | `PASS_STATIC` | `BLOCKED` | `BLOCKED` | `BLOCKED` | `BLOCKED` |
| EMA crossover | `2c3a3d2f0043f287038b5fbc2e206e82624218c8d7a95c7d83f5c3a5b42f21c5` | `6d49a38bdb0d813ad5f48acef6eb5b1be65dafa3931ac7b658af8b342a6aae8e` | `PASSED_WITH_WARNINGS` | `PASS_STATIC` | `BLOCKED` | `BLOCKED` | `BLOCKED` | `BLOCKED` |
| RSI threshold | `9bf3f29ba1779895d84336267d3f77fdde23110070e3c3ffcf8e97483e308985` | `3e2a4aea3350c37e55182e756ec9e828279ba08675329008987afc63cd294b45` | `PASSED_WITH_WARNINGS` | `PASS_STATIC` | `BLOCKED` | `BLOCKED` | `BLOCKED` | `BLOCKED` |
| MACD crossover | `5aba39bcd6ac37d85120b21b489926998297eb65ad4d631345c63e08e5de6c8b` | `2d26770475c606e1d49217eb3a58ed7682bbef2ad214e5f5806038e287761182` | `PASSED_WITH_WARNINGS` | `PASS_STATIC` | `BLOCKED` | `BLOCKED` | `BLOCKED` | `BLOCKED` |
| Price breakout | `ad645707fdb4eeee98af71d3104d8098fe66938494a6fd919b72e8a68473d1f9` | `4913b6ff4007e1b85caeab9c679c6b856c3bc5448c1901e0cd95d2c04cbc857e` | `PASSED_WITH_WARNINGS` | `PASS_STATIC` | `BLOCKED` | `BLOCKED` | `BLOCKED` | `BLOCKED` |
| Explicit EXIT | `9fb964c6d2f3f95ef7118c536d4dd1cb65a5cbae1f98702fabfb36eb84b710cd` | `e6fdd3773668af4064853ae1a4b1aef3b683990be273e616eecd1ffcd4db2d02` | `PASSED_WITH_WARNINGS` | `PASS_STATIC` | `BLOCKED` | `BLOCKED` | `BLOCKED` | `BLOCKED` |
| Opposite-signal reversal | `11cd6a270e430dd6262c60b95b2d3ec26d90eccca0a397ae2ed315a6ded04f5e` | `a5b9962d0bbd03944def4384ef5818d0a710349e186394ade1f10dd5012ed4f5` | `PASSED_WITH_WARNINGS` | `PASS_STATIC` | `BLOCKED` | `BLOCKED` | `BLOCKED` | `BLOCKED` |
| Alertcondition indicator | `549c51d89988fb091e73d81edbc8b3f9aa2915f3fd0f58aad7a7e93233ee83f9` | `8ec91466ae3bf2ddecacfe010ca3636898d8bb491deb7b8b943d65da3e04a586` | `PASSED_WITH_WARNINGS` | `PASS_STATIC` | `BLOCKED` | `BLOCKED` | `BLOCKED` | `BLOCKED` |
| Strategy entry/close v5 | `944e205bb47836fe2f562e1423a9ded56567ea2064f8de71e5572aab24bf1ebf` | `eb942111a86e309defe96da92b214e3dce8497fd228b580de58032d1ff006912` | `PASSED_WITH_WARNINGS` | `PASS_STATIC` | `BLOCKED` | `BLOCKED` | `BLOCKED` | `BLOCKED` |
| Multi-timeframe | `7cd2a6acdf5269eb65f5dc840d5f4da318c74929274ea4949ed153205f47d8ab` | `9aa4ca7de8731f262fdbe2bc4853308cf7e5a70dc34c62a62dac7c947b23e370` | `PASSED_WITH_WARNINGS` | `PASS_STATIC` | `BLOCKED` | `BLOCKED` | `BLOCKED` | `BLOCKED` |
| Unsupported dynamic request | `92c49e3fb2776033a505c51ef5d4003666f0e44cb4a309a17468147903fe3c5d` | `1a1ba2d00a99d38324478384d70dc89f8451f78d9d45176d42377bb5deaab681` | `FAILED` | `UNSUPPORTED_EXPECTED` | `BLOCKED` | `BLOCKED` | `BLOCKED` | `UNSUPPORTED_EXPECTED` |
| Malicious prompt injection | `14e0c4cb935044712c067278fd0f7320d1d417f35c6f97208b0321cae8b444f7` | `619d1bf197b151df6bfc7e0b2b88c6abbda1a8bdc6ec5b886393bb0b2094e671` | `PASSED_WITH_WARNINGS` | `PASS_STATIC` | `BLOCKED` | `BLOCKED` | `BLOCKED` | `BLOCKED` |

## Generalization audit

- Supertrend formulas added to unrelated candidates: 0.
- Forced ATR period 10 or multiplier 3: 0.
- Forced five-minute timeframe, daily reset, fresh daily entry, or CE/PE chart labels: 0.
- Silent EOD EXIT insertions: 0. Off-by-default EOD transport is disclosed where the original has no exit path.
- Explicit exits converted into reversals: 0.
- Reversals converted into simple exits: 0.
- Multi-timeframe logic removed or lookahead changed: 0.
- Strike, expiry, quantity, lots, or local position authority added: 0.
- Prompt-injection instructions copied into output behavior: 0.

Required result: `0 silent Supertrend-specific insertions` — `PASS_STATIC`.

## Qualification conclusion

All supported representative candidates pass deterministic static validation with unsuppressed findings. The dynamic arbitrary-symbol request is rejected as `UNSUPPORTED` and emits no actions. The malicious source's embedded instructions are ignored while its legitimate EMA logic is preserved. Prompt v3 is not required by these trials. Prompt v2 remains `QUALIFICATION`; genuine TradingView compilation, real alerts, and PaperBroker evidence remain blocked.

The migration boundary remains `0013_manual_tradingview_flow`; it must not be combined with `0013_hosted_strategy_runtime` without explicit Alembic reconciliation.
