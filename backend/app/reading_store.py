from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from app.provider_url_rules import DEFAULT_LLM_BASE_URL
from app.security_crypto import decrypt_secret, encrypt_secret


def _now_ms() -> int:
    from time import time

    return int(round(time() * 1000))


LEVEL_META: dict[str, tuple[float, str]] = {
    "junior": (3.5, "A2"),
    "senior": (5.5, "A2-B1"),
    "cet4": (7.5, "B1"),
    "cet6": (9.5, "B2"),
    "kaoyan": (10.5, "B2-C1"),
    "toefl": (11.5, "C1-C2"),
    "sat": (11.5, "C1-C2"),
}


class SqliteReadingStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._db_path), timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _normalize_name(value: str) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_user_id(user_id: str | None) -> str:
        safe = str(user_id or "").strip()
        return safe or "legacy"

    @staticmethod
    def _json_dump(payload: Any, fallback: str) -> str:
        try:
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            return fallback

    @staticmethod
    def _json_load(raw: str, fallback: Any) -> Any:
        try:
            return json.loads(raw or "")
        except Exception:
            return fallback

    @staticmethod
    def _normalize_llm_payload(raw: Any) -> dict[str, Any]:
        data = raw if isinstance(raw, dict) else {}
        return {
            "base_url": str(data.get("base_url") or "").strip() or DEFAULT_LLM_BASE_URL,
            "api_key": str(data.get("api_key") or "").strip(),
            "model": str(data.get("model") or "").strip() or "gpt-5.2",
            "llm_support_json": bool(data.get("llm_support_json", False)),
        }

    def _encrypt_llm_payload(self, raw: Any) -> dict[str, Any]:
        payload = self._normalize_llm_payload(raw)
        payload["api_key"] = encrypt_secret(str(payload.get("api_key") or "").strip())
        return payload

    def _decrypt_llm_payload(self, raw: Any) -> dict[str, Any]:
        payload = self._normalize_llm_payload(raw)
        payload["api_key"] = decrypt_secret(str(payload.get("api_key") or "").strip())
        return payload

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reading_sources (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL DEFAULT 'legacy',
                        video_name TEXT NOT NULL,
                        srt_name TEXT NOT NULL,
                        source_text TEXT NOT NULL DEFAULT '',
                        translation_text TEXT NOT NULL DEFAULT '',
                        summary_terms_json TEXT NOT NULL DEFAULT '{}',
                        subtitle_count INTEGER NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL DEFAULT 0,
                        updated_at INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                source_columns = {
                    str(row["name"]).strip()
                    for row in connection.execute("PRAGMA table_info(reading_sources)").fetchall()
                }
                if "user_id" not in source_columns:
                    connection.execute("ALTER TABLE reading_sources ADD COLUMN user_id TEXT NOT NULL DEFAULT 'legacy'")
                connection.execute("DROP INDEX IF EXISTS idx_reading_sources_unique")
                connection.execute("DROP INDEX IF EXISTS idx_reading_sources_updated")
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_reading_sources_unique ON reading_sources(user_id, video_name, srt_name)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_reading_sources_updated ON reading_sources(user_id, updated_at DESC)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reading_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS profile_settings_v2 (
                        user_id TEXT PRIMARY KEY,
                        english_level TEXT NOT NULL DEFAULT 'cet4',
                        english_level_numeric REAL NOT NULL DEFAULT 7.5,
                        english_level_cefr TEXT NOT NULL DEFAULT 'B1',
                        llm_mode TEXT NOT NULL DEFAULT 'unified',
                        llm_unified_json TEXT NOT NULL DEFAULT '{}',
                        llm_listening_json TEXT NOT NULL DEFAULT '{}',
                        llm_reading_json TEXT NOT NULL DEFAULT '{}',
                        updated_at INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                legacy_profile = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='profile_settings' LIMIT 1"
                ).fetchone()
                if legacy_profile:
                    legacy_profile_columns = {
                        str(row["name"]).strip()
                        for row in connection.execute("PRAGMA table_info(profile_settings)").fetchall()
                    }
                    if "user_id" not in legacy_profile_columns:
                        legacy_row = connection.execute(
                            """
                            SELECT english_level, english_level_numeric, english_level_cefr, llm_mode,
                                   llm_unified_json, llm_listening_json, llm_reading_json, updated_at
                            FROM profile_settings
                            WHERE id=1
                            LIMIT 1
                            """
                        ).fetchone()
                        if legacy_row:
                            connection.execute(
                                """
                                INSERT OR REPLACE INTO profile_settings_v2(
                                    user_id, english_level, english_level_numeric, english_level_cefr, llm_mode,
                                    llm_unified_json, llm_listening_json, llm_reading_json, updated_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    "legacy",
                                    str(legacy_row["english_level"] or "cet4"),
                                    float(legacy_row["english_level_numeric"] or 7.5),
                                    str(legacy_row["english_level_cefr"] or "B1"),
                                    str(legacy_row["llm_mode"] or "unified"),
                                    str(legacy_row["llm_unified_json"] or "{}"),
                                    str(legacy_row["llm_listening_json"] or "{}"),
                                    str(legacy_row["llm_reading_json"] or "{}"),
                                    int(legacy_row["updated_at"] or 0),
                                ),
                            )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reading_versions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        version_id TEXT NOT NULL UNIQUE,
                        user_id TEXT NOT NULL DEFAULT 'legacy',
                        source_id INTEGER NOT NULL,
                        user_level TEXT NOT NULL DEFAULT 'cet4',
                        scope TEXT NOT NULL DEFAULT 'all',
                        ratio_preset TEXT NOT NULL DEFAULT 'long_term',
                        difficulty_tier TEXT NOT NULL DEFAULT 'balanced',
                        genre TEXT NOT NULL DEFAULT 'news',
                        i_plus_one_hit INTEGER NOT NULL DEFAULT 0,
                        config_json TEXT NOT NULL DEFAULT '{}',
                        difficulty_report_json TEXT NOT NULL DEFAULT '{}',
                        materials_json TEXT NOT NULL DEFAULT '[]',
                        quiz_json TEXT NOT NULL DEFAULT '{}',
                        pipeline_version TEXT NOT NULL DEFAULT 'reading_v2_v2',
                        created_at INTEGER NOT NULL DEFAULT 0,
                        updated_at INTEGER NOT NULL DEFAULT 0,
                        FOREIGN KEY (source_id) REFERENCES reading_sources(id) ON DELETE CASCADE
                    )
                    """
                )
                version_columns = {
                    str(row["name"]).strip()
                    for row in connection.execute("PRAGMA table_info(reading_versions)").fetchall()
                }
                if "user_id" not in version_columns:
                    connection.execute("ALTER TABLE reading_versions ADD COLUMN user_id TEXT NOT NULL DEFAULT 'legacy'")
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_reading_versions_source_updated ON reading_versions(source_id, updated_at DESC)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_reading_versions_updated ON reading_versions(user_id, updated_at DESC)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reading_short_answer_attempts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        attempt_id TEXT NOT NULL UNIQUE,
                        user_id TEXT NOT NULL DEFAULT 'legacy',
                        version_id TEXT NOT NULL,
                        question_id TEXT NOT NULL,
                        answer_text TEXT NOT NULL DEFAULT '',
                        result_json TEXT NOT NULL DEFAULT '{}',
                        created_at INTEGER NOT NULL DEFAULT 0,
                        FOREIGN KEY (version_id) REFERENCES reading_versions(version_id) ON DELETE CASCADE
                    )
                    """
                )
                short_columns = {
                    str(row["name"]).strip()
                    for row in connection.execute("PRAGMA table_info(reading_short_answer_attempts)").fetchall()
                }
                if "user_id" not in short_columns:
                    connection.execute(
                        "ALTER TABLE reading_short_answer_attempts ADD COLUMN user_id TEXT NOT NULL DEFAULT 'legacy'"
                    )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_short_answer_version_question_created ON reading_short_answer_attempts(user_id, version_id, question_id, created_at DESC)"
                )
                self._clear_legacy_reading_materials_once(connection)
                self._ensure_default_profile(connection, user_id="legacy")
                connection.commit()

    def _clear_legacy_reading_materials_once(self, connection: sqlite3.Connection) -> None:
        marker = connection.execute(
            "SELECT value FROM reading_meta WHERE key='legacy_reading_materials_cleared_v1' LIMIT 1"
        ).fetchone()
        if marker:
            return
        legacy = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reading_materials' LIMIT 1"
        ).fetchone()
        if legacy:
            connection.execute("DROP TABLE IF EXISTS reading_materials")
        connection.execute(
            "INSERT OR REPLACE INTO reading_meta(key, value) VALUES (?, ?)",
            ("legacy_reading_materials_cleared_v1", str(_now_ms())),
        )

    def _ensure_default_profile(self, connection: sqlite3.Connection, *, user_id: str = "legacy") -> None:
        safe_user_id = self._normalize_user_id(user_id)
        row = connection.execute(
            "SELECT user_id FROM profile_settings_v2 WHERE user_id=? LIMIT 1",
            (safe_user_id,),
        ).fetchone()
        if row:
            return
        default_llm = self._encrypt_llm_payload(
            {
            "base_url": DEFAULT_LLM_BASE_URL,
            "api_key": "",
            "model": "gpt-5.2",
            "llm_support_json": False,
            }
        )
        connection.execute(
            """
            INSERT INTO profile_settings_v2(
                user_id, english_level, english_level_numeric, english_level_cefr, llm_mode,
                llm_unified_json, llm_listening_json, llm_reading_json, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                safe_user_id,
                "cet4",
                7.5,
                "B1",
                "unified",
                self._json_dump(default_llm, "{}"),
                self._json_dump(default_llm, "{}"),
                self._json_dump(default_llm, "{}"),
                _now_ms(),
            ),
        )

    def upsert_source(
        self,
        *,
        user_id: str = "legacy",
        video_name: str,
        srt_name: str,
        subtitles: list[dict[str, Any]] | None = None,
        summary_terms: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        safe_user_id = self._normalize_user_id(user_id)
        safe_video = self._normalize_name(video_name)
        safe_srt = self._normalize_name(srt_name)
        if not safe_video or not safe_srt:
            return None
        rows: list[dict[str, str]] = []
        for item in subtitles or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            rows.append({"text": text, "translation": str(item.get("translation") or "").strip()})
        if not rows:
            return None
        source_text = "\n".join(item["text"] for item in rows).strip()
        translation_text = "\n".join(item["translation"] for item in rows if item["translation"]).strip()
        now = _now_ms()
        with self._lock:
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT id FROM reading_sources WHERE user_id=? AND video_name=? AND srt_name=? LIMIT 1",
                    (safe_user_id, safe_video, safe_srt),
                ).fetchone()
                if existing:
                    connection.execute(
                        """
                        UPDATE reading_sources
                        SET source_text=?, translation_text=?, summary_terms_json=?, subtitle_count=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            source_text,
                            translation_text,
                            self._json_dump(summary_terms if isinstance(summary_terms, dict) else {}, "{}"),
                            len(rows),
                            now,
                            int(existing["id"]),
                        ),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO reading_sources(
                            user_id, video_name, srt_name, source_text, translation_text, summary_terms_json,
                            subtitle_count, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            safe_user_id,
                            safe_video,
                            safe_srt,
                            source_text,
                            translation_text,
                            self._json_dump(summary_terms if isinstance(summary_terms, dict) else {}, "{}"),
                            len(rows),
                            now,
                            now,
                        ),
                    )
                connection.commit()
        return self.get_source(user_id=safe_user_id, video_name=safe_video, srt_name=safe_srt)

    def list_sources(self, *, user_id: str = "legacy", limit: int = 200) -> list[dict[str, Any]]:
        safe_user_id = self._normalize_user_id(user_id)
        safe_limit = max(1, min(1000, int(limit or 200)))
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT video_name, srt_name, subtitle_count, summary_terms_json, updated_at
                    FROM reading_sources
                    WHERE user_id=?
                    ORDER BY updated_at DESC, id DESC
                    LIMIT ?
                    """,
                    (safe_user_id, safe_limit),
                ).fetchall()
        data: list[dict[str, Any]] = []
        for row in rows:
            summary_terms = self._json_load(str(row["summary_terms_json"] or "{}"), {})
            terms = summary_terms.get("terms") if isinstance(summary_terms, dict) else []
            data.append(
                {
                    "video_name": str(row["video_name"] or ""),
                    "srt_name": str(row["srt_name"] or ""),
                    "subtitle_count": int(row["subtitle_count"] or 0),
                    "updated_at": int(row["updated_at"] or 0),
                    "has_summary_terms": bool(isinstance(terms, list) and len(terms) > 0),
                }
            )
        return data

    def get_source(self, *, user_id: str = "legacy", video_name: str, srt_name: str) -> dict[str, Any] | None:
        safe_user_id = self._normalize_user_id(user_id)
        safe_video = self._normalize_name(video_name)
        safe_srt = self._normalize_name(srt_name)
        if not safe_video or not safe_srt:
            return None
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT id, video_name, srt_name, source_text, translation_text, summary_terms_json, subtitle_count, created_at, updated_at
                    FROM reading_sources
                    WHERE user_id=? AND video_name=? AND srt_name=?
                    LIMIT 1
                    """,
                    (safe_user_id, safe_video, safe_srt),
                ).fetchone()
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "video_name": str(row["video_name"] or ""),
            "srt_name": str(row["srt_name"] or ""),
            "source_text": str(row["source_text"] or ""),
            "translation_text": str(row["translation_text"] or ""),
            "summary_terms": self._json_load(str(row["summary_terms_json"] or "{}"), {}),
            "subtitle_count": int(row["subtitle_count"] or 0),
            "created_at": int(row["created_at"] or 0),
            "updated_at": int(row["updated_at"] or 0),
        }

    def get_material(
        self,
        *,
        user_id: str = "legacy",
        video_name: str,
        srt_name: str,
        user_level: str,
        pipeline_version: str,
        scope: str = "",
        ratio_preset: str = "",
        difficulty_tier: str = "",
        genre: str = "",
    ) -> dict[str, Any] | None:
        safe_user_id = self._normalize_user_id(user_id)
        safe_video = self._normalize_name(video_name)
        safe_srt = self._normalize_name(srt_name)
        safe_level = self._normalize_name(user_level).lower() or "cet4"
        safe_pipeline = self._normalize_name(pipeline_version) or "reading_v2_v2"
        if not safe_video or not safe_srt:
            return None
        query = [
            f"{self._select_version_sql()} WHERE v.user_id=? AND s.user_id=? AND s.video_name=? AND s.srt_name=? AND v.user_level=? AND v.pipeline_version=?"
        ]
        params: list[Any] = [safe_user_id, safe_user_id, safe_video, safe_srt, safe_level, safe_pipeline]

        def _maybe_append(field: str, value: str) -> None:
            safe_value = self._normalize_name(value).lower()
            if safe_value:
                query.append(f"AND v.{field}=?")
                params.append(safe_value)

        _maybe_append("scope", scope)
        _maybe_append("ratio_preset", ratio_preset)
        _maybe_append("difficulty_tier", difficulty_tier)
        _maybe_append("genre", genre)
        sql = " ".join(query) + " ORDER BY v.updated_at DESC, v.id DESC LIMIT 1"

        with self._lock:
            with self._connect() as connection:
                row = connection.execute(sql, tuple(params)).fetchone()
        if not row:
            return None
        return self._normalize_version_row(row)

    def get_profile_settings(self, *, user_id: str = "legacy") -> dict[str, Any]:
        safe_user_id = self._normalize_user_id(user_id)
        with self._lock:
            with self._connect() as connection:
                self._ensure_default_profile(connection, user_id=safe_user_id)
                row = connection.execute(
                    """
                    SELECT english_level, english_level_numeric, english_level_cefr, llm_mode,
                           llm_unified_json, llm_listening_json, llm_reading_json, updated_at
                    FROM profile_settings_v2
                    WHERE user_id=?
                    LIMIT 1
                    """,
                    (safe_user_id,),
                ).fetchone()
                connection.commit()
        if not row:
            level_num, cefr = LEVEL_META["cet4"]
            return {
                "english_level": "cet4",
                "english_level_numeric": level_num,
                "english_level_cefr": cefr,
                "llm_mode": "unified",
                "llm_unified": self._normalize_llm_payload({}),
                "llm_listening": self._normalize_llm_payload({}),
                "llm_reading": self._normalize_llm_payload({}),
                "updated_at": _now_ms(),
            }
        return {
            "english_level": str(row["english_level"] or "cet4"),
            "english_level_numeric": float(row["english_level_numeric"] or 7.5),
            "english_level_cefr": str(row["english_level_cefr"] or "B1"),
            "llm_mode": str(row["llm_mode"] or "unified"),
            "llm_unified": self._decrypt_llm_payload(self._json_load(str(row["llm_unified_json"] or "{}"), {})),
            "llm_listening": self._decrypt_llm_payload(self._json_load(str(row["llm_listening_json"] or "{}"), {})),
            "llm_reading": self._decrypt_llm_payload(self._json_load(str(row["llm_reading_json"] or "{}"), {})),
            "updated_at": int(row["updated_at"] or 0),
        }

    def upsert_profile_settings(self, patch: dict[str, Any], *, user_id: str = "legacy") -> dict[str, Any]:
        safe_user_id = self._normalize_user_id(user_id)
        current = self.get_profile_settings(user_id=safe_user_id)
        safe_patch = patch if isinstance(patch, dict) else {}
        next_level = str(safe_patch.get("english_level") or current.get("english_level") or "cet4").strip().lower()
        if next_level not in LEVEL_META:
            next_level = "cet4"
        level_num, cefr = LEVEL_META[next_level]
        next_mode = str(safe_patch.get("llm_mode") or current.get("llm_mode") or "unified").strip().lower()
        if next_mode not in {"unified", "custom"}:
            next_mode = "unified"

        def _merge_llm(current_raw: Any, patch_raw: Any) -> dict[str, Any]:
            current_llm = self._normalize_llm_payload(current_raw)
            patch_llm = patch_raw if isinstance(patch_raw, dict) else {}
            merged = {
                "base_url": str(patch_llm.get("base_url") or current_llm.get("base_url") or "").strip() or DEFAULT_LLM_BASE_URL,
                "api_key": str(current_llm.get("api_key") or "").strip(),
                "model": str(patch_llm.get("model") or current_llm.get("model") or "").strip() or "gpt-5.2",
                "llm_support_json": bool(
                    patch_llm.get("llm_support_json")
                    if "llm_support_json" in patch_llm
                    else current_llm.get("llm_support_json", False)
                ),
            }
            if "api_key" in patch_llm:
                merged["api_key"] = str(patch_llm.get("api_key") or "").strip()
            return merged

        next_unified = _merge_llm(current.get("llm_unified"), safe_patch.get("llm_unified"))
        next_listening = _merge_llm(current.get("llm_listening"), safe_patch.get("llm_listening"))
        next_reading = _merge_llm(current.get("llm_reading"), safe_patch.get("llm_reading"))

        now = _now_ms()
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO profile_settings_v2(
                        user_id, english_level, english_level_numeric, english_level_cefr, llm_mode,
                        llm_unified_json, llm_listening_json, llm_reading_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        english_level=excluded.english_level,
                        english_level_numeric=excluded.english_level_numeric,
                        english_level_cefr=excluded.english_level_cefr,
                        llm_mode=excluded.llm_mode,
                        llm_unified_json=excluded.llm_unified_json,
                        llm_listening_json=excluded.llm_listening_json,
                        llm_reading_json=excluded.llm_reading_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        safe_user_id,
                        next_level,
                        level_num,
                        cefr,
                        next_mode,
                        self._json_dump(self._encrypt_llm_payload(next_unified), "{}"),
                        self._json_dump(self._encrypt_llm_payload(next_listening), "{}"),
                        self._json_dump(self._encrypt_llm_payload(next_reading), "{}"),
                        now,
                    ),
                )
                connection.commit()
        return self.get_profile_settings(user_id=safe_user_id)

    def upsert_profile_api_keys(self, patch: dict[str, Any], *, user_id: str = "legacy") -> dict[str, Any]:
        safe_patch = patch if isinstance(patch, dict) else {}
        current = self.get_profile_settings(user_id=user_id)
        next_payload = {
            "llm_unified": dict(current.get("llm_unified") if isinstance(current.get("llm_unified"), dict) else {}),
            "llm_listening": dict(current.get("llm_listening") if isinstance(current.get("llm_listening"), dict) else {}),
            "llm_reading": dict(current.get("llm_reading") if isinstance(current.get("llm_reading"), dict) else {}),
        }
        if "llm_unified_api_key" in safe_patch:
            next_payload["llm_unified"]["api_key"] = str(safe_patch.get("llm_unified_api_key") or "").strip()
        if "llm_listening_api_key" in safe_patch:
            next_payload["llm_listening"]["api_key"] = str(safe_patch.get("llm_listening_api_key") or "").strip()
        if "llm_reading_api_key" in safe_patch:
            next_payload["llm_reading"]["api_key"] = str(safe_patch.get("llm_reading_api_key") or "").strip()
        return self.upsert_profile_settings(next_payload, user_id=user_id)

    def _normalize_version_row(self, row: sqlite3.Row) -> dict[str, Any]:
        materials = self._json_load(str(row["materials_json"] or "[]"), [])
        quiz = self._json_load(str(row["quiz_json"] or "{}"), {})
        has_intensive = any(
            isinstance(item, dict)
            and str(item.get("kind") or "").strip().lower() == "intensive"
            and str(item.get("text") or "").strip()
            for item in (materials if isinstance(materials, list) else [])
        )
        has_extensive = any(
            isinstance(item, dict)
            and str(item.get("kind") or "").strip().lower() == "extensive"
            and str(item.get("text") or "").strip()
            for item in (materials if isinstance(materials, list) else [])
        )
        return {
            "version_id": str(row["version_id"] or ""),
            "video_name": str(row["video_name"] or ""),
            "srt_name": str(row["srt_name"] or ""),
            "user_level": str(row["user_level"] or "cet4"),
            "scope": str(row["scope"] or "all"),
            "ratio_preset": str(row["ratio_preset"] or "long_term"),
            "difficulty_tier": str(row["difficulty_tier"] or "balanced"),
            "genre": str(row["genre"] or "news"),
            "i_plus_one_hit": bool(int(row["i_plus_one_hit"] or 0)),
            "pipeline_version": str(row["pipeline_version"] or "reading_v2_v2"),
            "config": self._json_load(str(row["config_json"] or "{}"), {}),
            "difficulty_report": self._json_load(str(row["difficulty_report_json"] or "{}"), {}),
            "materials": materials if isinstance(materials, list) else [],
            "quiz": quiz if isinstance(quiz, dict) else {},
            "created_at": int(row["created_at"] or 0),
            "updated_at": int(row["updated_at"] or 0),
            "has_intensive": has_intensive,
            "has_extensive": has_extensive,
        }

    def _normalize_short_answer_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = self._json_load(str(row["result_json"] or "{}"), {})
        if not isinstance(result, dict):
            result = {}
        return {
            "attempt_id": str(row["attempt_id"] or ""),
            "version_id": str(row["version_id"] or ""),
            "question_id": str(row["question_id"] or ""),
            "answer_text": str(row["answer_text"] or ""),
            "submitted_at": int(row["created_at"] or 0),
            **result,
        }

    def save_version(
        self,
        *,
        user_id: str = "legacy",
        source_id: int,
        user_level: str,
        scope: str,
        ratio_preset: str,
        difficulty_tier: str,
        genre: str,
        i_plus_one_hit: bool,
        config: dict[str, Any],
        difficulty_report: dict[str, Any],
        materials: list[dict[str, Any]],
        quiz: dict[str, Any],
        pipeline_version: str = "reading_v2_v2",
    ) -> dict[str, Any] | None:
        safe_user_id = self._normalize_user_id(user_id)
        safe_source_id = max(0, int(source_id or 0))
        if safe_source_id <= 0:
            return None
        version_id = uuid.uuid4().hex
        now = _now_ms()
        with self._lock:
            with self._connect() as connection:
                source = connection.execute(
                    "SELECT id FROM reading_sources WHERE id=? AND user_id=? LIMIT 1",
                    (safe_source_id, safe_user_id),
                ).fetchone()
                if not source:
                    return None
                connection.execute(
                    """
                    INSERT INTO reading_versions(
                        version_id, user_id, source_id, user_level, scope, ratio_preset, difficulty_tier, genre,
                        i_plus_one_hit, config_json, difficulty_report_json, materials_json, quiz_json,
                        pipeline_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        safe_user_id,
                        safe_source_id,
                        self._normalize_name(user_level).lower() or "cet4",
                        self._normalize_name(scope).lower() or "all",
                        self._normalize_name(ratio_preset).lower() or "long_term",
                        self._normalize_name(difficulty_tier).lower() or "balanced",
                        self._normalize_name(genre).lower() or "news",
                        1 if i_plus_one_hit else 0,
                        self._json_dump(config if isinstance(config, dict) else {}, "{}"),
                        self._json_dump(difficulty_report if isinstance(difficulty_report, dict) else {}, "{}"),
                        self._json_dump(materials if isinstance(materials, list) else [], "[]"),
                        self._json_dump(quiz if isinstance(quiz, dict) else {}, "{}"),
                        self._normalize_name(pipeline_version) or "reading_v2_v2",
                        now,
                        now,
                    ),
                )
                connection.commit()
        return self.get_version(user_id=safe_user_id, version_id=version_id)

    def save_material(
        self,
        *,
        user_id: str = "legacy",
        source_id: int,
        user_level: str,
        scope: str,
        ratio_preset: str,
        difficulty_tier: str,
        genre: str,
        i_plus_one_hit: bool,
        config: dict[str, Any],
        difficulty_report: dict[str, Any],
        materials: list[dict[str, Any]],
        quiz: dict[str, Any],
        pipeline_version: str = "reading_v2_v2",
    ) -> dict[str, Any] | None:
        return self.save_version(
            user_id=user_id,
            source_id=source_id,
            user_level=user_level,
            scope=scope,
            ratio_preset=ratio_preset,
            difficulty_tier=difficulty_tier,
            genre=genre,
            i_plus_one_hit=i_plus_one_hit,
            config=config,
            difficulty_report=difficulty_report,
            materials=materials,
            quiz=quiz,
            pipeline_version=pipeline_version,
        )

    def _select_version_sql(self) -> str:
        return """
            SELECT v.version_id, v.user_id, s.video_name, s.srt_name, v.user_level, v.scope, v.ratio_preset, v.difficulty_tier,
                   v.genre, v.i_plus_one_hit, v.config_json, v.difficulty_report_json, v.materials_json, v.quiz_json,
                   v.pipeline_version, v.created_at, v.updated_at
            FROM reading_versions v
            JOIN reading_sources s ON s.id = v.source_id
        """

    def get_version(self, *, user_id: str = "legacy", version_id: str) -> dict[str, Any] | None:
        safe_user_id = self._normalize_user_id(user_id)
        safe_id = self._normalize_name(version_id)
        if not safe_id:
            return None
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    f"{self._select_version_sql()} WHERE v.user_id=? AND s.user_id=? AND v.version_id=? LIMIT 1",
                    (safe_user_id, safe_user_id, safe_id),
                ).fetchone()
        if not row:
            return None
        return self._normalize_version_row(row)

    def list_history(self, *, user_id: str = "legacy", limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], bool]:
        safe_user_id = self._normalize_user_id(user_id)
        safe_limit = max(1, min(100, int(limit or 20)))
        safe_offset = max(0, int(offset or 0))
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    f"{self._select_version_sql()} WHERE v.user_id=? AND s.user_id=? ORDER BY v.updated_at DESC, v.id DESC LIMIT ? OFFSET ?",
                    (safe_user_id, safe_user_id, safe_limit + 1, safe_offset),
                ).fetchall()
        normalized = [self._normalize_version_row(row) for row in rows]
        has_more = len(normalized) > safe_limit
        return normalized[:safe_limit], has_more

    def get_latest_version_by_source(self, *, user_id: str = "legacy", video_name: str, srt_name: str) -> dict[str, Any] | None:
        safe_user_id = self._normalize_user_id(user_id)
        safe_video = self._normalize_name(video_name)
        safe_srt = self._normalize_name(srt_name)
        if not safe_video or not safe_srt:
            return None
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    f"{self._select_version_sql()} WHERE v.user_id=? AND s.user_id=? AND s.video_name=? AND s.srt_name=? ORDER BY v.updated_at DESC, v.id DESC LIMIT 1",
                    (safe_user_id, safe_user_id, safe_video, safe_srt),
                ).fetchone()
        if not row:
            return None
        return self._normalize_version_row(row)

    def list_versions(self, *, user_id: str = "legacy", limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        safe_user_id = self._normalize_user_id(user_id)
        safe_limit = max(1, min(200, int(limit or 20)))
        safe_offset = max(0, int(offset or 0))
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    f"{self._select_version_sql()} WHERE v.user_id=? AND s.user_id=? ORDER BY v.updated_at DESC, v.id DESC LIMIT ? OFFSET ?",
                    (safe_user_id, safe_user_id, safe_limit, safe_offset),
                ).fetchall()
        return [self._normalize_version_row(row) for row in rows]

    def delete_version(self, *, user_id: str = "legacy", version_id: str) -> int:
        safe_user_id = self._normalize_user_id(user_id)
        safe_id = self._normalize_name(version_id)
        if not safe_id:
            return 0
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM reading_short_answer_attempts WHERE user_id=? AND version_id=?",
                    (safe_user_id, safe_id),
                )
                cursor = connection.execute(
                    "DELETE FROM reading_versions WHERE user_id=? AND version_id=?",
                    (safe_user_id, safe_id),
                )
                connection.commit()
                return int(cursor.rowcount or 0)

    def save_short_answer_attempt(
        self,
        *,
        user_id: str = "legacy",
        version_id: str,
        question_id: str,
        answer_text: str,
        result_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        safe_user_id = self._normalize_user_id(user_id)
        safe_version = self._normalize_name(version_id)
        safe_question = self._normalize_name(question_id)
        if not safe_version or not safe_question:
            return None
        attempt_id = uuid.uuid4().hex
        now = _now_ms()
        with self._lock:
            with self._connect() as connection:
                exists = connection.execute(
                    "SELECT version_id FROM reading_versions WHERE user_id=? AND version_id=? LIMIT 1",
                    (safe_user_id, safe_version),
                ).fetchone()
                if not exists:
                    return None
                connection.execute(
                    """
                    INSERT INTO reading_short_answer_attempts(
                        attempt_id, user_id, version_id, question_id, answer_text, result_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        safe_user_id,
                        safe_version,
                        safe_question,
                        str(answer_text or "").strip(),
                        self._json_dump(result_payload if isinstance(result_payload, dict) else {}, "{}"),
                        now,
                    ),
                )
                connection.commit()
        return self.get_short_answer_attempt(user_id=safe_user_id, attempt_id=attempt_id)

    def get_short_answer_attempt(self, *, user_id: str = "legacy", attempt_id: str) -> dict[str, Any] | None:
        safe_user_id = self._normalize_user_id(user_id)
        safe_id = self._normalize_name(attempt_id)
        if not safe_id:
            return None
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT attempt_id, version_id, question_id, answer_text, result_json, created_at
                    FROM reading_short_answer_attempts
                    WHERE user_id=? AND attempt_id=?
                    LIMIT 1
                    """,
                    (safe_user_id, safe_id),
                ).fetchone()
        if not row:
            return None
        return self._normalize_short_answer_row(row)

    def list_short_answer_attempts(
        self,
        *,
        user_id: str = "legacy",
        version_id: str,
        question_id: str = "",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        safe_user_id = self._normalize_user_id(user_id)
        safe_version = self._normalize_name(version_id)
        safe_question = self._normalize_name(question_id)
        if not safe_version:
            return []
        safe_limit = max(1, min(2000, int(limit or 500)))
        with self._lock:
            with self._connect() as connection:
                if safe_question:
                    rows = connection.execute(
                        """
                        SELECT attempt_id, version_id, question_id, answer_text, result_json, created_at
                        FROM reading_short_answer_attempts
                        WHERE user_id=? AND version_id=? AND question_id=?
                        ORDER BY created_at DESC, id DESC
                        LIMIT ?
                        """,
                        (safe_user_id, safe_version, safe_question, safe_limit),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT attempt_id, version_id, question_id, answer_text, result_json, created_at
                        FROM reading_short_answer_attempts
                        WHERE user_id=? AND version_id=?
                        ORDER BY created_at DESC, id DESC
                        LIMIT ?
                        """,
                        (safe_user_id, safe_version, safe_limit),
                    ).fetchall()
        return [self._normalize_short_answer_row(row) for row in rows]

    def delete_short_answer_group(self, *, user_id: str = "legacy", version_id: str, question_id: str) -> int:
        safe_user_id = self._normalize_user_id(user_id)
        safe_version = self._normalize_name(version_id)
        safe_question = self._normalize_name(question_id)
        if not safe_version or not safe_question:
            return 0
        with self._lock:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM reading_short_answer_attempts WHERE user_id=? AND version_id=? AND question_id=?",
                    (safe_user_id, safe_version, safe_question),
                )
                connection.commit()
                return int(cursor.rowcount or 0)
