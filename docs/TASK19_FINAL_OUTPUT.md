# NOVA Signal Router — DhanHQ v2 Compliance Audit: Final Output

> **Date:** 2026-05-25  
> **Audit scope:** DhanHQ v2 compliance, production-readiness, security, test coverage  
> **Test result:** ✅ 66 / 66 passed

---

## 1. Files Changed (production code)

| File | Change |
|------|--------|
| `backend/app/config.py` | Added `TOKEN_MAX_AGE_HOURS=24`, `TOKEN_WARN_AGE_HOURS=23`, `QUOTE_REQUIRED_BEFORE_ORDER=False` |
| `backend/app/services/security_id_resolver.py` | **CRITICAL FIX** — resolution order corrected (was DEFAULT_ENV before SCRIP_MASTER); added NIFTY ID=13 guard; added `lot_size` field; added `_guard_nifty_underlying_id()` |
| `backend/app/services/execution_router.py` | Added `correlationId`, `disclosedQuantity`, `triggerPrice`, `afterMarketOrder` to Dhan payload; added post-order status polling |
| `backend/app/services/dhan_client.py` | Added `DHAN_TERMINAL_STATUSES`, `DHAN_PENDING_STATUSES`, `DhanOrderStatusResult`; added `poll_order_status()` on both Mock and Real clients; added base URL v2 constant |
| `backend/app/services/dhan_debugger.py` | Added `disclosedQuantity`, `triggerPrice`, `afterMarketOrder` to required field list in `validate_dhan_payload()` |
| `backend/app/services/credential_vault.py` | Added `dhan_token_age_metadata()` — token age/expiry metadata without exposing the token |
| `backend/app/services/risk_manager.py` | Fixed `QTY_MODE=LOTS` error message |
| `backend/app/routers/engine.py` | Rewrote `start_engine()`: hard-block on expired token; structured per-check list; static IP check; Dhan ping included; live-order confirmation gate |
| `backend/app/routers/setup.py` | Added `token_age` to all responses; added scrip master download endpoint `POST /setup/scrip-master/refresh`; added `GET /setup/scrip-master/status`; added `GET /setup/security-id/resolve`; `connect_dhan` returns `outgoing_ip`, `ip_whitelist`, `token` metadata |

## 2. Files Changed (tests)

| File | Change |
|------|--------|
| `backend/app/tests/test_compliance_audit.py` | **NEW** — 40 tests across 8 classes covering all audit gaps |
| `backend/app/tests/test_dhan_client.py` | Updated `test_place_order_uses_v2_orders_endpoint` payload to include v2 required fields |

## 3. Compliance Fixes Made

### CRITICAL — Security ID Resolution Order (was wrong)

**Before:** `PROVIDED_IN_SIGNAL → DEFAULT_ENV → SCRIP_MASTER → NOT_FOUND`  
**After:** `PROVIDED_IN_SIGNAL → SCRIP_MASTER → DEFAULT_ENV → NOT_FOUND`

A misconfigured `DEFAULT_SECURITY_ID` could previously override a correct scrip-master lookup, sending a live order to the wrong instrument. Now `DEFAULT_ENV` is only reached if the scrip master doesn't have the contract — and only when `ALLOW_DEFAULT_SECURITY_ID=true`.

### CRITICAL — NIFTY Underlying ID=13 Guard

Security ID `"13"` is the NIFTY index (used for quoting) and is never valid for option order placement. A guard now blocks this ID at every resolution path:

- Signal provides ID=13 → blocked before order
- Scrip master resolves to ID=13 → blocked
- DEFAULT_SECURITY_ID=13 → blocked

### Dhan v2 Payload — Missing Required Fields

**Added:** `correlationId`, `disclosedQuantity`, `triggerPrice`, `afterMarketOrder`

These are all required by `POST /v2/orders`. The pre-audit payload was missing all four, which would cause Dhan to reject orders with a 400 validation error.

### Post-Order Status Polling

Added `poll_order_status()` to both `MockDhanClient` and `RealDhanClient`. After a successful placement, the router polls `GET /v2/orders/{order_id}` up to 4 times (1.5 s delay) to resolve `TRANSIT/PENDING` to a terminal status (`TRADED`, `REJECTED`, `CANCELLED`, `EXPIRED`). Final status, `is_filled`, and `avg_price` are returned in every order result.

### Token Age Enforcement

`dhan_token_age_metadata()` computes age from the vault's `connected_at` timestamp. The engine start endpoint hard-blocks if `age >= TOKEN_MAX_AGE_HOURS (24h)`. A soft warning fires at `TOKEN_WARN_AGE_HOURS (23h)`.

### Engine Start Readiness Checklist

`POST /api/engine/start` now performs 10 structured checks before enabling trading:

1. `dhan_connected` — credentials present in vault
2. `dhan_token_valid` — live ping to Dhan API
3. `token_age` — hard-block if expired, warn if > 23h
4. `webhook_secret_set` — secret configured
5. `risk_limits` — qty/trades/loss-limit > 0
6. `backend_public_url` — not placeholder
7. `emergency_stop` — inactive
8. `global_kill_switch` — inactive
9. `static_ip` — outgoing IP shown (operator must verify whitelist)
10. `live_order_gate` — REAL+ENABLE_LIVE_ORDERS requires `confirm_live_orders=true`

---

## 4. Final Order Payload Shape (Dhan v2)

```json
{
  "dhanClientId": "<from vault>",
  "correlationId": "<sha256(signal_id+action)[:20]>",
  "transactionType": "BUY",
  "exchangeSegment": "NSE_FNO",
  "productType": "INTRADAY",
  "orderType": "MARKET",
  "validity": "DAY",
  "tradingSymbol": "NIFTY28MAY2026CE22500",
  "securityId": "123456",
  "quantity": 1,
  "disclosedQuantity": 0,
  "price": 0,
  "triggerPrice": 0,
  "afterMarketOrder": false
}
```

**Endpoint:** `POST https://api.dhan.co/v2/orders`  
**Headers:** `client-id: <clientId>`, `access-token: <token>`, `Content-Type: application/json`

---

## 5. Security ID Resolution Behaviour

```
Signal received
  ↓
1. signal.security_id present?
   → guard ID=13 → BLOCK
   → else → USE (PROVIDED_IN_SIGNAL)
  ↓
2. AUTO_RESOLVE_SECURITY_ID=true?
   → scrip master CSV lookup (SEM_SMST_SECURITY_ID)
   → guard ID=13 on match → BLOCK
   → found → USE (SCRIP_MASTER)
  ↓
3. ALLOW_DEFAULT_SECURITY_ID=true && DEFAULT_SECURITY_ID set?
   → guard ID=13 → BLOCK
   → else → USE (DEFAULT_ENV) [manual testing only]
  ↓
4. NOT_FOUND → order BLOCKED before Dhan call
```

In REAL mode: NOT_FOUND = hard block. In MOCK mode: order proceeds with empty securityId (for testing the flow).

---

## 6. Quantity Mode

**Mode:** `QTY_MODE=ABSOLUTE` (default, only supported mode)

Signal `qty` is sent directly as Dhan `quantity`. If `qty > MAX_QTY_PER_ORDER`, the order is blocked before Dhan is called. `QTY_MODE=LOTS` is stubbed but not implemented (returns `RiskDecision(False, ...)` at risk check).

---

## 7. Connect / Funds Validation

`POST /api/setup/dhan/connect`:
1. Validates vault is ready (or local mock allowed)
2. Calls `validate_token` → `GET /v2/profile` to confirm client ID match
3. Calls `get_fund_limit` → `GET /v2/fundlimit` to snapshot available balance
4. Saves encrypted credentials to vault (Fernet AES-128)
5. Returns masked `client_id`, fund snapshot, `outgoing_ip`, `ip_whitelist` note, `token` age metadata
6. **Never returns** `access_token` in response

---

## 8. Token Expiry Behaviour

| Age | Behaviour |
|-----|-----------|
| < 23h | Normal — no warning |
| 23–24h | Soft warn in `/engine/status` and `/setup/status` |
| ≥ 24h | Hard block at `POST /api/engine/start` (HTTP 400) |

Re-connect via `POST /api/setup/dhan/connect` resets the `connected_at` timestamp.

---

## 9. Static IP Readiness

`POST /api/setup/dhan/connect` and `POST /api/engine/start` both:
- Fetch outgoing IP from `https://api.ipify.org` (cached 5 min)
- Return it as `outgoing_ip` + `ip_whitelist.backend_ip`
- Display a warning: _"Confirm this IP is whitelisted in your Dhan account"_

Dhan order placement requires the backend server IP to be whitelisted. There is no programmatic way to verify the whitelist — the operator must verify manually in the Dhan portal.

---

## 10. Engine Readiness Checklist (per check)

Run `POST /api/engine/start` before going live. The response `checks[]` array shows every gate:

```json
[
  { "name": "dhan_connected",     "ok": true,  "severity": "ok" },
  { "name": "dhan_token_valid",   "ok": true,  "severity": "ok" },
  { "name": "token_age",          "ok": true,  "severity": "ok", "age_minutes": 5 },
  { "name": "webhook_secret_set", "ok": true,  "severity": "ok" },
  { "name": "risk_limits",        "ok": true,  "severity": "ok" },
  { "name": "backend_public_url", "ok": true,  "severity": "ok" },
  { "name": "emergency_stop",     "ok": true,  "severity": "ok" },
  { "name": "global_kill_switch", "ok": true,  "severity": "ok" },
  { "name": "static_ip",         "ok": true,  "severity": "warning", "outgoing_ip": "x.x.x.x" },
  { "name": "security_id_resolver","ok": true, "severity": "ok" },
  { "name": "live_order_gate",    "ok": true,  "severity": "ok" }
]
```

---

## 11. Phase 2 Items (out of scope, tracked)

| Item | Location |
|------|----------|
| `QTY_MODE=LOTS` — lot-size conversion | `risk_manager.py` stub + `config.py` |
| `QUOTE_REQUIRED_BEFORE_ORDER` — LTP pre-flight | `config.py` flag; `dhan_client.py` TODO |
| `GET /v2/orders/{id}` polling — full implementation | `dhan_client.py` `poll_order_status()` |
| `GET /v2/trades` — execution price confirmation | `dhan_client.py` TODO stub |
| `POST /v2/marketfeed/ltp` — quote validation | `dhan_client.py` TODO stub |
| Multi-user account isolation | Phase 2 architecture |

---

## 12. Test Results

```
66 passed in 9.52s
```

### Test command

```bash
cd nova_signal_router/backend
python3 -B -m pytest app/tests/ -v
```

### Test files and class coverage

| File | Tests | Coverage |
|------|-------|----------|
| `test_compliance_audit.py` | 40 | Webhook secret, signal parser, securityId NIFTY guard, qty mode, live-order gating, Dhan payload shape, engine start readiness, setup security |
| `test_dhan_client.py` | 6 | v2 endpoint URLs, headers, Pine payload rejection, token validation, fund limit |
| `test_security_id_resolver.py` | 5 | Provided ID, missing ID, DEFAULT_ENV allow/block, payload validation after resolution |
| `test_signal_parser.py` | 7 | NOVA and Pine parsing, field normalization, signal ID stability |
| `test_webhook_formats.py` | 6 | Secret rejection, unsupported format, max qty, duplicate signal dedup, market hours fallback |
| `test_instrument_resolver.py` | 2 | Scrip master CSV lookup hit and miss |

---

## 13. VPS `.env` (production backend)

```bash
APP_ENV=production
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
BACKEND_PUBLIC_BASE_URL=https://api.yourdomain.com
FRONTEND_ORIGIN=https://your-vercel-app.vercel.app

# Broker mode — set ENABLE_LIVE_ORDERS=true only after engine readiness passes
DHAN_MODE=REAL
ENABLE_LIVE_ORDERS=false
WEBHOOK_TRADING_ENABLED=false

# Generate with: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOKEN_ENCRYPTION_KEY=REPLACE_WITH_FERNET_KEY

# Market safety
REQUIRE_MARKET_HOURS=true
MARKET_CLOSED_DEBUG=false
FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED=false

# Token age limits (hours)
TOKEN_MAX_AGE_HOURS=24
TOKEN_WARN_AGE_HOURS=23

# Security ID resolution
AUTO_RESOLVE_SECURITY_ID=true
DHAN_SCRIP_MASTER_PATH=data/dhan_scrip_master.csv
ALLOW_DEFAULT_SECURITY_ID=false
DEFAULT_SECURITY_ID=

# State / logs
RUNTIME_STATE_DIR=runtime_state
RUNTIME_LOG_DIR=runtime_logs
DEBUG_ENABLED=false
```

> ⚠️ **DO NOT ADD** `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`, or `WEBHOOK_SECRET` to `.env`.  
> These are entered via UI and stored in the encrypted vault only.

## 14. Vercel `.env` (frontend)

```bash
VITE_API_BASE_URL=https://api.yourdomain.com
```

That is the only variable the frontend needs.

---

## 15. Pre-Live Checklist

- [ ] VPS has a **static IP** — confirmed with cloud provider
- [ ] Static IP is **whitelisted in Dhan** → Settings → API → Whitelist IP
- [ ] `TOKEN_ENCRYPTION_KEY` generated and set in VPS env
- [ ] `BACKEND_PUBLIC_BASE_URL` set to real domain (not placeholder)
- [ ] `POST /api/setup/dhan/connect` — connect credentials via UI
- [ ] `POST /api/setup/scrip-master/refresh` — download latest instrument list
- [ ] `GET /api/setup/security-id/resolve?symbol=NIFTY&...` — verify scrip master lookup
- [ ] `POST /api/setup/webhook-secret` — set webhook secret via UI
- [ ] `POST /api/setup/risk` — set qty/trades/loss limits
- [ ] `POST /api/engine/start` — all 10 checks pass
- [ ] TradingView alert URL set to `https://api.yourdomain.com/webhook/tradingview`
- [ ] TradingView alert body contains `"secret": "<your-webhook-secret>"`
- [ ] Test with one mock alert in paper mode before enabling live orders
- [ ] Set `ENABLE_LIVE_ORDERS=true` only after all checks pass
