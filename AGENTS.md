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

## Blender Plugin

The Blender Activity Logger add-on source lives in `packages/blender_al`.

After every code change in `packages/blender_al`, rebuild the installable add-on archive:

```bash
cd /Volumes/MacMiniExternal2TB/Development/AL/packages
rm -f blender_al.zip
zip -r blender_al.zip blender_al -x "*/__pycache__/*" "*.pyc"
```

Treat `packages/blender_al.zip` as the current installable Blender add-on archive.

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
