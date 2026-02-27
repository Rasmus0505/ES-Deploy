from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any


def _now_ms() -> int:
    from time import time

    return int(round(time() * 1000))


class SqliteAuthStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._db_path), timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS auth_users (
                        user_id TEXT PRIMARY KEY,
                        username TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        password_salt TEXT NOT NULL,
                        is_active INTEGER NOT NULL DEFAULT 1,
                        created_at INTEGER NOT NULL DEFAULT 0,
                        updated_at INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS auth_sessions (
                        session_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        token_jti TEXT NOT NULL UNIQUE,
                        issued_at INTEGER NOT NULL DEFAULT 0,
                        expires_at INTEGER NOT NULL DEFAULT 0,
                        revoked_at INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id, issued_at DESC)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_auth_sessions_exp ON auth_sessions(expires_at)"
                )
                connection.commit()

    @staticmethod
    def _normalize_username(username: str) -> str:
        return str(username or "").strip().lower()

    def count_users(self) -> int:
        with self._lock:
            with self._connect() as connection:
                row = connection.execute("SELECT COUNT(1) AS total FROM auth_users").fetchone()
        return int(row["total"] or 0) if row else 0

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        safe_username = self._normalize_username(username)
        if not safe_username:
            return None
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT user_id, username, password_hash, password_salt, is_active, created_at, updated_at
                    FROM auth_users
                    WHERE username=?
                    LIMIT 1
                    """,
                    (safe_username,),
                ).fetchone()
        if not row:
            return None
        return {
            "user_id": str(row["user_id"] or ""),
            "username": str(row["username"] or ""),
            "password_hash": str(row["password_hash"] or ""),
            "password_salt": str(row["password_salt"] or ""),
            "is_active": bool(int(row["is_active"] or 0)),
            "created_at": int(row["created_at"] or 0),
            "updated_at": int(row["updated_at"] or 0),
        }

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        safe_user_id = str(user_id or "").strip()
        if not safe_user_id:
            return None
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT user_id, username, password_hash, password_salt, is_active, created_at, updated_at
                    FROM auth_users
                    WHERE user_id=?
                    LIMIT 1
                    """,
                    (safe_user_id,),
                ).fetchone()
        if not row:
            return None
        return {
            "user_id": str(row["user_id"] or ""),
            "username": str(row["username"] or ""),
            "password_hash": str(row["password_hash"] or ""),
            "password_salt": str(row["password_salt"] or ""),
            "is_active": bool(int(row["is_active"] or 0)),
            "created_at": int(row["created_at"] or 0),
            "updated_at": int(row["updated_at"] or 0),
        }

    def create_user(self, *, user_id: str, username: str, password_hash: str, password_salt: str) -> dict[str, Any]:
        safe_user_id = str(user_id or "").strip()
        safe_username = self._normalize_username(username)
        if not safe_user_id or not safe_username:
            raise ValueError("user_id and username are required")
        now = _now_ms()
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO auth_users(
                        user_id, username, password_hash, password_salt, is_active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    (safe_user_id, safe_username, str(password_hash or ""), str(password_salt or ""), now, now),
                )
                connection.commit()
        created = self.get_user_by_id(safe_user_id)
        if not created:
            raise RuntimeError("create_user failed")
        return created

    def create_session(
        self,
        *,
        session_id: str,
        user_id: str,
        token_jti: str,
        issued_at: int,
        expires_at: int,
    ) -> None:
        safe_session = str(session_id or "").strip()
        safe_user = str(user_id or "").strip()
        safe_jti = str(token_jti or "").strip()
        if not safe_session or not safe_user or not safe_jti:
            raise ValueError("session_id/user_id/token_jti are required")
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO auth_sessions(session_id, user_id, token_jti, issued_at, expires_at, revoked_at)
                    VALUES (?, ?, ?, ?, ?, 0)
                    """,
                    (safe_session, safe_user, safe_jti, int(issued_at or 0), int(expires_at or 0)),
                )
                connection.commit()

    def is_session_active(self, *, user_id: str, token_jti: str, now_ms: int) -> bool:
        safe_user = str(user_id or "").strip()
        safe_jti = str(token_jti or "").strip()
        if not safe_user or not safe_jti:
            return False
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT revoked_at, expires_at
                    FROM auth_sessions
                    WHERE user_id=? AND token_jti=?
                    LIMIT 1
                    """,
                    (safe_user, safe_jti),
                ).fetchone()
        if not row:
            return False
        revoked_at = int(row["revoked_at"] or 0)
        expires_at = int(row["expires_at"] or 0)
        if revoked_at > 0:
            return False
        if expires_at <= int(now_ms or 0):
            return False
        return True

    def revoke_session(self, *, token_jti: str) -> None:
        safe_jti = str(token_jti or "").strip()
        if not safe_jti:
            return
        now = _now_ms()
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE auth_sessions SET revoked_at=? WHERE token_jti=? AND revoked_at=0",
                    (now, safe_jti),
                )
                connection.commit()

