# DhanHQ v2 Compliance & Production-Readiness Audit

> Historical audit: this document predates Stages 0-4. The current deployment
> is approved only for an authenticated multi-user Paper beta.
> `ENABLE_LIVE_ORDERS=false` must remain enforced. Use
> `PRODUCTION_RUNBOOK.md` for current operations.

**Project:** NOVA Signal Router  
**Date:** 2026-05-25  
**Scope:** Full DhanHQ v2 API compliance, safety, deployment readiness for TradingView → Dhan order execution.

---

## 1. Authentication & Token Handling

| Item | Status | Notes |
|------|--------|-------|
| Dhan access-token header | ✅ COMPLIANT | `access-token: <token>` sent on every request |
| client-id header | ✅ COMPLIANT | `client-id: <id>` sent as compatibility metadata (not required by Dhan official docs; `dhanClientId` is in the order body) |
| Token stored encrypted (Fernet) | ✅ COMPLIANT | AES-128 CBC via `cryptography.fernet` |
| Token never logged in full | ✅ COMPLIANT | `mask_secret()` masks all log output |
| Token never returned to frontend | ✅ COMPLIANT | `access_token_present: bool` only |
| Token never in Vercel env | ✅ COMPLIANT | UI → backend POST only |
| Token saved_at timestamp stored | ✅ COMPLIANT | `connected_at` in vault |
| Token age metadata returned | ✅ COMPLIANT | `token_age` block in setup/status and engine/start |
| 24-hour expiry enforcement | ✅ COMPLIANT | `TOKEN_MAX_AGE_HOURS=24` hard-blocks engine start if expired |
| 23-hour warn threshold | ✅ COMPLIANT | `TOKEN_WARN_AGE_HOURS=23` returns warning in checks |
| Profile validation on connect | ✅ COMPLIANT | `GET /v2/profile` validates client_id match |
| Funds fetch on connect | ✅ COMPLIANT | `GET /v2/fundlimit` called after token validation |

**Reconnect flow:** Token expiry is surfaced in the frontend via `token_age.token_expired` flag.  
Frontend must show a "Reconnect Dhan" button when `token_expired: true` or `token_warn: true`.

---

## 2. Static IP Handling

| Item | Status | Notes |
|------|--------|-------|
| Outgoing IP detected via ipify | ✅ COMPLIANT | `get_outgoing_ip()` with 5-min cache |
| IP shown in connect response | ✅ COMPLIANT | `outgoing_ip` and `ip_whitelist` in /connect response |
| IP shown in setup/status | ✅ COMPLIANT | `outgoing_ip` in status payload |
| IP shown in engine/start checks | ✅ COMPLIANT | `static_ip` check item with outgoing_ip |
| Dhan whitelist API check | ⚠️ NOT AVAILABLE | No programmatic API; operator must verify manually |
| Engine blocked if IP not whitelisted | ⚠️ WARN ONLY | Cannot verify programmatically; warning surfaced |
| Production flag: must use static VPS IP | ✅ DOCUMENTED | See DEPLOY_BACKEND_VPS.md |

**Important:** Dhan requires static IP whitelisting via their portal (not API-checkable). Backend outgoing IP is displayed; operator must confirm it matches the whitelisted IP before enabling live orders.

---

## 3. Dhan Connect / Funds Validation

| Item | Status | Notes |
|------|--------|-------|
| POST /api/setup/dhan/connect | ✅ COMPLIANT | Validates, saves encrypted, returns wallet |
| Uses GET /fundlimit for wallet | ✅ COMPLIANT | Real-money balance shown |
| Does NOT place orders during connect | ✅ COMPLIANT | Connect is read-only |
| Returns masked client_id | ✅ COMPLIANT | `****1234` format |
| Returns token metadata | ✅ COMPLIANT | saved_at, age, estimated_expiry |
| Returns outgoing IP | ✅ COMPLIANT | For whitelist verification |
| Returns wallet (available_balance, etc.) | ✅ COMPLIANT | Full DhanFundsResult mapped |
| Graceful error on token invalid | ✅ COMPLIANT | 400 with kind/message/interpreted_error |
| MOCK mode local fallback | ✅ COMPLIANT | No encryption key needed in MOCK/local |

**Connect response shape (REAL mode):**
```json
{
  "success": true,
  "dhan_connected": true,
  "dhan_client_id_masked": "****0001",
  "access_token_present": true,
  "wallet": { "available_balance": 98440.0, ... },
  "outgoing_ip": "203.0.113.10",
  "ip_whitelist": { "backend_ip": "203.0.113.10", "warning": "..." },
  "token": {
    "saved_at": "2026-05-25T09:00:00+00:00",
    "age_minutes": 0,
    "expires_in_hours_estimate": 24
  }
}
```

---

## 4. Order Payload Compliance

### Final Dhan v2 Payload (POST /orders)

All fields present in the final payload sent to Dhan:

```json
{
  "dhanClientId": "1000000001",
  "correlationId": "abc123def456789012",
  "transactionType": "BUY",
  "exchangeSegment": "NSE_FNO",
  "productType": "INTRADAY",
  "orderType": "MARKET",
  "validity": "DAY",
  "tradingSymbol": "NIFTY28MAY2622500CE",
  "securityId": "999888",
  "quantity": 1,
  "disclosedQuantity": 0,
  "price": 0,
  "triggerPrice": 0,
  "afterMarketOrder": false
}
```

**Field classification per Dhan v2 official docs (`POST /v2/orders`):**

| Field | Required? | Status | Notes |
|-------|-----------|--------|-------|
| dhanClientId | ✅ Hard required | ✅ Included | From encrypted vault |
| transactionType | ✅ Hard required | ✅ Included | BUY/SELL (not B/S) |
| exchangeSegment | ✅ Hard required | ✅ Included | NSE_FNO |
| productType | ✅ Hard required | ✅ Included | INTRADAY/CNC/MARGIN |
| orderType | ✅ Hard required | ✅ Included | MARKET/LIMIT |
| validity | ✅ Hard required | ✅ Included | DAY |
| quantity | ✅ Hard required | ✅ Included | Integer > 0 |
| price | ✅ Hard required | ✅ Included | 0 for MARKET |
| securityId | ✅ Required in practice | ✅ Included | Not labeled *required* in Dhan table but Dhan rejects orders without it |
| correlationId | Optional (≤30 chars) | ✅ Included | SHA-256 of signal_id + action (20 chars); recommended for tracking |
| disclosedQuantity | Optional (default 0) | ✅ Included | Always set to 0 by payload builder |
| triggerPrice | Conditional (SL orders) | ✅ Included | Set to 0 for MARKET/LIMIT; required for SL/SL-M |
| afterMarketOrder | Conditional (AMO) | ✅ Included | Set to false; required true for after-market orders |

The payload builder (`_build_dhan_payload_and_resolution`) includes safe defaults for all optional/conditional fields.  
The validator (`validate_dhan_payload`) hard-blocks only on missing core required fields; optional/conditional fields generate warnings, not blocks.

**Raw Pine fields that must NOT be in the final payload:**  
`alertType`, `order_legs`, `strike_price`, `expiry_date`, `option_type`, `transactionType: "B"/"S"`, `productType: "I"`, `orderType: "MKT"` — all blocked by `validate_dhan_payload()`.

---

## 5. SecurityId Resolution

### Resolution Order (production-safe)

```
1. PROVIDED_IN_SIGNAL  — signal carries security_id → use directly
2. SCRIP_MASTER        — lookup in local Dhan CSV (AUTO_RESOLVE_SECURITY_ID=true)
3. OPTION_CHAIN        — not implemented (phase 2)
4. DEFAULT_ENV         — ALLOW_DEFAULT_SECURITY_ID=true required (controlled testing)
5. NOT_FOUND           — block order
```

**Previous bug fixed:** DEFAULT_ENV was tried BEFORE SCRIP_MASTER. This has been corrected.

| Item | Status | Notes |
|------|--------|-------|
| PROVIDED_IN_SIGNAL first | ✅ FIXED | |
| SCRIP_MASTER before DEFAULT_ENV | ✅ FIXED | Critical safety fix |
| NIFTY underlying ID=13 blocked | ✅ FIXED | Blocked at all resolution steps |
| lot_size extracted from scrip master | ✅ ADDED | `SEM_LOT_UNITS` column |
| DEFAULT_ENV requires explicit config | ✅ COMPLIANT | `ALLOW_DEFAULT_SECURITY_ID=false` by default |
| Default warning logged | ✅ COMPLIANT | logger.warning on DEFAULT_ENV use |
| NOT_FOUND blocks order | ✅ COMPLIANT | Empty securityId fails payload validation |
| Debug endpoint: GET /api/setup/security-id/resolve | ✅ ADDED | For pre-deploy verification |
| Scrip master download: POST /api/setup/scrip-master/refresh | ✅ ADDED | Downloads from official Dhan URLs |
| Scrip master status: GET /api/setup/scrip-master/status | ✅ ADDED | |

**NIFTY underlying ID guard:**  
Security ID `13` is the NIFTY index underlying — valid for LTP quoting but NEVER for option order placement. Any resolution returning `13` for an option order is blocked with a clear error.

**Scrip master URLs:**  
- `https://images.dhan.co/api-data/api-scrip-master.csv`
- `https://images.dhan.co/api-data/api-scrip-master-detailed.csv`

---

## 6. Quantity / Lot-Size Handling

| Item | Status | Notes |
|------|--------|-------|
| QTY_MODE=ABSOLUTE (default) | ✅ COMPLIANT | Signal qty = Dhan quantity directly |
| QTY_MODE=LOTS | ⚠️ NOT IMPLEMENTED | Phase 2; blocked with clear error if attempted |
| lot_size from scrip master | ✅ ADDED | Returned in resolution result (for display/future use) |
| max_qty_per_order enforced | ✅ COMPLIANT | Checked before order, blocks oversized qty |
| Quantity as integer | ✅ COMPLIANT | `int(float(qty))` normalisation |
| Frontend qty label | ✅ RECOMMENDED | `qty_mode_note` in setup status response |

**WARNING displayed in setup status:**  
> "Signal qty is treated as ABSOLUTE Dhan quantity (not lots). Ensure your signal qty is the correct number of contracts, not lot count."

**For production:** Dhan option lot size for NIFTY is typically 75. If your Pine/NOVA signal sends `qty=1` meaning "1 lot", you must either:
- Send `qty=75` in the alert (absolute mode), or
- Implement `QTY_MODE=LOTS` with scrip master lot_size lookup (phase 2).

---

## 7. Webhook Secret Handling

| Item | Status | Notes |
|------|--------|-------|
| Secret stored server-side (encrypted vault) | ✅ COMPLIANT | |
| Secret never returned after save | ✅ COMPLIANT | Only `webhook_secret_set: bool` in status |
| Missing secret rejects webhook (403) | ✅ COMPLIANT | `SETUP_INCOMPLETE` status |
| Wrong secret rejects webhook (403) | ✅ COMPLIANT | `UNAUTHORIZED` status |
| Min 8 characters enforced | ✅ COMPLIANT | VaultError if < 8 chars |
| Masked secret shown in status | ✅ COMPLIANT | `****` format via mask_secret() |

**Webhook secret is UI-configured, not env-configured.**  
`POST /api/setup/webhook-secret` saves it. Never put it in `.env`.

---

## 8. Risk Checks

Each TradingView alert passes these checks in order:

1. **Payload parsing** — valid JSON, known format
2. **Webhook secret** — matches stored secret
3. **Signal validation** — required fields present
4. **Duplicate signal** — signal_id not seen before
5. **Engine enabled** — `webhook_trading_enabled=true`
6. **QTY_MODE check** — only ABSOLUTE supported
7. **Product type** — INTRADAY only (if ALLOW_ONLY_INTRADAY=true)
8. **Symbol** — NIFTY only (if ALLOW_ONLY_NIFTY=true)
9. **Market hours** — if REQUIRE_MARKET_HOURS=true
10. **Emergency stop** — blocks entry
11. **Global kill switch** — blocks entry (optionally exits)
12. **allow_entry / allow_exit** — per-direction toggles
13. **Open position check** — no double entry
14. **max_qty_per_order** — quantity ceiling
15. **max_trades_per_day** — trade count ceiling
16. **daily_loss_limit** — P&L floor
17. **SecurityId resolution** — must resolve before Dhan call
18. **Live-order gate** — DHAN_MODE=REAL and ENABLE_LIVE_ORDERS=true required
19. **Payload validation** — final Dhan payload validated before HTTP call

---

## 9. Live-Order Gating

There is exactly **one** code path that calls `client.place_order()`:  
`execution_router.py → _place_order() → client.place_order()`

Gate checks inside `_place_order()`:

| Gate | Action if failed |
|------|-----------------|
| `DHAN_MODE != REAL` | Uses `MockDhanClient` (no HTTP call) |
| `ENABLE_LIVE_ORDERS=false` | Returns BLOCKED (no HTTP call) |
| `creds missing` | Returns BLOCKED |
| `securityId empty` | Returns BLOCKED |
| `MARKET_CLOSED_DEBUG + market closed` | Returns BLOCKED |

**MockDhanClient never makes HTTP calls.**  
**RealDhanClient only called when:** DHAN_MODE=REAL AND ENABLE_LIVE_ORDERS=true AND securityId present.

---

## 10. Post-Order Status Handling

| Item | Status | Notes |
|------|--------|-------|
| orderId saved from placement response | ✅ COMPLIANT | In order_result |
| GET /orders/{order-id} polling | ✅ ADDED | `poll_order_status()` in RealDhanClient |
| Terminal status detection | ✅ ADDED | TRADED, REJECTED, CANCELLED, EXPIRED |
| Pending/transit handling | ✅ ADDED | Returns PENDING_CONFIRMATION after max polls |
| Polling result in execution result | ✅ ADDED | `order_status_poll` field |
| Status in audit log | ✅ COMPLIANT | `DHAN_ORDER_STATUS_POLL` events logged |
| Live Order WebSocket/Postback | ⚠️ PHASE 2 | Not required for MVP |

**MVP polling:** 4 polls × 1.5s delay = max 6s wait. If still pending, status is `PENDING_CONFIRMATION` (dashboard shows ambiguous state; operator should check Dhan portal).

---

## 11. Market Quote Validation

| Item | Status | Notes |
|------|--------|-------|
| QUOTE_REQUIRED_BEFORE_ORDER config | ✅ ADDED | Default: false |
| POST /marketfeed/ltp implementation | ⚠️ PHASE 2 | TODO in dhan_client.py |
| LTP shown in dashboard | ⚠️ PHASE 2 | |

**For MVP:** `QUOTE_REQUIRED_BEFORE_ORDER=false`. Market quote validation is not yet implemented.  
Once implemented, set `QUOTE_REQUIRED_BEFORE_ORDER=true` for production.

---

## 12. Deployment Checklist

### Pre-deployment (VPS Backend)

- [ ] Generate a unique `TOKEN_ENCRYPTION_KEY` (Fernet key): `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- [ ] Set `APP_ENV=production`
- [ ] Set `DHAN_MODE=REAL`
- [ ] Set `ENABLE_LIVE_ORDERS=false` and keep it disabled
- [ ] Set `BACKEND_PUBLIC_BASE_URL=https://api.yourdomain.com`
- [ ] Set `FRONTEND_ORIGIN=https://your-vercel-domain.vercel.app`
- [ ] Set `REQUIRE_MARKET_HOURS=true`
- [ ] Set `DEBUG_ENABLED=false`
- [ ] Set `AUTO_RESOLVE_SECURITY_ID=true`
- [ ] Set `ALLOW_DEFAULT_SECURITY_ID=false`
- [ ] Obtain static IP for VPS
- [ ] Whitelist VPS static IP in Dhan portal
- [ ] Download scrip master: `POST /api/setup/scrip-master/refresh`
- [ ] Verify security ID: `GET /api/setup/security-id/resolve?symbol=NIFTY&expiry=...&strike=...&option_side=CE`
- [ ] Connect Dhan credentials via UI (NOT via env)
- [ ] Configure webhook secret via UI (NOT via env)
- [ ] Set conservative Paper risk limits
- [ ] Complete dry-run (ENABLE_LIVE_ORDERS=false): send real TradingView alert, verify BLOCKED response
- [ ] Confirm the Live checklist remains blocked

### VPS Backend `.env` (production example)

```env
APP_ENV=production
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
FRONTEND_ORIGIN=https://your-vercel-app.vercel.app
BACKEND_PUBLIC_BASE_URL=https://api.yourdomain.com

DHAN_MODE=REAL
ENABLE_LIVE_ORDERS=false

TOKEN_ENCRYPTION_KEY=<generate with Fernet.generate_key()>

AUTO_RESOLVE_SECURITY_ID=true
DHAN_SCRIP_MASTER_PATH=data/dhan_scrip_master.csv
ALLOW_DEFAULT_SECURITY_ID=false
DEFAULT_SECURITY_ID=

REQUIRE_MARKET_HOURS=true
MARKET_CLOSED_DEBUG=false
FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED=false

TOKEN_MAX_AGE_HOURS=24
TOKEN_WARN_AGE_HOURS=23
QUOTE_REQUIRED_BEFORE_ORDER=false

RUNTIME_STATE_DIR=runtime_state
RUNTIME_LOG_DIR=runtime_logs
DEBUG_ENABLED=false
```

### Vercel Frontend `.env`

```env
VITE_API_BASE_URL=https://api.yourdomain.com
```

### What must NOT be in any `.env` file

- `DHAN_CLIENT_ID` — enter via UI
- `DHAN_ACCESS_TOKEN` — enter via UI
- `WEBHOOK_SECRET` — configure via UI

---

## 13. Known Limitations / Phase 2

| Item | Limitation | Phase 2 Plan |
|------|-----------|-------------|
| Live Order Update | WebSocket/Postback not connected | Add Dhan WebSocket order update stream |
| Market Quote LTP | Not implemented before order | Implement `POST /marketfeed/ltp` |
| Option Chain fallback | Not implemented | Add Option Chain API as resolution step 3 |
| QTY_MODE=LOTS | Not implemented | Resolve lot_size from scrip master, multiply |
| Multi-user isolation | Single operator (single Dhan account) | Add user DB, per-user credential vault |
| Order Book / Trade Book | Endpoints stubbed, not surfaced in UI | Add GET /orders and GET /trades to dashboard |
| Scheduled scrip master refresh | Manual only (POST endpoint) | Cron job to refresh daily before market open |
| REQUIRE_MARKET_HOURS default | False in config (must be set manually) | Default true in production template |

---

## Appendix: Endpoint Inventory

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/v2/orders` | POST | ✅ Implemented | Place order |
| `/v2/orders/{id}` | GET | ✅ Implemented | Order status poll |
| `/v2/orders` | GET | 📋 TODO | Order book (phase 2) |
| `/v2/trades` | GET | 📋 TODO | Trade book (phase 2) |
| `/v2/fundlimit` | GET | ✅ Implemented | Funds validation |
| `/v2/positions` | GET | ✅ Implemented | Open positions |
| `/v2/profile` | GET | ✅ Implemented | Token validation |
| `/v2/marketfeed/ltp` | POST | 📋 TODO | Market quote (phase 2) |
