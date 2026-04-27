# AL

AL is an Activity Logger monorepo. It contains the first Unity plugin (`UAL`), a FastAPI backend, and a React frontend.

## Layout

- `packages/ual` - Unity Activity Logger package for Unity Package Manager.
- `apps/backend` - FastAPI API server with MongoDB persistence and ALR1 decoding.
- `apps/frontend` - React/Vite dashboard.
- `references/originals` - copied original Unity script and decoder source used as reference material.
- `docs/REMINDERS.md` - follow-up product and deployment reminders.

## Local Requirements

- Python 3.14+
- `uv`
- MongoDB running locally on `mongodb://127.0.0.1:27017`
- Node.js/npm for the frontend

MongoDB and Node are not bundled with this repo. On macOS they can be installed with Homebrew.

## Backend

```bash
cd apps/backend
uv sync
uv run fastapi dev al_backend/main.py
```

The backend defaults to:

- API: `http://127.0.0.1:8000`
- Mongo URI: `mongodb://127.0.0.1:27017`
- Mongo database: `al`

The MVP temporarily commits the private key in `apps/backend/al_backend/UnityActivityLoggerKey.json` as requested. Replace this with environment-managed secrets before any public deployment.

## Frontend

```bash
cd apps/frontend
npm install
npm run dev
```

The dashboard defaults to `http://127.0.0.1:8000` for API calls. Override with `VITE_API_URL`.

## Unity Package

Import `packages/ual` into Unity through Package Manager from a Git URL with a path:

```text
https://github.com/DmitryShane/AL.git?path=/packages/ual
```

For local testing, add the package from disk using Unity Package Manager and select `packages/ual`.
