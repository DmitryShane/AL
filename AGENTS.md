# AGENTS.md

## Project Layout

This repository is the main AL workspace. Work on the backend, frontend, and local tooling here.

- Backend lives in `apps/backend`.
- Frontend lives in `apps/frontend`.
- Local start/stop scripts live in `scripts`.

## Unity Plugin

The Unity package `com.al.ual` is linked into the Unity project from a separate plugin repository/folder.

When working on the Unity Activity Logger plugin, edit the linked package folder in the Unity project:

```text
/Volumes/MacMiniExternal2TB/Development/unity-bike-rush-2/Packages/com.al.ual
```

Treat `com.al.ual` as the active plugin source for Unity work.

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
