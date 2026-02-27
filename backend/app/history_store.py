from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class SqliteHistoryStore:
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
                existing = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='learning_history' LIMIT 1"
                ).fetchone()
                if existing:
                    columns = {
                        str(row["name"]).strip()
                        for row in connection.execute("PRAGMA table_info(learning_history)").fetchall()
                    }
                    if "user_id" not in columns:
                        connection.execute(
                            """
                            CREATE TABLE IF NOT EXISTS learning_history_v2 (
                                user_id TEXT NOT NULL,
                                video_name TEXT NOT NULL,
                                srt_name TEXT NOT NULL,
                                current_index INTEGER NOT NULL DEFAULT 0,
                                total_sentences INTEGER NOT NULL DEFAULT 0,
                                thumbnail TEXT NOT NULL DEFAULT '',
                                timestamp INTEGER NOT NULL DEFAULT 0,
                                completed INTEGER NOT NULL DEFAULT 0,
                                history_id TEXT NOT NULL DEFAULT '',
                                display_name TEXT NOT NULL DEFAULT '',
                                folder_id TEXT NOT NULL DEFAULT '',
                                subtitle_task_meta TEXT NOT NULL DEFAULT '',
                                PRIMARY KEY (user_id, video_name, srt_name)
                            )
                            """
                        )
                        connection.execute(
                            """
                            INSERT OR REPLACE INTO learning_history_v2(
                                user_id, video_name, srt_name, current_index, total_sentences,
                                thumbnail, timestamp, completed, history_id, display_name, folder_id, subtitle_task_meta
                            )
                            SELECT
                                'legacy',
                                video_name,
                                srt_name,
                                current_index,
                                total_sentences,
                                thumbnail,
                                timestamp,
                                completed,
                                history_id,
                                display_name,
                                folder_id,
                                subtitle_task_meta
                            FROM learning_history
                            """
                        )
                        connection.execute("DROP TABLE learning_history")
                        connection.execute("ALTER TABLE learning_history_v2 RENAME TO learning_history")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS learning_history (
                        user_id TEXT NOT NULL DEFAULT 'legacy',
                        video_name TEXT NOT NULL,
                        srt_name TEXT NOT NULL,
                        current_index INTEGER NOT NULL DEFAULT 0,
                        total_sentences INTEGER NOT NULL DEFAULT 0,
                        thumbnail TEXT NOT NULL DEFAULT '',
                        timestamp INTEGER NOT NULL DEFAULT 0,
                        completed INTEGER NOT NULL DEFAULT 0,
                        history_id TEXT NOT NULL DEFAULT '',
                        display_name TEXT NOT NULL DEFAULT '',
                        folder_id TEXT NOT NULL DEFAULT '',
                        subtitle_task_meta TEXT NOT NULL DEFAULT '',
                        PRIMARY KEY (user_id, video_name, srt_name)
                    )
                    """
                )
                existing_columns = {
                    str(row["name"]).strip()
                    for row in connection.execute("PRAGMA table_info(learning_history)").fetchall()
                }
                if "display_name" not in existing_columns:
                    connection.execute(
                        "ALTER TABLE learning_history ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"
                    )
                if "folder_id" not in existing_columns:
                    connection.execute(
                        "ALTER TABLE learning_history ADD COLUMN folder_id TEXT NOT NULL DEFAULT ''"
                    )
                if "subtitle_task_meta" not in existing_columns:
                    connection.execute(
                        "ALTER TABLE learning_history ADD COLUMN subtitle_task_meta TEXT NOT NULL DEFAULT ''"
                    )
                if "user_id" not in existing_columns:
                    connection.execute(
                        "ALTER TABLE learning_history ADD COLUMN user_id TEXT NOT NULL DEFAULT 'legacy'"
                    )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_learning_history_user_ts ON learning_history(user_id, timestamp DESC)"
                )
                connection.commit()

    @staticmethod
    def _normalize_user_id(user_id: str) -> str:
        safe = str(user_id or "").strip()
        return safe or "legacy"

    @staticmethod
    def _normalize_subtitle_task_meta(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        pending_state = str(raw.get("pending_state", "none") or "none").strip().lower()
        if pending_state not in {"none", "failed", "cancelled"}:
            pending_state = "none"
        last_job_id = str(raw.get("last_job_id", "") or "").strip()
        if not last_job_id:
            return None
        last_job_status = str(raw.get("last_job_status", "queued") or "queued").strip().lower()
        if last_job_status not in {"queued", "running", "completed", "failed", "cancelled"}:
            last_job_status = "queued"
        source_mode = str(raw.get("source_mode", "file") or "file").strip().lower()
        if source_mode not in {"file", "url", "resume"}:
            source_mode = "file"
        try:
            updated_at = int(raw.get("updated_at", 0) or 0)
        except (TypeError, ValueError):
            updated_at = 0
        return {
            "pending_state": pending_state,
            "last_job_id": last_job_id,
            "last_job_status": last_job_status,
            "last_stage": str(raw.get("last_stage", "") or "").strip(),
            "last_message": str(raw.get("last_message", "") or "").strip(),
            "has_partial_result": bool(raw.get("has_partial_result", False)),
            "source_mode": source_mode,
            "updated_at": max(0, updated_at),
        }

    @classmethod
    def _load_subtitle_task_meta(cls, raw_text: Any) -> dict[str, Any] | None:
        text = str(raw_text or "").strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except Exception:
            return None
        return cls._normalize_subtitle_task_meta(payload)

    @staticmethod
    def _normalize_record(record: dict[str, Any]) -> dict[str, Any] | None:
        video_name = str(record.get("videoName", "")).strip()
        srt_name = str(record.get("srtName", "")).strip()
        if not video_name or not srt_name:
            return None

        try:
            current_index = int(record.get("currentIndex", 0) or 0)
        except (TypeError, ValueError):
            current_index = 0
        try:
            total_sentences = int(record.get("totalSentences", 0) or 0)
        except (TypeError, ValueError):
            total_sentences = 0
        try:
            timestamp = int(record.get("timestamp", 0) or 0)
        except (TypeError, ValueError):
            timestamp = 0

        return {
            "videoName": video_name,
            "srtName": srt_name,
            "currentIndex": max(0, current_index),
            "totalSentences": max(0, total_sentences),
            "thumbnail": str(record.get("thumbnail", "") or ""),
            "timestamp": max(0, timestamp),
            "completed": bool(record.get("completed", False)),
            "historyId": str(record.get("historyId", "") or ""),
            "displayName": str(record.get("displayName", "") or "").strip(),
            "folderId": str(record.get("folderId", "") or "").strip(),
            "subtitleTaskMeta": SqliteHistoryStore._normalize_subtitle_task_meta(record.get("subtitleTaskMeta")),
        }

    def list_records(self, *, user_id: str = "legacy") -> list[dict[str, Any]]:
        safe_user_id = self._normalize_user_id(user_id)
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT
                        user_id,
                        video_name,
                        srt_name,
                        current_index,
                        total_sentences,
                        thumbnail,
                        timestamp,
                        completed,
                        history_id,
                        display_name,
                        folder_id,
                        subtitle_task_meta
                    FROM learning_history
                    WHERE user_id=?
                    ORDER BY timestamp DESC
                    """,
                    (safe_user_id,),
                ).fetchall()

        records: list[dict[str, Any]] = []
        for row in rows:
            records.append(
                {
                    "videoName": row["video_name"],
                    "srtName": row["srt_name"],
                    "currentIndex": int(row["current_index"] or 0),
                    "totalSentences": int(row["total_sentences"] or 0),
                    "thumbnail": row["thumbnail"] or "",
                    "timestamp": int(row["timestamp"] or 0),
                    "completed": bool(row["completed"]),
                    "historyId": row["history_id"] or "",
                    "displayName": row["display_name"] or "",
                    "folderId": row["folder_id"] or "",
                    "subtitleTaskMeta": self._load_subtitle_task_meta(row["subtitle_task_meta"]),
                }
            )
        return records

    def replace_all_records(self, records: list[dict[str, Any]], *, user_id: str = "legacy") -> int:
        safe_user_id = self._normalize_user_id(user_id)
        deduped: dict[tuple[str, str], dict[str, Any]] = {}
        for raw_record in records or []:
            normalized = self._normalize_record(raw_record)
            if not normalized:
                continue
            key = (normalized["videoName"], normalized["srtName"])
            current = deduped.get(key)
            if not current or normalized["timestamp"] >= current["timestamp"]:
                deduped[key] = normalized

        payload = list(deduped.values())

        with self._lock:
            with self._connect() as connection:
                connection.execute("DELETE FROM learning_history WHERE user_id=?", (safe_user_id,))
                connection.executemany(
                    """
                    INSERT INTO learning_history (
                        user_id,
                        video_name,
                        srt_name,
                        current_index,
                        total_sentences,
                        thumbnail,
                        timestamp,
                        completed,
                        history_id,
                        display_name,
                        folder_id,
                        subtitle_task_meta
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            safe_user_id,
                            item["videoName"],
                            item["srtName"],
                            item["currentIndex"],
                            item["totalSentences"],
                            item["thumbnail"],
                            item["timestamp"],
                            1 if item["completed"] else 0,
                            item["historyId"],
                            item["displayName"],
                            item["folderId"],
                            json.dumps(item["subtitleTaskMeta"], ensure_ascii=False)
                            if isinstance(item.get("subtitleTaskMeta"), dict)
                            else "",
                        )
                        for item in payload
                    ],
                )
                connection.commit()
        return len(payload)
