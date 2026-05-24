# Production Checklist

Complete all items in order before setting `ENABLE_LIVE_ORDERS=true`.

---

## VPS / Network

- [ ] VPS has a **static public IP** (not dynamic)
- [ ] `GET /api/setup/status` returns `outgoing_ip` matching the VPS IP
- [ ] That VPS IP is whitelisted in the [Dhan Developer Portal](https://developer.dhan.co/)
- [ ] `BACKEND_PUBLIC_BASE_URL` is an HTTPS URL (not ngrok, not localhost)
- [ ] `FRONTEND_ORIGIN` is the Vercel app HTTPS URL
- [ ] SSL certificate active on the backend domain (Certbot or equivalent)

---

## Environment Config (backend `.env`)

Required variables and their production values:

| Variable | Required value |
|----------|---------------|
| `APP_ENV` | `production` |
| `DHAN_MODE` | `REAL` |
| `ENABLE_LIVE_ORDERS` | `false` ← start here; only set `true` after dry-run |
| `TOKEN_ENCRYPTION_KEY` | A Fernet key generated with `Fernet.generate_key()` |
| `BACKEND_PUBLIC_BASE_URL` | `https://api.yourdomain.com` |
| `FRONTEND_ORIGIN` | `https://your-vercel-app.vercel.app` |
| `REQUIRE_MARKET_HOURS` | `true` |
| `AUTO_RESOLVE_SECURITY_ID` | `true` |
| `ALLOW_DEFAULT_SECURITY_ID` | `false` |
| `DEBUG_ENABLED` | `false` |

Variables that must **not** appear in `.env`:

| Variable | Correct location |
|----------|-----------------|
| `DHAN_CLIENT_ID` | Frontend setup page → encrypted vault |
| `DHAN_ACCESS_TOKEN` | Frontend setup page → encrypted vault |
| `WEBHOOK_SECRET` | Frontend setup page → encrypted vault |

---

## Credentials (via frontend Setup UI)

- [ ] Dhan Client ID and Access Token entered via the Setup page (NOT via `.env`)
- [ ] Setup status shows `dhan_connected: true`
- [ ] Wallet/funds visible after connection (confirms token is valid)
- [ ] Dhan token age is displayed and well within 24-hour limit
- [ ] Webhook secret configured via the Setup page (NOT via `.env`)
- [ ] Setup status shows `webhook_secret_set: true`

---

## Risk Limits

- [ ] `max_qty_per_order = 1` (for first live test)
- [ ] `max_trades_per_day = 1` (for first live test)
- [ ] `daily_loss_limit = 500` or stricter
- [ ] `allow_entry = true`
- [ ] `allow_exit = true`
- [ ] `emergency_stop = false`
- [ ] `global_kill_switch = false`

---

## Security ID (scrip master)

- [ ] Scrip master downloaded: `POST /api/setup/scrip-master/refresh`
- [ ] Security ID resolver verified for target contract:
  `GET /api/setup/security-id/resolve?symbol=NIFTY&expiry=YYYY-MM-DD&strike=22500&option_side=CE`
- [ ] Resolver returns `ok: true` with method `SCRIP_MASTER`
- [ ] `ALLOW_DEFAULT_SECURITY_ID=false` confirmed in `.env`

---

## TradingView Webhook

- [ ] Webhook URL in TradingView: `https://api.yourdomain.com/webhook/tradingview`
- [ ] Alert message body is **exactly**:
  ```
  {{strategy.order.alert_message}}
  ```
  (This passes the Pine strategy's alert_message JSON, which the backend parses)
- [ ] Or for NOVA format, the full JSON payload with `strategy_code` field

---

## Dry-Run Test (ENABLE_LIVE_ORDERS=false)

- [ ] Start Engine via the frontend
- [ ] Send a real TradingView alert to the webhook URL
- [ ] Dashboard shows the signal lifecycle: received → parsed → risk checked → `BLOCKED (live orders disabled)`
- [ ] Order log shows `ENABLE_LIVE_ORDERS=false` block reason
- [ ] Audit log contains no raw Dhan access token
- [ ] Webhook log contains no raw access token
- [ ] Browser localStorage does NOT contain Dhan token or webhook secret

---

## Go Live

After dry-run passes:

- [ ] Set `ENABLE_LIVE_ORDERS=true` in backend `.env`
- [ ] Restart backend: `sudo systemctl restart nova-signal-router`
- [ ] Start Engine; confirm frontend shows the live-orders warning
- [ ] Send one test alert with `qty=1`
- [ ] Confirm order appears in Dhan portal with correct status
- [ ] Confirm `order_events.jsonl` shows the order and poll result
- [ ] Gradually increase limits after first successful order

---

## Safety Checklist (always verify before any session)

- [ ] Emergency stop is **off** before starting
- [ ] Global kill switch is **off** before starting
- [ ] Token age < 23 hours (reconnect if close to expiry)
- [ ] `outgoing_ip` in setup/status still matches whitelisted VPS IP
- [ ] `ALLOW_DEFAULT_SECURITY_ID=false` in production
