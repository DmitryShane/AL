#!/usr/bin/env python3
"""Force-download GitHub avatars for all author profiles (same as POST /authors/avatars/refresh-all)."""

from __future__ import annotations

import json
import sys

from al_backend.container import BackendContainer
from al_backend.settings import load_settings


def main() -> int:
    settings = load_settings()
    container = BackendContainer(settings)
    try:
        result = container.services.refresh_all_author_github_avatars()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    finally:
        container.close()


if __name__ == "__main__":
    sys.exit(main())
