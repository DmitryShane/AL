# AL

AL is an Activity Logger workspace. It contains the FastAPI backend, React frontend, and local tooling.

## Layout

- `apps/backend` - FastAPI API server with MongoDB persistence and ALR1 decoding.
- `apps/frontend` - React/Vite dashboard.
- `/Volumes/MacMiniExternal2TB/Development/unity-bike-rush-2/Packages/com.al.ual` - linked Unity package and Blender Activity Logger add-on source.
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

- API: `https://activity.mempic.com`
- Mongo URI: `mongodb://127.0.0.1:27017`
- Mongo database: `al`

The MVP temporarily commits the private key in `apps/backend/al_backend/UnityActivityLoggerKey.json` as requested. Replace this with environment-managed secrets before any public deployment.

## Frontend

```bash
cd apps/frontend
npm install
npm run dev
```

The dashboard defaults to `https://activity.mempic.com` for API calls. Production builds override `VITE_API_URL` to the public site origin so browser requests use nginx's same-origin `/api/` proxy.

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

If `TELEGRAM_ALLOWED_CHAT_ID` is omitted, the bot logs incoming chat ids so you can copy the correct one and restart it locked to that chat. The bot posts events to `AL_BACKEND_URL`, which defaults to `https://activity.mempic.com`. For production, use the same variables with the public backend URL.

## Unity And Blender Package

The active plugin source is the linked package at:

```text
/Volumes/MacMiniExternal2TB/Development/unity-bike-rush-2/Packages/com.al.ual
```

Blender add-on source lives in `blender_al` inside that package, with the installable archive at `blender_al.zip`.

