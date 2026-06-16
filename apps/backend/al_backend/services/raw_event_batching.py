from __future__ import annotations

import os


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default

    return max(1, value)


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() not in {"0", "false", "no", "off"}


RAW_EVENT_ACCOUNTING_SUB_BATCH_SIZE = _positive_int_env("AL_REBUILD_RAW_EVENT_BATCH_SIZE", 200)
REBUILD_RAW_FLUSH_BATCHES = _positive_int_env("AL_REBUILD_RAW_FLUSH_BATCHES", 5)
REBUILD_CURSOR_BATCH_SIZE = _positive_int_env("AL_REBUILD_CURSOR_BATCH_SIZE", 200)
REBUILD_MEMORY_SOFT_LIMIT_MB = _positive_int_env("AL_REBUILD_MEMORY_SOFT_LIMIT_MB", 400)
REBUILD_MEMORY_GUARD_ENABLED = _bool_env("AL_REBUILD_MEMORY_GUARD_ENABLED", True)
REBUILD_DELTA_SPILL_THRESHOLD = _positive_int_env("AL_REBUILD_DELTA_SPILL_THRESHOLD", 20_000)
REBUILD_BATCH_DELTA_CHUNK_SIZE = _positive_int_env("AL_REBUILD_BATCH_DELTA_CHUNK_SIZE", 500)
EDITOR_INPUT_COMPACTION_WINDOW_SECONDS = _positive_int_env("AL_EDITOR_INPUT_COMPACTION_WINDOW_SECONDS", 30)
REBUILD_FAST_ACCOUNTING_ENABLED = _bool_env("AL_REBUILD_FAST_ACCOUNTING_ENABLED", True)
REBUILD_SLOW_EVENT_THRESHOLD_MS = _positive_int_env("AL_REBUILD_SLOW_EVENT_THRESHOLD_MS", 250)
