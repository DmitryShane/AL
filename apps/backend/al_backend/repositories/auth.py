from __future__ import annotations

from ..api_security import SESSION_MAX_AGE_SECONDS
from ..activity_math import *
from ..mongo_composable import MongoComposableMixin


class AuthRepository(MongoComposableMixin):
    def ensure_bootstrap_site_admin(self, email: str, password: str) -> None:
        normalized_email = _normalize_email(email)

        if not normalized_email or not password:
            return

        now = dt.datetime.now(dt.UTC)
        existing = self.db.site_users.find_one({"email": normalized_email}, {"_id": 1, "passwordHash": 1})
        update = {
            "email": normalized_email,
            "displayName": normalized_email,
            "role": "admin",
            "active": True,
            "updatedAt": now,
        }

        if existing:
            self.db.site_users.update_one({"email": normalized_email}, {"$set": update})
            return

        update["passwordHash"] = hash_password(password)
        update["createdAt"] = now
        self.db.site_users.update_one({"email": normalized_email}, {"$set": update}, upsert=True)

    def _author_profile_for_site_user_email(self, email: str) -> dict[str, Any] | None:
        normalized = _normalize_email(email)

        if not normalized:
            return None

        profile = self.db.author_profiles.find_one({"authorEmail": normalized})

        if profile:
            return profile

        return self.db.author_profiles.find_one({"rawAuthor": normalized})

    def _public_site_user_with_avatar(self, user: dict[str, Any]) -> dict[str, Any]:
        public = _public_site_user(user)
        profile = self._author_profile_for_site_user_email(public.get("email", ""))

        if not profile:
            return public

        raw_author = str(profile.get("rawAuthor") or "").strip()
        gh_user = _github_username_for_avatar_fetch(raw_author, profile)
        url = _cached_author_avatar_api_url(raw_author, gh_user, profile)

        if url:
            public["avatarUrl"] = url

        return public

    def authenticate_site_user(self, email: str, password: str) -> dict[str, Any] | None:
        normalized_email = _normalize_email(email)

        if not normalized_email or not password:
            return None

        user = self.db.site_users.find_one({"email": normalized_email, "active": True})

        if not user or not verify_password(password, user.get("passwordHash", "")):
            return None

        return self._public_site_user_with_avatar(user)

    def create_site_session(self, email: str) -> str:
        token = new_session_token()
        now = dt.datetime.now(dt.UTC)
        expires_at = now + dt.timedelta(seconds=SESSION_MAX_AGE_SECONDS)
        self.db.site_sessions.insert_one(
            {
                "tokenHash": session_token_hash(token),
                "email": _normalize_email(email),
                "createdAt": now,
                "expiresAt": expires_at,
            }
        )
        return token

    def site_user_for_session(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None

        token_hash = session_token_hash(token)
        session = self.db.site_sessions.find_one({"tokenHash": token_hash, "expiresAt": {"$gt": dt.datetime.now(dt.UTC)}})

        if not session:
            return None

        user = self.db.site_users.find_one({"email": session.get("email"), "active": True})

        if not user:
            return None

        return self._public_site_user_with_avatar(user)

    def delete_site_session(self, token: str | None) -> None:
        if token:
            self.db.site_sessions.delete_one({"tokenHash": session_token_hash(token)})

    def site_users(self) -> list[dict[str, Any]]:
        return [self._public_site_user_with_avatar(user) for user in self.db.site_users.find({}, {"passwordHash": 0}).sort("email", ASCENDING)]

    def upsert_site_user(
        self,
        email: str,
        display_name: str | None,
        role: str,
        can_view_server_stats: bool,
        active: bool,
        password: str | None = None,
    ) -> dict[str, Any]:
        normalized_email = _normalize_email(email)

        if not normalized_email:
            return {"ok": False, "error": "Email is required"}

        if role not in {"admin", "editor", "viewer"}:
            return {"ok": False, "error": "Invalid role"}

        existing = self.db.site_users.find_one({"email": normalized_email})

        if not existing and not password:
            return {"ok": False, "error": "Password is required for new users"}

        now = dt.datetime.now(dt.UTC)
        update = {
            "email": normalized_email,
            "displayName": (display_name or normalized_email).strip(),
            "role": role,
            "canViewServerStats": can_view_server_stats,
            "active": active,
            "updatedAt": now,
        }

        if password:
            update["passwordHash"] = hash_password(password)

        operation: dict[str, Any] = {"$set": update}

        if not existing:
            operation["$setOnInsert"] = {"createdAt": now}

        self.db.site_users.update_one({"email": normalized_email}, operation, upsert=True)
        user = self.db.site_users.find_one({"email": normalized_email}) or update
        return {"ok": True, "user": self._public_site_user_with_avatar(user)}

    def delete_site_user(self, email: str) -> dict[str, Any]:
        normalized_email = _normalize_email(email)
        result = self.db.site_users.delete_one({"email": normalized_email})
        self.db.site_sessions.delete_many({"email": normalized_email})
        return {"ok": True, "deleted": result.deleted_count}

