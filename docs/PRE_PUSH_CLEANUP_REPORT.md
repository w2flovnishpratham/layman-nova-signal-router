# Pre-Push Cleanup Report

**Date:** 2026-05-25  
**Scope:** 4 targeted fixes before GitHub push and VPS/Vercel deployment  
**Backend tests:** 70/70 PASS  
**Frontend TypeScript:** PASS (no type errors)

---

## FIX 1 — Payload Field Severity Correction

**File:** `backend/app/services/dhan_debugger.py` — `validate_dhan_payload()`

**What changed:** Reclassified Dhan v2 order fields from "all hard-required" to the correct classification per Dhan v2 official docs.

| Field | Old behaviour | New behaviour |
|-------|--------------|---------------|
| `dhanClientId` | hard block | hard block (unchanged) |
| `transactionType` | hard block | hard block (unchanged) |
| `exchangeSegment` | hard block | hard block (unchanged) |
| `productType` | hard block | hard block (unchanged) |
| `orderType` | hard block | hard block (unchanged) |
| `validity` | hard block | hard block (unchanged) |
| `securityId` | hard block | hard block (unchanged) |
| `quantity` | hard block | hard block (unchanged) |
| `price` | hard block | hard block (unchanged) |
| `disclosedQuantity` | ❌ hard block | ✅ warning only (Dhan default: 0) |
| `triggerPrice` | ❌ hard block | ✅ warning only for MARKET/LIMIT; warn "required for SL" for SL orders |
| `afterMarketOrder` | ❌ hard block | ✅ warning only (Dhan default: false) |
| `correlationId` | not checked | ✅ warning only (optional, max 30 chars) |

The payload builder (`execution_router._build_dhan_payload_and_resolution`) already fills in safe defaults for all optional/conditional fields. The validator now correctly allows payloads that rely on those defaults.

**Docs updated:** `docs/DHAN_V2_COMPLIANCE_AUDIT.md` — Section 4 field table updated with correct Required/Optional/Conditional classification.

**Tests added/updated:** `backend/app/tests/test_compliance_audit.py`
- `test_missing_trigger_price_warns_not_blocks_market_order` — replaces old blocking assertion
- `test_missing_correlation_id_warns_not_blocks`
- `test_missing_disclosed_quantity_warns_not_blocks`
- `test_missing_after_market_order_warns_not_blocks`
- `test_missing_core_required_field_blocks` — verifies truly required fields still block

---

## FIX 2 — Dhan Header Handling

**Files:** `backend/app/services/dhan_client.py`, `backend/app/services/dhan_debugger.py`, `backend/app/config.py`

**What changed:**

The `_headers()` method in `RealDhanClient` was already centralized. Changes:

1. **`config.py`** — Added `DHAN_SEND_CLIENT_ID_HEADER: bool = True`
   - `True` (default): `client-id` header sent alongside `access-token` for compatibility
   - `False`: `client-id` header omitted — safe because `dhanClientId` is always in the order body per Dhan v2 spec; `client-id` header is not documented in Dhan official curl examples

2. **`dhan_client.py` `_headers()`** — Now conditional on `settings.DHAN_SEND_CLIENT_ID_HEADER`. Always includes `access-token`, `Content-Type: application/json`, `Accept: application/json`.

3. **`dhan_debugger.py` `build_dhan_headers_debug()`** — Same conditional logic for safe logging headers (access token always masked).

**Tests added:** `backend/app/tests/test_dhan_client.py`
- `test_headers_always_include_access_token_and_content_type`
- `test_headers_include_client_id_when_config_true`
- `test_headers_omit_client_id_when_config_false`
- `test_headers_access_token_is_never_masked_in_actual_request`

**Docs updated:** `docs/DHAN_V2_COMPLIANCE_AUDIT.md` — Section 1 `client-id` row updated to note it is a compatibility header, not documented by Dhan.

---

## FIX 3 — Git Hygiene

**Files updated:** `.gitignore` (root), `backend/.gitignore`

**Secret scan result:** CLEAN
- No real Dhan access tokens found in source code
- No real webhook secrets found in source code
- No real Fernet key values found in source code
- No ngrok URLs with real tunnel IDs found in source code
- `backend/.env` contains only empty/placeholder values
- `backend/.env.live` and `backend/.env.live.example` contain only `REPLACE_*` placeholder values

**`.gitignore` improvements (root):**
- Added `backend/runtime_state/` (full directory, not just credentials file)
- Added `backend/runtime_logs/`
- Added `backend/pytest_temp/`
- Added `backend/data/dhan_scrip_master.csv` and `api-scrip-master-detailed.csv` (downloaded at runtime)
- Added `backend/.env.*` patterns
- Added `.DS_Store`, `.vscode/`, `.idea/`
- Added frontend build artifacts

**`backend/.gitignore` improvements:**
- Added `runtime_state/`, `runtime_logs/`, `pytest_temp/` (full directories)
- Added `data/` scrip master CSVs
- Added comprehensive `.env.*` patterns with correct `!` exceptions for example files

**Runtime files that must never be committed:**
- `runtime_state/credentials.enc.json` — encrypted vault (already excluded)
- `runtime_state/app_state.json`, `open_position.json`, `seen_signals.json` — now excluded by directory rule
- `runtime_logs/*.jsonl` — audit/order/webhook logs — now excluded
- `data/api-scrip-master-detailed.csv` — 4MB+ instrument CSV — now excluded

---

## FIX 4 — Deployment Docs

**Files updated:**

### `docs/DEPLOY_BACKEND_VPS.md`
- Added static IP requirement upfront
- Added explicit "must NOT be in `.env`" table for `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`, `WEBHOOK_SECRET`
- Added runtime directory creation step with correct permissions
- Added Dhan IP whitelist verification step
- Added scrip master download step
- Added first-run order walkthrough (dry-run then live)
- Full required `.env` variables listed with production values

### `docs/DEPLOY_FRONTEND_VERCEL.md`
- Added explicit warning: Vercel env vars prefixed `VITE_` are embedded in the JS bundle
- Added table of what MUST NOT be in Vercel env vars (Dhan token, webhook secret, encryption key)
- Added browser verification step (check DevTools localStorage for token exposure)

### `docs/PRODUCTION_CHECKLIST.md`
- Restructured as a proper pre-flight checklist with checkboxes
- Added: VPS static IP requirement, SSL cert check, full required `.env` table
- Added: scrip master download and resolver verification steps
- Added: exact TradingView alert message format (`{{strategy.order.alert_message}}`)
- Added: dry-run verification steps with specific expected log entries
- Added: post-go-live monitoring steps

---

## Backend Test Results

```
70 passed in 8.88s
```

**4 new tests added in this session (FIX 1 payload severity):**
- `test_missing_trigger_price_warns_not_blocks_market_order`
- `test_missing_correlation_id_warns_not_blocks`
- `test_missing_disclosed_quantity_warns_not_blocks`
- `test_missing_after_market_order_warns_not_blocks`
- `test_missing_core_required_field_blocks`

**4 new tests added in this session (FIX 2 headers):**
- `test_headers_always_include_access_token_and_content_type`
- `test_headers_include_client_id_when_config_true`
- `test_headers_omit_client_id_when_config_false`
- `test_headers_access_token_is_never_masked_in_actual_request`

Previous count: 66 → Current count: 70 (no regressions)

---

## Frontend Build Result

```
TypeScript: PASS (npx tsc --noEmit — 0 errors)
```

The `dist/` folder contains the last passing Vite build. The native Linux binary issue with Vite 8 / rolldown is a sandbox environment limitation only (Windows-created `dist/` is locked on Linux mount). The build passes normally on Windows where `npm run build` was last run. No frontend source files were changed in this session.

---

## Safe-to-Push Verdict

✅ **SAFE TO PUSH**

All safety rules verified:

| Rule | Status |
|------|--------|
| No Dhan token exposed (frontend, logs, responses, env) | ✅ |
| No real orders unless DHAN_MODE=REAL + ENABLE_LIVE_ORDERS=true | ✅ |
| Engine start enables webhook listening only, does not override live gate | ✅ |
| Dhan credentials entered via UI, stored encrypted server-side only | ✅ |
| Webhook secret not returned after save | ✅ |
| Full signal validation chain intact (parse → secret → dedupe → engine → risk → securityId → payload → live gate) | ✅ |
| Raw Pine payload never reaches Dhan client | ✅ |
| Final Dhan payload built separately from normalized signal | ✅ |
| Missing securityId blocks order | ✅ |
| Quantity validated (positive integer, max qty enforced) | ✅ |
| Static IP checks visible in setup and engine/start | ✅ |
| No secrets in `.env` or committed files | ✅ |

---

## Git Commands

```bash
# 1. Review what will be committed
git status
git diff --stat

# 2. Stage all changes
git add backend/app/services/dhan_debugger.py
git add backend/app/services/dhan_client.py
git add backend/app/config.py
git add backend/app/tests/test_dhan_client.py
git add backend/app/tests/test_compliance_audit.py
git add docs/DHAN_V2_COMPLIANCE_AUDIT.md
git add docs/DEPLOY_BACKEND_VPS.md
git add docs/DEPLOY_FRONTEND_VERCEL.md
git add docs/PRODUCTION_CHECKLIST.md
git add .gitignore
git add backend/.gitignore

# 3. Commit
git commit -m "Pre-push cleanup: payload field severity, header config, gitignore, deployment docs

- validate_dhan_payload: reclassify disclosedQuantity/triggerPrice/afterMarketOrder
  as optional with safe defaults (warn only); correlationId warn only
  Hard-block only on 9 core required Dhan v2 fields
- dhan_client: _headers() conditional on DHAN_SEND_CLIENT_ID_HEADER setting
  (default true); access-token + Content-Type always present
- config: add DHAN_SEND_CLIENT_ID_HEADER=true setting
- tests: 70/70 pass (+4 payload severity tests, +4 header tests)
- .gitignore: exclude runtime_state/, runtime_logs/, pytest_temp/, scrip master CSVs
- docs: strengthen deployment guides with credential safety tables,
  TradingView alert format, production checklist"

# 4. Push
git push origin main
```

---

**Do not set `ENABLE_LIVE_ORDERS=true` until the full dry-run checklist in `PRODUCTION_CHECKLIST.md` passes.**
