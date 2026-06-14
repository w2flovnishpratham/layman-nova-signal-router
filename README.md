# NOVA Signal Router

Deployable single-operator MVP for routing TradingView alerts through backend safety checks before Dhan order placement.

The frontend never calls Dhan. Dhan credentials are submitted once during onboarding and stored only by the backend in an encrypted file vault.

## Quick Start

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
uvicorn app.main:app --reload --port 8000
```

Put the generated key in `.env` as `TOKEN_ENCRYPTION_KEY`.

### Frontend

```bash
cd frontend
npm install
copy .env.local.example .env.local
npm run dev
```

Open `http://localhost:5173` and follow the setup steps:

1. Connect Dhan.
2. Verify wallet/funds.
3. Save TradingView webhook secret.
4. Copy webhook URL and alert message.
5. Set risk limits.
6. Start Engine.

## Safety Defaults

| Flag | Default |
| --- | --- |
| `ENABLE_LIVE_ORDERS` | `false` |
| `WEBHOOK_TRADING_ENABLED` | `false` |
| Runtime max quantity per order | `1` |
| Runtime max trades per day | `1` |
| Runtime daily loss limit | `500` |
| `DEBUG_ENABLED` | `false` |

When `DHAN_MODE=REAL` and `ENABLE_LIVE_ORDERS=false`, alerts can be parsed and logged, but Dhan order placement is blocked with reason `Live orders disabled`.

## Deploy

- Frontend: Vercel with `VITE_API_BASE_URL=https://api.yourdomain.com`
- Backend: VPS with static IP, Nginx, SSL, and `BACKEND_PUBLIC_BASE_URL=https://api.yourdomain.com`
- Dhan static IP whitelist: VPS public IP, not Vercel

## Documentation

- [Backend VPS deployment](docs/DEPLOY_BACKEND_VPS.md)
- [Frontend Vercel deployment](docs/DEPLOY_FRONTEND_VERCEL.md)
- [Production checklist](docs/PRODUCTION_CHECKLIST.md)
- [API contract](docs/API_CONTRACT.md)
- [TradingView webhook format](docs/TRADINGVIEW_WEBHOOK_FORMAT.md)

Production/live webhooks require `X-Nova-Timestamp` and an HMAC-SHA256
`X-Nova-Signature` over `<timestamp>.<raw_body>`. TradingView cannot generate
these headers directly, so production alerts require a trusted signing relay.
- [Safety rules](docs/SAFETY_RULES.md)
