# AL

AL is an Activity Logger monorepo. It contains the first Unity plugin (`UAL`), a FastAPI backend, and a React frontend.

## Layout

- `packages/ual` - Unity Activity Logger package for Unity Package Manager.
- `apps/backend` - FastAPI API server with MongoDB persistence and ALR1 decoding.
- `apps/frontend` - React/Vite dashboard.
- `packages/blender_al` - Blender Activity Logger add-on.
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

- API: `http://64.225.108.88:8000`
- Mongo URI: `mongodb://127.0.0.1:27017`
- Mongo database: `al`

The MVP temporarily commits the private key in `apps/backend/al_backend/UnityActivityLoggerKey.json` as requested. Replace this with environment-managed secrets before any public deployment.

## Frontend

```bash
cd apps/frontend
npm install
npm run dev
```

The dashboard defaults to `http://64.225.108.88:8000` for API calls. Override with `VITE_API_URL`.

## Telegram Bot

The Telegram bot listens to the team chat and sends workday events to the backend:

- `онлайн` / `online` starts the Telegram workday or closes the current AFK break.
- `афк` / `afk` starts an AFK break.
- `офлайн` / `оффлайн` / `offline` closes the Telegram workday.

Create the bot with BotFather, add it to the work chat, and disable privacy mode if it needs to read ordinary chat messages. Do not commit the bot token.

Local bot settings live in `.env.telegram-bot`:

```bash
cp .env.telegram-bot.example .env.telegram-bot
```

```bash
scripts/start-bot-local.sh
scripts/stop-bot-local.sh
```

If `TELEGRAM_ALLOWED_CHAT_ID` is omitted, the bot logs incoming chat ids so you can copy the correct one and restart it locked to that chat. The bot posts events to `AL_BACKEND_URL`, which defaults to `http://64.225.108.88:8000`. For production, use the same variables with the public backend URL.

## Unity Package

Import `packages/ual` into Unity through Package Manager from a Git URL with a path:

```text
https://github.com/DmitryShane/AL.git?path=/packages/ual
```

For local testing, add the package from disk using Unity Package Manager and select `packages/ual`.

