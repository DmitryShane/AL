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

The Blender add-on source lives in:

```text
/Volumes/MacMiniExternal2TB/Development/unity-bike-rush-2/Packages/com.al.ual/blender_al
```

After every code change in `com.al.ual/blender_al`, rebuild the installable add-on archive at `com.al.ual/blender_al.zip`.

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
