# Reminders

- Later configure automatic deployment to the external server when commits land in the main production branch.
- Add a website button to manually fetch/request data for all authors.
- Add a website button to manually fetch/request data for one selected author.
- Add UI for global report send interval shared by all authors.
- Add UI for individual author interval overrides.
- Move private keys and production secrets out of the repository before public deployment.
- Before publishing, configure the Telegram bot worker on the server with `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID`, and production `AL_BACKEND_URL`.
- Before publishing, verify the default backend URL in Unity and Blender plugins is the production endpoint: `http://64.225.108.88:8000`.
