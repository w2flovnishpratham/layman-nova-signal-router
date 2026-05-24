# Deploy Frontend on Vercel

The frontend is a Vite app. It never calls Dhan directly — it only calls the VPS backend API.  
The frontend **must not** hold any Dhan credentials, webhook secrets, or encryption keys.

---

## 1. Push repo to GitHub

Ensure your repo does not contain `.env` files with real credentials before pushing:

```bash
git status  # verify no .env or runtime_state/ files are staged
git add .
git commit -m "Prepare deployable NOVA Signal Router MVP"
git push origin main
```

---

## 2. Import in Vercel

1. Create a new Vercel project from the GitHub repository.
2. Set **Root Directory** to `frontend`.
3. Set **Build Command** to `npm run build`.
4. Set **Output Directory** to `dist`.

---

## 3. Set the one required environment variable

In Vercel project settings → Environment Variables, set **only**:

```
VITE_API_BASE_URL=https://api.yourdomain.com
```

Do not include `/api` at the end — the frontend adds it internally.

### ⛔ What must NEVER be in Vercel environment variables

| Variable | Why |
|----------|-----|
| `DHAN_CLIENT_ID` | Vercel env is exposed to the browser build — never put credentials here |
| `DHAN_ACCESS_TOKEN` | Same — any Vercel env prefixed `VITE_` is embedded in the JS bundle |
| `WEBHOOK_SECRET` | Never — configure this via the frontend setup page |
| `TOKEN_ENCRYPTION_KEY` | VPS only — this never goes anywhere near the frontend |

---

## 4. Backend CORS

Set the backend `.env` value to match your Vercel deployment URL:

```env
FRONTEND_ORIGIN=https://your-vercel-app.vercel.app
```

Restart the backend after changing CORS:

```bash
sudo systemctl restart nova-signal-router
```

---

## 5. Verify

Open the Vercel URL. The first screen should be the setup stepper:
- Connect Dhan (enter Client ID + Access Token — sent directly to the VPS backend, never stored in browser)
- Configure webhook secret
- Set risk limits

The browser developer tools **must not** show any Dhan token in localStorage, cookies, or API responses.
