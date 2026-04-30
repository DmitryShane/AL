# AGENTS.md

## Project Layout

This repository is the main AL workspace. Work on the backend, frontend, and local tooling here.

- Backend lives in `apps/backend`.
- Frontend lives in `apps/frontend`.
- Local start/stop scripts live in `scripts`.

## Unity And Blender Plugins

The Unity package `com.al.ual` is linked into the Unity project from a separate plugin repository/folder. It is also the active source for the Blender Activity Logger add-on.

When working on Unity or Blender Activity Logger plugin code, edit the linked package folder in the Unity project:

```text
/Volumes/MacMiniExternal2TB/Development/unity-bike-rush-2/Packages/com.al.ual
```

Treat `com.al.ual` as the active plugin source for Unity and Blender work.

When creating new Activity Logger plugins, keep them minimal and purpose-built for logging only. Do not create README files, standalone documentation, extra settings pages, sample apps, or other nonessential scaffolding unless the user explicitly asks for them.

The Blender add-on source lives in:

```text
/Volumes/MacMiniExternal2TB/Development/unity-bike-rush-2/Packages/com.al.ual/blender_al
```

After every code change in `com.al.ual/blender_al`, rebuild the installable add-on archive at `com.al.ual/blender_al.zip`.

The VS Code Activity Logger extension source lives in:

```text
/Volumes/MacMiniExternal2TB/Development/unity-bike-rush-2/Packages/com.al.ual/vscode_al
```

After any change under `vscode_al` (TypeScript in `src/`, `package.json`, or `tsconfig`), the agent must **finish the full workflow in the same session** without asking the user to run commands locally: `cd` into `vscode_al`, run `npm install` when dependencies or the lockfile changed (or when `node_modules` is missing), then run **`npm run package`**. That script runs `npm run compile` (updates `out/`) and **`vsce package`**, which writes the installable **`dist/al-vscode-activity-logger-<version>.vsix`**. Authors install the VSIX from `dist/`; leaving `dist/` stale while claiming the extension is ready is wrong. If you truly only need a TypeScript check without a VSIX, `npm run compile` alone is enough for `out/`, but default to **`npm run package`** whenever the change is meant for end users.

## Backend And Frontend

Continue working on the server and website in this AL repository:

- FastAPI backend: `apps/backend`
- React/Vite frontend: `apps/frontend`

Use these commands for local development:

```bash
start
stop
```

`start` launches the backend and frontend. `stop` shuts them down.

## Terminology

- **AL (Activity Logger)** — the product and service as a whole: this repository (FastAPI backend, web dashboard, Telegram/Discord bots, and how data is stored and summarized).
- **UAL** — specifically the **Unity package** `com.al.ual` and the client plugin(s) that report into AL with `source: ual`. Other editors (Blender, VS Code) use separate add-ons/extensions under the same package tree but are not called “UAL” in user-facing text; prefer **Activity Logger** or **AL** when talking about the system in general.

## Production Data Sync

Production runs at `activity.mempic.com`. When the user asks to pull production data locally, use SSH as `root@activity.mempic.com` and treat MongoDB dumps as sensitive data.

- Production env lives in `/etc/al/backend.env`; the current production MongoDB defaults are `AL_MONGO_URI=mongodb://127.0.0.1:27017` and `AL_MONGO_DATABASE=al`.
- Store dump archives outside the repository, for example under `/tmp/al-prod-sync`. Never commit MongoDB dumps, restored data exports, secrets, or server env files.
- Do not create a local backup before replacing the local `al` database unless the user explicitly asks for one. Local data is treated as disposable during production sync.
- Create the production dump on the server with `mongodump --uri="mongodb://127.0.0.1:27017" --db="al" --archive="/tmp/al-prod-$(date +%Y%m%d-%H%M%S).archive.gz" --gzip`, copy it locally with `scp`, then remove the temporary server archive.
- Restore production data locally with `mongorestore --uri="mongodb://127.0.0.1:27017" --nsInclude="al.*" --drop --archive="<local-prod-dump>.archive.gz" --gzip`.
- After restore, run `start` or `scripts/start-local.sh`, then verify `http://127.0.0.1:8000/api/v1/health` and `http://127.0.0.1:5173/`.
- Normal deployment flow is local work followed by push or merge to `main`; `.github/workflows/deploy.yml` deploys `main` to production automatically.
- Production database changes must never be shipped through git or normal deploy. Only run production imports/restores when the user explicitly asks for that exact operation.
- Before committing, check `git status --short` and do not add database artifacts or env snapshots. The deploy workflow runs `scripts/check-no-data-artifacts.sh` and must stay in place.
