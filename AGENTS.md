# AGENTS.md

## Project Layout

This repository is the main AL workspace. Work on the backend, frontend, and local tooling here.

- Backend lives in `apps/backend`.
- Frontend lives in `apps/frontend`.
- Local start/stop scripts live in `scripts`.

## Unity And Blender Plugins

The Unity package `com.mempic.al` is linked into the Unity project from a separate plugin repository/folder. It is also the active source for the Activity Logger plugins.

When working on Unity or Blender Activity Logger plugin code, edit the linked package folder in the Unity project:

```text
/Volumes/MacMiniExternal2TB/Development/unity-bike-rush-2/Packages/com.mempic.al
```

Treat `com.mempic.al` as the active plugin source for Unity, Blender, VS Code, Cursor, Figma, Codex, and Device Activity Logger work.

The Unity Activity Logger plugin and Device Activity Logger plugin are separate plugins. Do not treat `device_al` as the Unity editor plugin. The Unity editor plugin source lives in `com.mempic.al/unity_al` and reports with `source: ual`; the Device Activity Logger source lives in `com.mempic.al/device_al` and reports with device sources such as `dev`, `dev-ios`, `dev-android`, or `dev-editor`.

When creating new Activity Logger plugins, keep them minimal and purpose-built for logging only. Do not create README files, standalone documentation, extra settings pages, sample apps, or other nonessential scaffolding unless the user explicitly asks for them.

The Device Activity Logger plugin source lives in:

```text
/Volumes/MacMiniExternal2TB/Development/unity-bike-rush-2/Packages/com.mempic.al/device_al
```

After any behavior or end-user change under `device_al`, bump the Device Activity Logger plugin version in `device_al/DeviceAL.cs` (`PluginVersion`) only. Do **not** bump `com.mempic.al/package.json` for Device Activity Logger changes, and do **not** update the Unity project `Packages/manifest.json` tag unless the owner explicitly asks to publish/update the root Unity package tag. The root `com.mempic.al` package version and the Device Activity Logger runtime `PluginVersion` are separate version tracks.

The Blender add-on source lives in:

```text
/Volumes/MacMiniExternal2TB/Development/unity-bike-rush-2/Packages/com.mempic.al/blender_al
```

After every code change in `com.mempic.al/blender_al`, rebuild the installable add-on archive at `com.mempic.al/blender_al/blender_al.zip`.

The VS Code Activity Logger extension source lives in:

```text
/Volumes/MacMiniExternal2TB/Development/unity-bike-rush-2/Packages/com.mempic.al/vscode_al
```

After any change under `vscode_al` (TypeScript in `src/`, `package.json`, or `tsconfig`), the agent must **finish the full workflow in the same session** without asking the user to run commands locally: `cd` into `vscode_al`, run `npm install` when dependencies or the lockfile changed (or when `node_modules` is missing), then run **`npm run package`**. That script runs `npm run compile` (updates `out/`) and **`vsce package`**, which writes the installable **`dist/al-vscode-activity-logger-<version>.vsix`**. Authors install the VSIX from `dist/`; leaving `dist/` stale while claiming the extension is ready is wrong. If you truly only need a TypeScript check without a VSIX, `npm run compile` alone is enough for `out/`, but default to **`npm run package`** whenever the change is meant for end users.

The Cursor Activity Logger extension source lives in:

```text
/Volumes/MacMiniExternal2TB/Development/unity-bike-rush-2/Packages/com.mempic.al/cursor_al
```

After any behavior or end-user change under `cursor_al` (TypeScript in `src/`, `package.json`, or `tsconfig`), the agent must complete the full release/install workflow in the same session: bump the extension version in both `package.json` and `src/config.ts` (`PLUGIN_VERSION`), update the `npm run package` VSIX output filename to match, run `npm install` when dependencies or the lockfile changed (or when `node_modules` is missing), run **`npm run package`** to refresh `out/` and write **`dist/al-cursor-activity-logger-<version>.vsix`**, then install that VSIX locally when the owner asks to update the local Cursor plugin. Use the actual Cursor CLI at `/Applications/Cursor.app/Contents/Resources/app/bin/cursor --install-extension "<vsix-path>" --force`; do **not** use the generic `code` CLI for Cursor plugin installs because it installs into `~/.vscode/extensions`, while Cursor uses `~/.cursor/extensions`. After installation, verify that `~/.cursor/extensions/al.al-cursor-activity-logger-<version>/package.json` exists and the Cursor UI shows the new version. Never leave a changed Cursor plugin at the previous version or with stale `out/` / `dist/` artifacts.

## Backend And Frontend

Continue working on the server and website in this AL repository:

- FastAPI backend: `apps/backend`
- React/Vite frontend: `apps/frontend`

All user-facing website text and code literals must be in English. Do not add Russian text to the website UI, source code, comments, constants, fixtures, or generated artifacts unless the user explicitly asks for a Russian-language deliverable. Russian is only used in chat with the user.

Use these commands for local development:

```bash
start
stop
```

`start` launches the backend and frontend. `stop` shuts them down.

### Backend Tests

The backend uses `uv` from `apps/backend`; do not run backend tests with the system `python` or `python3`.

Use this pattern for backend tests:

```bash
cd apps/backend
uv run pytest tests/test_activity_summary.py
```

For a narrower check, still run it through `uv run` from `apps/backend`, for example `uv run pytest tests/test_activity_summary.py -k idle`.

### Local Rebuild And Restart

When code changes need local verification, decide whether rebuild/restart is actually required and perform it yourself only when necessary.

- After any backend Python runtime change under `apps/backend`, if the local backend is running or the change affects what the local API/UI should show, the agent must restart the local app before claiming the change is ready or locally verified: run `stop`, then `start`, then verify `http://127.0.0.1:8000/api/v1/health`. Do not rely only on direct repository calls, scripts, or unit tests as proof that the running server has loaded the new code.
- Frontend React/CSS changes under `apps/frontend` do not usually require restarting Vite dev server; hot reload should update the browser. If viewing production `dist`, run `npm run build` and restart the process serving `dist`.
- After UI/frontend changes or UI-focused tasks, do **not** run browser/UI verification by default. Only perform browser-based UI checks when the owner explicitly asks for UI verification.
- Dependency, lockfile, env, config, startup script, or build pipeline changes require the relevant install/build/restart step.
- MongoDB data-only changes do not require restart; refresh the page or re-query the API.
- If both backend and frontend runtime code changed and the user needs to see the result locally, prefer a reliable `stop` then `start`, followed by page refresh.
- Only skip the backend restart when the user explicitly says not to restart, no backend runtime code changed, or the backend is not running and the user did not ask for local API/UI verification. If the restart is skipped after backend work, state that explicitly in the final response.
- Do not rebuild or restart just by habit. Do it when it is needed for the user's requested verification or for the running app to pick up the change.

### Hourly Activity Chart Semantics

On the hourly activity chart, the black `missed` fill is visual-only and has a narrow meaning: it may be used only before the Telegram work day starts, or after an explicit offline trigger/sign-off when the day has ended or the author is known to be offline.

Do not use, suggest, or preserve black `missed` fill for holes inside an active work day. In-workday gaps must be explained by real tracked categories such as active, idle, break/AFK, meeting, or overtime; if a normal working-hour column is visually incomplete, investigate missing accounting data instead of filling it with `missed`.

### Activity Card Status Semantics

On Activity author cards, red offline is reserved for an active workday failure: the author has started their workday, reports are expected, and plugin reports have stopped.

Use grey offline for authors who have not started their current workday yet, authors after an explicit Telegram offline/sign-off, and historical snapshots. Do not mark those states as red `reports_stopped`.

### Agents: red offline is not “Telegram offline”

When debugging summaries, `status_events`, or idle accounting:

- **Red author-card offline** maps to **`reports_stopped`** (plugin reports stopped while the workday expects them). It is **not** the same thing as Telegram chat offline or Telegram day boundaries.
- Transitions are stored as **`status_events`** with `statusEventType` `offline`/`online` and **`reason` values such as `reports_stopped` / `reports_resumed`**. Activity aggregation treats these as generic offline/online intervals (`activity_aggregation._status_interval_context_for_event`); it does not branch on Telegram vs plugin semantics there.
- Persisting plugin stale transitions (`status_events`, status `report_rows`) is implemented in **`author_status_events.py`** (`record_status_event`, `_record_status_transition_for_author`).

## Terminology

- **AL (Activity Logger)** — the product and service as a whole: this repository (FastAPI backend, web dashboard, Telegram/Discord bots, and how data is stored and summarized).
- **Unity AL** — specifically the Unity editor plugin under `com.mempic.al/unity_al`. It reports into AL with historical `source: ual`. Other editors (Blender, VS Code, Cursor, Figma, Codex) use separate add-ons/extensions under the same package tree; prefer **Activity Logger** or **AL** when talking about the system in general.

## Communication With The Repo Owner

- Default to **short, direct answers** unless the user asks for depth, a tutorial, or a formal write-up. Skip long preamble and filler.
- In chat, always answer the owner briefly and concisely. Do not add broad context, extra details, optional explanations, or implementation notes unless the owner explicitly asks for them.
- The user is the repository owner. When the owner asks to check "my" data, reports, activity, profile, or similar site data, interpret that as the website author **Dmitry Shane** unless the owner explicitly names another author.
- The owner may write in Russian in chat; **repository and product text stay English** per the rule above. **Russian in chat is not an implicit request for Russian in the product** — if the owner describes desired wording in Russian (alerts, bot copy, UI labels, emails), implement it in **English** unless they clearly ask for a **Russian-language deliverable** (for example: “пусть в боте будет по-русски”, “Russian locale for Telegram”).
- If the owner is chatting in Russian, keep chat replies in Russian even when the immediate task text, pasted plan, command, or implementation request is written in English (for example, “PLEASE IMPLEMENT THIS PLAN”). English task phrasing is not a request to switch chat responses to English.
- Write implementation plans for the owner in Russian, because plans are part of chat communication. Keep repository and product text in English unless explicitly asked otherwise.

## MongoDB Data Layout (backend)

- Storage is **MongoDB** (`AL_MONGO_DATABASE`, usually `al`), not a single file: many **collections**, see `apps/backend/al_backend/repository.py` → `ensure_indexes()`.
- **Ingest / raw**: `raw_reports`, `raw_event_batches`, `raw_activity_events` (event-level, unique `eventId`).
- **Derived / reporting**: `report_rows`, `daily_author_activity` (per author/source/project/day), `activity_author_day_summary_snapshots` (per-author/day historical Activity read model), `activity_day_summary_snapshots` (composed historical Activity day summaries), `activity_snapshots`, `day_sessions`, plus author data in `author_profiles` / `author_aliases`.
- **Weeks / months** in the UI are built from daily (and related) data at query time, not one monolithic “all history” document per author.
- For future analytics and historical Activity features, prefer per-author/day snapshots first, then composed day snapshots. Do not build new historical analytics by repeatedly scanning raw reports or `report_rows` at request time unless explicitly required.
- **Performance**: compound and sparse indexes on the hot fields; aggregate docs are versioned (`aggregates_version`) and rebuilt when the backend bumps that version (`rebuild_aggregates_if_needed`).

## Production Data Sync

Production runs at `activity.mempic.com`. When the user asks to pull production data locally, use SSH as `root@activity.mempic.com` and treat MongoDB dumps as sensitive data.

- In owner chat, phrases like **"обнови БД"**, **"обнови базу"**, or **"обнови локальную БД"** mean: first ask which scope to sync from production before running anything: **today**, **week**, or **full database**. Do not infer the scope.
- If the owner asks for a production database rebuild, first offer the scope choice: **today**, **specific day**, or **full history**. The safe/default production rebuild scope is **today only**, across all authors.
- Never run a full-history production aggregate rebuild by default. Only run a full production rebuild when the owner explicitly asks to rebuild the entire production history.
- If the available code path only supports `rebuild_aggregates_if_needed(force=True)` as a full-history rebuild, stop instead of running it on production; implement or use a day/date-range scoped rebuild path first.
- Production env lives in `/etc/al/backend.env`; the current production MongoDB defaults are `AL_MONGO_URI=mongodb://127.0.0.1:27017` and `AL_MONGO_DATABASE=al`.
- The production server's system Python does not have `pymongo`; for direct production MongoDB inspection, use `mongosh` over SSH instead of trying to run ad hoc Python scripts or changing the production Python environment.
- Never run `uv run`, `uv sync`, `pip install`, package installation, or any command that may mutate `.venv` as `root` inside `/opt/al/current` or any production app checkout. Production app files and virtualenv must remain owned by the `al` user so deploy can update them. If Python app context is required on production, run it as `sudo -H -u al env HOME=/opt/al ...` from the app directory, or use a temporary directory outside `/opt/al/current` for isolated diagnostics. After any accidental root-owned artifact under `/opt/al/current`, fix ownership before deploy with `chown -R al:al /opt/al/current/apps/backend/.venv` and verify no root-owned files remain.
- Store dump archives outside the repository, for example under `/tmp/al-prod-sync`. Never commit MongoDB dumps, restored data exports, secrets, or server env files.
- Do not create a local backup before replacing the local `al` database unless the user explicitly asks for one. Local data is treated as disposable during production sync.
- Create the production dump on the server with `mongodump --uri="mongodb://127.0.0.1:27017" --db="al" --archive="/tmp/al-prod-$(date +%Y%m%d-%H%M%S).archive.gz" --gzip`, copy it locally with `scp`, then remove the temporary server archive.
- Restore production data locally with `mongorestore --uri="mongodb://127.0.0.1:27017" --nsInclude="al.*" --drop --archive="<local-prod-dump>.archive.gz" --gzip`.
- For scoped production syncs, replace only documents in the selected date range locally. Do not drop the entire local `al` database unless the owner selected **full database**.
- After restore, run `start` or `scripts/start-local.sh`, then verify `http://127.0.0.1:8000/api/v1/health` and `http://127.0.0.1:5173/`.
- Normal deployment flow is local work followed by push or merge to `main`; `.github/workflows/deploy.yml` deploys `main` to production automatically.
- Production database changes must never be shipped through git or normal deploy. Only run production imports/restores when the user explicitly asks for that exact operation.
- Before committing, check `git status --short` and do not add database artifacts or env snapshots. The deploy workflow runs `scripts/check-no-data-artifacts.sh` and must stay in place.

## Push Command

- In owner chat, the word **"пуш"** means: review current changes, write a short accurate commit message, commit **all visible changes in the current `AL` repository** (`/Volumes/MacMiniExternal2TB/Development/AL`), and push that repository so the automatic deploy workflow can update production.
- Do not leave changed `AL` files out of the commit just because they look unrelated to the latest task. Include them and make the commit message broad enough to describe all included changes.
- Do not commit or push any linked/external repository (for example `/Volumes/MacMiniExternal2TB/Development/unity-bike-rush-2` or `Packages/com.mempic.al`) as part of **"пуш"**. Only do that when the user explicitly names that repository or separately asks to push it.
- Before committing, inspect `git status --short` and the diff. Do not include secrets, environment files, MongoDB dumps, restored data exports, or generated data artifacts.
- Keep the commit message concise and accurate. After pushing, report the commit message and push result briefly.
