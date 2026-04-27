#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

chmod +x .githooks/pre-commit .githooks/post-commit
git config core.hooksPath .githooks
git config push.followTags true

echo "Git hooks installed from .githooks"
