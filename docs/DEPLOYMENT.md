# Production Deployment

Production runs on `activity.mempic.com` with nginx, systemd, MongoDB, `uv`, and Node.js.

## First-Time Server Setup

SSH as root:

```bash
ssh root@activity.mempic.com
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
AL_CORS_ORIGINS=https://activity.mempic.com,http://activity.mempic.com
AL_ADMIN_EMAIL=dmitry.shane@gmail.com
AL_ADMIN_PASSWORD=replace-with-initial-admin-password
```

`AL_ADMIN_EMAIL` and `AL_ADMIN_PASSWORD` bootstrap the first site admin. After logging in, create normal user profiles from `Settings -> Site Users`.

Create `/etc/al/telegram-bot.env`:

```bash
TELEGRAM_BOT_TOKEN=replace-with-botfather-token
TELEGRAM_ALLOWED_CHAT_ID=replace-with-chat-id
AL_BACKEND_URL=https://activity.mempic.com
AL_TELEGRAM_LOG_LEVEL=INFO
```

Deploy the current `main`:

```bash
/opt/al/current/scripts/deploy-server.sh origin/main
```

Issue or renew HTTPS after DNS points to the server:

```bash
certbot --nginx -d activity.mempic.com --non-interactive --agree-tos -m dmitry.shane@gmail.com
ufw delete allow 8000/tcp || true
```

## GitHub Actions Secrets

Add these repository secrets:

```text
DEPLOY_HOST=activity.mempic.com
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
curl https://activity.mempic.com/api/v1/health
```

The dashboard is served from:

```text
https://activity.mempic.com/
```
