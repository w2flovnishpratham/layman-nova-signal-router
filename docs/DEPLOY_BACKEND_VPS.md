# Deploy Backend

The backend can run on a managed host or VPS. For live orders, Dhan traffic must
route through the AWS multi-IP egress proxy described in
`deploy/AWS_MULTI_IP_PROXY_SETUP.md`.

Dhan requires the user's assigned Nova Static IP to be whitelisted before live
orders will be accepted. The backend host IP is not the Dhan whitelist IP.

---

## 1. Obtain a backend host

Use a backend host that can run the FastAPI service and reach the AWS proxy host.
Live order egress uses AWS proxy slots, not the backend host's public IP.

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

DATABASE_URL=<NEON_PRODUCTION_DATABASE_URL>

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

AWS_PROXY_SLOTS_ENABLED=true
AWS_PROXY_HOST=13.203.58.220
AWS_PROXY_SHARED_PASSWORD=REPLACE_WITH_SECRET
```

### ⛔ What must NEVER be in `.env`

| Variable | Correct approach |
|----------|-----------------|
| `DHAN_CLIENT_ID` | Enter via the frontend setup page → stored encrypted server-side |
| `DHAN_ACCESS_TOKEN` | Enter via the frontend setup page → stored encrypted server-side |
| `WEBHOOK_SECRET` | Configure via the frontend setup page → stored encrypted server-side |

Putting credentials in `.env` bypasses the encrypted vault and exposes them in plaintext.
Never commit the Neon `DATABASE_URL`; keep it only in the VPS environment or secret store.

### Database schema

Create a new Neon production database or branch, set `DATABASE_URL` in the VPS
environment, then run Alembic from the backend directory before starting the
service:

```bash
cd /opt/nova-signal-router/backend
source .venv/bin/activate
python -m alembic upgrade head
python -m alembic current
```

Verify the expected tables exist:

```sql
select table_name
from information_schema.tables
where table_schema = 'public'
order by table_name;
```

Production startup treats Alembic as the schema authority. Do not use
`python -m scripts.init_db` or SQLAlchemy `create_all()` for production schema
initialization.

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

Allow your deploy user to restart only this service without an interactive password:

```bash
sudo visudo -f /etc/sudoers.d/nova-deploy
```

Add this line, replacing `deploy` with your VPS SSH username:

```sudoers
deploy ALL=(root) NOPASSWD: /bin/systemctl restart nova-signal-router, /bin/systemctl is-active nova-signal-router, /bin/systemctl status nova-signal-router
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

---

## 13. Git-based VPS deploy

The repository should exist on the VPS at `/opt/nova-signal-router`.

First-time clone:

```bash
sudo mkdir -p /opt/nova-signal-router
sudo chown -R deploy:deploy /opt/nova-signal-router
git clone git@github.com:YOUR_GITHUB_USER/YOUR_REPO.git /opt/nova-signal-router
```

Manual deploy and restart from the VPS:

```bash
cd /opt/nova-signal-router
bash scripts/deploy_vps.sh origin/main
```

Manual deploy and restart from your local machine:

```bash
git push origin main
ssh deploy@YOUR_VPS_IP "cd /opt/nova-signal-router && bash scripts/deploy_vps.sh origin/main"
```

The deploy script:
- fetches the target git ref
- installs backend requirements into `backend/.venv`
- runs `python -m alembic upgrade head` from `backend`
- compiles backend Python files
- keeps `.env`, `runtime_state`, `runtime_logs`, and `data` on the VPS
- restarts `nova-signal-router`
- checks `/api/health`

---

## 14. GitHub Actions CI/CD

The workflow lives at `.github/workflows/ci-deploy.yml`.

It runs on every push to `main`:
1. backend tests: `python -m pytest backend/app/tests`
2. frontend build: `npm ci && npm run build`
3. SSH deploy to VPS
4. systemd restart
5. health check

Create these GitHub repository secrets:

| Secret | Example |
|---|---|
| `VPS_HOST` | `203.0.113.10` |
| `VPS_PORT` | `22` |
| `VPS_USER` | `deploy` |
| `VPS_SSH_KEY` | Private key for the deploy user |

Optional GitHub repository variables:

| Variable | Default |
|---|---|
| `VPS_APP_DIR` | `/opt/nova-signal-router` |
| `VPS_SERVICE_NAME` | `nova-signal-router` |
| `VPS_HEALTH_URL` | `http://127.0.0.1:8000/api/health` |

Generate a deploy SSH key locally:

```bash
ssh-keygen -t ed25519 -C "nova-github-deploy" -f ~/.ssh/nova_github_deploy
```

Put the public key on the VPS:

```bash
ssh-copy-id -i ~/.ssh/nova_github_deploy.pub deploy@YOUR_VPS_IP
```

Store the private key in GitHub secret `VPS_SSH_KEY`:

```bash
cat ~/.ssh/nova_github_deploy
```
