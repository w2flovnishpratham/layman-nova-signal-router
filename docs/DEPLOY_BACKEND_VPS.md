# Deploy Backend on a VPS

The backend must run on a VPS with a **static public IP**. This is the only service that calls Dhan.  
Dhan requires your server IP to be whitelisted in their portal before live orders will be accepted.

---

## 1. Obtain a static IP VPS

Use any provider (DigitalOcean, Linode, Hetzner, etc.). The VPS IP **must be static** — a dynamic IP will cause Dhan to reject orders. Note the public IP before proceeding.

---

## 2. Copy backend to VPS

```bash
# From your local machine:
scp -r backend user@YOUR_VPS_IP:/opt/nova-signal-router/backend
```

---

## 3. Create Python environment

```bash
cd /opt/nova-signal-router/backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 4. Create `.env`

```bash
cp .env.example .env

# Generate a unique Fernet key for token encryption (KEEP IT SAFE):
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Edit `.env` with these required values:

```env
APP_ENV=production
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000
BACKEND_PUBLIC_BASE_URL=https://api.yourdomain.com
FRONTEND_ORIGIN=https://your-vercel-app.vercel.app

DHAN_MODE=REAL
ENABLE_LIVE_ORDERS=false

TOKEN_ENCRYPTION_KEY=PASTE_GENERATED_FERNET_KEY

AUTO_RESOLVE_SECURITY_ID=true
DHAN_SCRIP_MASTER_PATH=data/dhan_scrip_master.csv
ALLOW_DEFAULT_SECURITY_ID=false
DEFAULT_SECURITY_ID=

REQUIRE_MARKET_HOURS=true
DEBUG_ENABLED=false

RUNTIME_STATE_DIR=runtime_state
RUNTIME_LOG_DIR=runtime_logs
```

### ⛔ What must NEVER be in `.env`

| Variable | Correct approach |
|----------|-----------------|
| `DHAN_CLIENT_ID` | Enter via the frontend setup page → stored encrypted server-side |
| `DHAN_ACCESS_TOKEN` | Enter via the frontend setup page → stored encrypted server-side |
| `WEBHOOK_SECRET` | Configure via the frontend setup page → stored encrypted server-side |

Putting credentials in `.env` bypasses the encrypted vault and exposes them in plaintext.

---

## 5. Create runtime directories

```bash
mkdir -p /opt/nova-signal-router/backend/runtime_state
mkdir -p /opt/nova-signal-router/backend/runtime_logs
mkdir -p /opt/nova-signal-router/backend/data
chmod 700 /opt/nova-signal-router/backend/runtime_state
```

---

## 6. systemd service

Create `/etc/systemd/system/nova-signal-router.service`:

```ini
[Unit]
Description=NOVA Signal Router Backend
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/nova-signal-router/backend
Environment="PATH=/opt/nova-signal-router/backend/.venv/bin"
ExecStart=/opt/nova-signal-router/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable nova-signal-router
sudo systemctl start nova-signal-router
sudo systemctl status nova-signal-router
```

---

## 7. Nginx reverse proxy

Create `/etc/nginx/sites-available/nova-signal-router`:

```nginx
server {
    listen 80;
    server_name api.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/nova-signal-router /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 8. SSL with Certbot

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d api.yourdomain.com
```

---

## 9. Whitelist VPS IP in Dhan

1. Hit `GET /api/setup/status` to confirm the `outgoing_ip` field matches your VPS public IP.
2. Log in to [Dhan Developer Portal](https://developer.dhan.co/) and whitelist that IP.
3. Only then enable live orders (`ENABLE_LIVE_ORDERS=true` + restart).

---

## 10. Verify deployment

```bash
curl https://api.yourdomain.com/api/health
curl https://api.yourdomain.com/api/setup/status
```

Confirm:
- `outgoing_ip` matches the VPS IP you whitelisted in Dhan
- `dhan_connected: false` (you haven't entered credentials yet — that's correct)
- `webhook_secret_set: false` (configured via UI next)

---

## 11. Download scrip master

After deployment, download the Dhan instrument CSV:

```bash
curl -X POST https://api.yourdomain.com/api/setup/scrip-master/refresh
```

This downloads to `backend/data/api-scrip-master-detailed.csv` for security ID resolution.

---

## 12. First-run order (do this from the frontend UI)

1. Open the frontend URL.
2. Connect Dhan credentials (Client ID + Access Token) via the Setup page.
3. Set webhook secret via the Setup page.
4. Set risk limits: `max_qty_per_order=1`, `max_trades_per_day=1`, `daily_loss_limit=500`.
5. Start the Engine.
6. Send a test TradingView alert. Confirm it shows `BLOCKED (live orders disabled)`.
7. Once dry-run passes: set `ENABLE_LIVE_ORDERS=true` in `.env`, restart backend.
8. Send one real alert with `qty=1`. Monitor in dashboard and Dhan portal.
