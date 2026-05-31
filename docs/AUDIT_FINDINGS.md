# NOVA Audit Findings - Rolling Tracker

Living checklist of every gap surfaced in the 2026-05-30 audit. Mark items as fixed when shipped; link the commit SHA in the notes column.

Severity: Critical / High / Medium / Low

---

## Backend - Order / Trading

| ID | Sev | Item | Status | Notes |
|---|---|---|---|---|
| C1 | Critical | EOD auto-square-off at 15:15 IST | Done | Scheduled task; flatten all open positions |
| C2 | Critical | Manual entries invisible to dashboard | Done | Ghost-position watcher persists broker-only Dhan exposure and surfaces it in Dashboard/Positions |
| C3 | Critical | Manual exits leave stale open state | Done | Ghost-position watcher clears stale local tracker when NOVA position disappears from Dhan |
| C4 | Critical | Manual SL/TP modifications undetected | Done | Ghost watcher compares Dhan Super Order SL/TP legs vs NOVA recorded levels |
| C5 | Critical | Dhan API timeout mid-order = unknown state | Done | Timeout recovery checks Dhan order book by correlationId; unresolved timeouts return ORDER_STATE_UNKNOWN and require manual broker verification |
| C6 | Critical | Backend restart with open position = blind | Done | Startup reconciler verifies tracked position against Dhan, clears stale local state, and re-adopts visible Super Order legs |
| C7 | Critical | Seen-signals file grows forever | Done | 24h TTL, prune on access |
| C8 | Critical | Multi-process state writes unsafe | Pending | Single-worker enforced, or migrate to SQLite |
| C9 | Critical | Concurrent signals -> straddle race | Done | Per-strategy mutex serializes webhook routing for same strategy_code |
| C10 | Critical | Partial fills not handled | Done | Poll reads filled_qty; partial entries/exits adjust tracked quantity and log warnings |
| C11 | Critical | Settings race during signal processing | Done | Snapshot runtime settings into signal context |
| H1 | Medium | Token expires mid-session | Done | `route_signal` blocks expired REAL-mode Dhan tokens before broker calls; dashboard shows expired-token banner |
| H2 | High | Webhook secret no HMAC / freshness | Pending | HMAC signature, 60s timestamp window, nonce |
| H13 | High | OCO race between SL and TP | Pending | Use Dhan postback URL for order updates |
| H14 | High | WebSocket LTP reconnect untested | Pending | Simulate disconnect, verify REST fallback |
| M2 | Medium | No margin / funds pre-check | Pending | Local check before broker round-trip |
| M5 | Medium | Dhan rate limits not respected | Pending | Token-bucket on client calls |

## Backend - Forms / Setup Endpoints

| ID | Sev | Item | Status | Notes |
|---|---|---|---|---|
| H3 | High | No rate limit on /setup/dhan/* | Pending | Per-IP throttle, 5/5min |
| H4 | High | connect_dhan partial-state failure | Pending | Transactional rollback |
| H5 | High | Risk settings business-rule gaps | Pending | Validators: SL 0-80%, TP 0-500%, qty >= lot size |
| H7 | High | Destructive POSTs no confirmation | Pending | Wire ConfirmModal to all /control/* dangerous endpoints |
| H8 | High | Concurrent settings edits | Pending | Optimistic locking via version field |
| H9 | High | Backend restart during form submit | Pending | Graceful shutdown flushes state |
| H10 | Medium | Webhook secret strength | Done | Min length 16 + entropy check in setup, readiness, and webhook routing |
| H11 | High | No CSRF on POSTs | Pending | Add when auth lands |
| H12 | Medium | Scrip master refresh blocks UI 30s | Done | POST starts async refresh job; status endpoint exposes `refresh_job` for polling |
| M6 | Medium | Pydantic errors leak schema | Pending | Sanitize via custom handler |
| M8 | Medium | Failed auth attempts lack IP/UA | Pending | Include in audit metadata |
| M9 | Medium | /debug/* in production | Pending | Gate behind APP_ENV != prod |

## Backend - Ops / Reliability

| ID | Sev | Item | Status | Notes |
|---|---|---|---|---|
| M1 | Medium | No log rotation | Pending | Size-based, drop oldest |
| M3 | Medium | No critical-event notifier | Pending | Slack webhook for kill switch, token expiry, exit failures |
| M4 | Medium | Audit log write failures silent | Pending | Surface to dashboard |

## Frontend - Critical Reliability

| ID | Sev | Item | Status | Notes |
|---|---|---|---|---|
| FE-C1 | Critical | No `.catch()` on polling: Logs/Orders/Positions/LiveFlow | Done | Add catch + toast.error per page |
| FE-C2 | Critical | Dashboard polls 2s; status polls 5s | Done | Raise to 5s and 10s |
| FE-C3 | Critical | Polling continues on backgrounded tabs | Done | useVisibilityAwarePolling hook |
| FE-C4 | Critical | No request cancellation on navigation | Pending | AbortController in fetches |
| FE-C5 | Critical | No global ErrorBoundary | Done | New component, wrap Routes |
| FE-C6 | Critical | Axios has no global timeout | Done | 10s timeout |
| FE-C7 | Critical | No 401 interceptor | Done | Redirect to /app/setup with banner |

## Frontend - UX / Polish

| ID | Sev | Item | Status | Notes |
|---|---|---|---|---|
| FE-H1 | High | Setup form busy is single string (race) | Pending | Per-button busy flag |
| FE-H2 | High | No request deduplication | Pending | Cache in-flight promises by URL |
| FE-H3 | High | ConfirmModal closes on backdrop click | Pending | Disable for destructive variants |
| FE-H4 | High | ConfirmModal: no focus trap / Esc-to-close | Pending | Add keyboard handling |
| FE-H5 | High | Toast position covered by mobile nav | Pending | Move toast above nav on mobile |
| FE-H6 | High | No accessibility labels | Pending | Add aria-* across pages |
| FE-H7 | High | LandingPage timers fire on unmount | Pending | Clean up in useEffect return |
| FE-H8 | High | No loud offline banner | Pending | Full-width red banner when backend unreachable |
| FE-H9 | High | No version/build display | Pending | Show git short SHA in topbar |
| FE-H10 | High | No emergency keyboard shortcut | Pending | Ctrl+Shift+E = emergency stop |
| FE-H11 | High | Form state may get clobbered by polling | Pending | Verify pollStatus only writes status, not form fields |
| FE-H12 | High | autoComplete on credential inputs | Pending | Confirm SecretInput sets autoComplete="off" |
| FE-M1 | Medium | No loading skeletons | Pending | Add skeleton component |
| FE-M2 | Medium | No request batching | Pending | Combine dashboard's 5 calls |
| FE-M3 | Medium | No stale data indicator | Pending | Show last-sync timestamp |
| FE-M4 | Medium | Mobile bottom-bar discoverability | Pending | Surface Settings/Controls more prominently |
| FE-M5 | Medium | No CSP / X-Frame-Options | Pending | Set at nginx layer |

## Project / Ops

| ID | Sev | Item | Status | Notes |
|---|---|---|---|---|
| P1 | Critical | No frontend tests | Pending | Vitest + React Testing Library scaffold |
| P2 | Critical | No CI/CD | Pending | GitHub Actions: pytest, npm build, lint |
| P3 | Critical | No state file backup | Pending | Nightly tar to off-VPS storage |
| P4 | Critical | No uptime monitoring | Pending | UptimeRobot / BetterStack on /health |
| P5 | High | No Docker setup | Pending | docker-compose for backend + nginx |
| P6 | High | No systemd service file | Pending | nova-router.service in repo |
| P7 | High | Docs drift | Pending | Re-read existing docs, mark stale |
| P8 | High | No lint/format config | Pending | ruff + prettier + .editorconfig |
| P9 | Medium | outputs/ scratchpad in workspace | Pending | Move to research/ subdirectory |
| P10 | Medium | No env var validation at startup | Pending | Fail fast on missing critical vars |
| P11 | Medium | No frontend README | Pending | Brief dev/build/env doc |
| P12 | High | Windows env breaks pytest (Defender locks tmp_path) | Pending | Add --basetemp to pyproject.toml; add Defender exclusion for repo + C:/pytest-tmp |
| P13 | High | OneDrive sync causes git index.lock races | Pending | Exclude repo folder from OneDrive sync |

## Ghost Position Detector

| ID | Sev | Item | Status | Notes |
|---|---|---|---|---|
| GP1 | Critical | Background worker polls Dhan positions every 30s | Done | `workers/ghost_position_watcher.py`, wired in app lifespan |
| GP2 | Critical | Persist external_positions.json | Done | `state_store` helpers read/write `external_positions.json` |
| GP3 | Critical | Dashboard surfaces external positions | Done | Dashboard warning + Positions broker-only exposure table |
| GP4 | Critical | SL/TP drift detection | Done | Dashboard/Positions warning when Dhan broker-side SL/TP drifts from NOVA state |

---

## Progress Log

Most recent fixes at the top.

| Date | ID(s) | Commit | Notes |
|---|---|---|---|
| 2026-05-31 | C5 | _(pending push)_ | Dhan order and Super Order timeouts recover matching order-book rows by correlationId; unresolved timeouts surface ORDER_STATE_UNKNOWN |
| 2026-05-31 | C6 | _(pending push)_ | Startup reconciler verifies persisted open position against Dhan before monitors start; clears stale local state or re-adopts visible Super Order legs |
| 2026-05-31 | H12 | _(pending push)_ | Scrip master refresh now starts async job and status endpoint exposes `refresh_job` |
| 2026-05-31 | H10 | _(pending push)_ | Webhook secret min length and entropy validation across setup, readiness, and routing |
| 2026-05-31 | H1 | _(pending push)_ | Expired Dhan token blocks REAL-mode signals before broker calls |
| 2026-05-31 | C10 | _(pending push)_ | Dhan order status parser reads filled/remaining quantity; partial entry tracks only filled qty and partial exit leaves remaining qty open |
| 2026-05-31 | C9 | _(pending push)_ | Webhook now serializes `route_signal` per strategy_code so different signal_ids for the same strategy cannot race through position/risk checks |
| 2026-05-31 | C4, GP4 | _(pending push)_ | SL/TP drift detection compares Dhan Super Order order-book legs against NOVA recorded broker SL/TP and surfaces drift warnings in Dashboard + Positions |
| 2026-05-31 | C2, C3, GP1, GP2, GP3 | _(pending push)_ | Ghost-position watcher polls Dhan read-only broker state, persists `external_positions.json`, clears stale local tracker on manual broker exit, and surfaces broker-only exposure in Dashboard + Positions UI |
| 2026-05-30 | C1, C7, C11 | _(pending push)_ | Backend critical: seen-signals 24h TTL, settings snapshot per signal, EOD square-off worker at 15:15 IST |
| 2026-05-30 | FE-C1, FE-C2, FE-C3, FE-C5, FE-C6, FE-C7 | _(pending push)_ | Frontend critical: axios timeout+401, polling catch handling, ErrorBoundary, visibility-aware polling hook, interval rationalisation |
