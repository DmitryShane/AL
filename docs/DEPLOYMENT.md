# Production Deployment

Production runs on `64.225.108.88` with nginx, systemd, MongoDB, `uv`, and Node.js.

## First-Time Server Setup

SSH as root:

```bash
ssh root@64.225.108.88
```

Bootstrap the host:

```bash
curl -fsSL https://raw.githubusercontent.com/DmitryShane/AL/main/scripts/server-bootstrap.sh | bash
```

Clone the repository:

```bash
sudo -u al git clone https://github.com/DmitryShane/AL.git /opt/al/current
```

Create `/etc/al/backend.env`:

```bash
AL_MONGO_URI=mongodb://127.0.0.1:27017
AL_MONGO_DATABASE=al
AL_PRIVATE_KEY_PATH=/opt/al/current/apps/backend/al_backend/UnityActivityLoggerKey.json
AL_DEFAULT_SEND_INTERVAL_SECONDS=300
AL_CORS_ORIGINS=http://64.225.108.88,http://64.225.108.88:8000
```

Create `/etc/al/telegram-bot.env`:

```bash
TELEGRAM_BOT_TOKEN=replace-with-botfather-token
TELEGRAM_ALLOWED_CHAT_ID=replace-with-chat-id
AL_BACKEND_URL=http://64.225.108.88:8000
AL_TELEGRAM_LOG_LEVEL=INFO
```

Deploy the current `main`:

```bash
/opt/al/current/scripts/deploy-server.sh origin/main
```

## GitHub Actions Secrets

Add these repository secrets:

```text
DEPLOY_HOST=64.225.108.88
DEPLOY_USER=root
DEPLOY_PORT=22
DEPLOY_SSH_KEY=<private SSH key that can log in as root on the droplet>
```

The workflow in `.github/workflows/deploy.yml` runs on every push to `main` and executes:

```bash
/opt/al/current/scripts/deploy-server.sh <commit-sha>
```

## Runtime Checks

```bash
systemctl status mongod nginx al-backend al-telegram-bot
curl http://127.0.0.1:8000/api/v1/health
curl http://64.225.108.88:8000/api/v1/health
```

The dashboard is served from:

```text
http://64.225.108.88/
```
