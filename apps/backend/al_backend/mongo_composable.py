"""Shared typing/runtime hook for mixin services wired by BackendServices."""

from __future__ import annotations

from pymongo.database import Database


class MongoComposableMixin:
    """``BackendServices.__init__`` assigns ``self.db``; this mixin documents it for type checkers."""

    db: Database
