# Local Setup

## Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
uvicorn app.main:app --reload --port 8000
```

Put the generated key in `.env` as `TOKEN_ENCRYPTION_KEY`.

For local development you can keep:

```env
APP_ENV=local
BACKEND_PUBLIC_BASE_URL=http://localhost:8000
FRONTEND_ORIGIN=http://localhost:5173
DHAN_MODE=MOCK
ENABLE_LIVE_ORDERS=false
DEBUG_ENABLED=false
```

## Frontend

```bash
cd frontend
npm install
copy .env.local.example .env.local
npm run dev
```

Open `http://localhost:5173` and complete the setup stepper.

## Tests

```bash
cd backend
.\venv\Scripts\python.exe -m pytest app\tests
```

Or, if using `.venv`:

```bash
.\.venv\Scripts\python.exe -m pytest app\tests
```
