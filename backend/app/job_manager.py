from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.asr_cost_ledger import append_asr_cost_record
from app.llm_cost_ledger import append_llm_cost_record
from app.pipeline_runner import run_llm_resume_pipeline, run_subtitle_pipeline, validate_video_file
from app.translation_cost_ledger import append_translation_cost_record
from app.url_ingest import download_video_from_url
from vendor.videolingo_subtitle_core.engine import PipelineError, safe_rmtree


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _to_ms(value: datetime | None) -> int:
    if value is None:
        return 0
    return max(0, int(round(value.timestamp() * 1000)))


def _from_ms(value: Any) -> datetime | None:
    try:
        parsed = int(value or 0)
    except Exception:
        parsed = 0
    if parsed <= 0:
        return None
    return datetime.fromtimestamp(parsed / 1000, tz=timezone.utc)


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[DEBUG] Failed to load JSON: {path} -> {exc}")
        return None


def _build_partial_result(work_dir: str) -> dict | None:
    log_dir = Path(work_dir) / "log"
    if not log_dir.exists():
        return None

    candidates = [
        ("meaning_split", log_dir / "split_meaning.json", "sentences"),
        ("nlp_split", log_dir / "split_nlp.json", "sentences"),
        ("asr", log_dir / "asr_segments.json", "segments"),
    ]
    source = ""
    rows: list[dict] | None = None
    for stage, path, key in candidates:
        payload = _load_json(path)
        items = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(items, list) and items:
            source = stage
            rows = items
            break
    if not rows:
        return None

    subtitles: list[dict] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(item.get("start") or 0.0)
        except Exception:
            start = 0.0
        try:
            end = float(item.get("end") or 0.0)
        except Exception:
            end = start + 0.8
        if end <= start:
            end = start + 0.5
        subtitles.append(
            {
                "id": len(subtitles) + 1,
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
                "translation": str(item.get("translation") or "").strip(),
                "index": len(subtitles),
            }
        )
    if not subtitles:
        return None

    word_payload = _load_json(log_dir / "word_segments.json")
    word_segments = word_payload.get("word_segments") if isinstance(word_payload, dict) else None
    if not isinstance(word_segments, list):
        word_segments = []

    result = {
        "subtitles": subtitles,
        "word_segments": word_segments,
        "stats": {
            "subtitle_count": len(subtitles),
            "word_count": len(word_segments),
            "word_segments_available": bool(word_segments),
            "partial_source": source,
        },
    }
    print(f"[DEBUG] Built partial subtitle result from {source} ({len(subtitles)} items).")
    return result


def _build_partial_from_sentences(sentences: list[dict], word_segments: list[dict]) -> dict | None:
    subtitles: list[dict] = []
    for item in sentences or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(item.get("start") or 0.0)
        except Exception:
            start = 0.0
        try:
            end = float(item.get("end") or 0.0)
        except Exception:
            end = start + 0.8
        if end <= start:
            end = start + 0.5
        subtitles.append(
            {
                "id": len(subtitles) + 1,
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
                "translation": str(item.get("translation") or "").strip(),
                "index": len(subtitles),
            }
        )
    if not subtitles:
        return None
    cleaned_words = word_segments if isinstance(word_segments, list) else []
    return {
        "subtitles": subtitles,
        "word_segments": cleaned_words,
        "stats": {
            "subtitle_count": len(subtitles),
            "word_count": len(cleaned_words),
            "word_segments_available": bool(cleaned_words),
            "partial_source": "resume",
        },
    }


_RECENT_PROGRESS_EVENT_LIMIT = 30
_RECENT_PROGRESS_EVENT_RETURN_LIMIT = 12
_POLL_INTERVAL_MS_HINT = 800


@dataclass
class JobRecord:
    job_id: str
    user_id: str
    work_dir: str
    video_path: str
    options: dict
    job_kind: str = "full"
    source_mode: str = "file"
    source_url: str = ""
    resume_sentences: list[dict] = field(default_factory=list)
    resume_word_segments: list[dict] = field(default_factory=list)
    status: str = "queued"
    progress_percent: int = 0
    current_stage: str = "queued"
    message: str = ""
    error: str | None = None
    error_code: str = ""
    error_detail: dict | None = None
    created_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    updated_at: datetime = field(default_factory=_now)
    completed_at: datetime | None = None
    result: dict | None = None
    result_consumed: bool = False
    partial_result: dict | None = None
    cancel_requested: bool = False
    whisper_runtime: str = ""
    whisper_model_requested: str = ""
    whisper_model_effective: str = ""
    asr_provider_effective: str = ""
    asr_fallback_used: bool = False
    stage_durations_ms: dict[str, int] = field(default_factory=dict)
    stage_order: list[str] = field(default_factory=list)
    stage_started_at: datetime | None = None
    stage_detail: dict[str, Any] = field(default_factory=dict)
    recent_progress_events: list[dict[str, Any]] = field(default_factory=list)
    status_revision: int = 0
    sync_diagnostics: dict[str, Any] = field(default_factory=dict)


class SubtitleJobManager:
    def __init__(
        self,
        runtime_root: str,
        *,
        db_path: str,
        global_concurrency_limit: int = 3,
        per_user_concurrency_limit: int = 1,
    ):
        self.runtime_root = Path(runtime_root)
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._global_concurrency_limit = max(1, int(global_concurrency_limit or 1))
        self._per_user_concurrency_limit = max(1, int(per_user_concurrency_limit or 1))
        self._jobs: dict[str, JobRecord] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.RLock()
        self._workers: list[threading.Thread] = []
        self._active_by_user: dict[str, int] = {}
        self._active_jobs_total = 0
        self._init_db()
        with self._lock:
            self._load_jobs_from_db_locked()
            self._ensure_worker_alive_locked()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._db_path), timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS subtitle_jobs (
                    job_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_subtitle_jobs_user_updated ON subtitle_jobs(user_id, updated_at DESC)"
            )
            connection.commit()

    def _record_to_persistence_payload(self, record: JobRecord) -> dict[str, Any]:
        return {
            "job_id": record.job_id,
            "user_id": record.user_id,
            "work_dir": record.work_dir,
            "video_path": record.video_path,
            "options": record.options if isinstance(record.options, dict) else {},
            "job_kind": record.job_kind,
            "source_mode": record.source_mode,
            "source_url": record.source_url,
            "resume_sentences": record.resume_sentences if isinstance(record.resume_sentences, list) else [],
            "resume_word_segments": record.resume_word_segments if isinstance(record.resume_word_segments, list) else [],
            "status": record.status,
            "progress_percent": int(record.progress_percent or 0),
            "current_stage": record.current_stage,
            "message": record.message,
            "error": record.error,
            "error_code": record.error_code,
            "error_detail": record.error_detail if isinstance(record.error_detail, dict) else None,
            "created_at_ms": _to_ms(record.created_at),
            "started_at_ms": _to_ms(record.started_at),
            "updated_at_ms": _to_ms(record.updated_at),
            "completed_at_ms": _to_ms(record.completed_at),
            "result": record.result if isinstance(record.result, dict) else None,
            "result_consumed": bool(record.result_consumed),
            "partial_result": record.partial_result if isinstance(record.partial_result, dict) else None,
            "cancel_requested": bool(record.cancel_requested),
            "whisper_runtime": record.whisper_runtime,
            "whisper_model_requested": record.whisper_model_requested,
            "whisper_model_effective": record.whisper_model_effective,
            "asr_provider_effective": record.asr_provider_effective,
            "asr_fallback_used": bool(record.asr_fallback_used),
            "stage_durations_ms": record.stage_durations_ms if isinstance(record.stage_durations_ms, dict) else {},
            "stage_order": record.stage_order if isinstance(record.stage_order, list) else [],
            "stage_started_at_ms": _to_ms(record.stage_started_at),
            "stage_detail": record.stage_detail if isinstance(record.stage_detail, dict) else {},
            "recent_progress_events": record.recent_progress_events if isinstance(record.recent_progress_events, list) else [],
            "status_revision": int(record.status_revision or 0),
            "sync_diagnostics": record.sync_diagnostics if isinstance(record.sync_diagnostics, dict) else {},
        }

    @staticmethod
    def _payload_to_record(payload: dict[str, Any]) -> JobRecord | None:
        if not isinstance(payload, dict):
            return None
        job_id = str(payload.get("job_id") or "").strip()
        user_id = str(payload.get("user_id") or "").strip() or "legacy"
        work_dir = str(payload.get("work_dir") or "").strip()
        if not job_id or not work_dir:
            return None
        created_at = _from_ms(payload.get("created_at_ms")) or _now()
        updated_at = _from_ms(payload.get("updated_at_ms")) or created_at
        status = str(payload.get("status") or "queued").strip().lower() or "queued"
        if status in {"queued", "running"}:
            status = "failed"
            payload["error_code"] = "service_restarted"
            payload["error"] = str(payload.get("error") or "服务重启后任务中断")
            payload["message"] = "服务重启后任务中断，请重新创建任务"
        return JobRecord(
            job_id=job_id,
            user_id=user_id,
            work_dir=work_dir,
            video_path=str(payload.get("video_path") or ""),
            options=payload.get("options") if isinstance(payload.get("options"), dict) else {},
            job_kind=str(payload.get("job_kind") or "full"),
            source_mode=str(payload.get("source_mode") or "file"),
            source_url=str(payload.get("source_url") or ""),
            resume_sentences=payload.get("resume_sentences") if isinstance(payload.get("resume_sentences"), list) else [],
            resume_word_segments=(
                payload.get("resume_word_segments") if isinstance(payload.get("resume_word_segments"), list) else []
            ),
            status=status,
            progress_percent=max(0, min(100, int(payload.get("progress_percent") or 0))),
            current_stage=str(payload.get("current_stage") or "queued"),
            message=str(payload.get("message") or ""),
            error=str(payload.get("error") or "").strip() or None,
            error_code=str(payload.get("error_code") or ""),
            error_detail=payload.get("error_detail") if isinstance(payload.get("error_detail"), dict) else None,
            created_at=created_at,
            started_at=_from_ms(payload.get("started_at_ms")),
            updated_at=updated_at,
            completed_at=_from_ms(payload.get("completed_at_ms")),
            result=payload.get("result") if isinstance(payload.get("result"), dict) else None,
            result_consumed=bool(payload.get("result_consumed")),
            partial_result=payload.get("partial_result") if isinstance(payload.get("partial_result"), dict) else None,
            cancel_requested=bool(payload.get("cancel_requested")),
            whisper_runtime=str(payload.get("whisper_runtime") or ""),
            whisper_model_requested=str(payload.get("whisper_model_requested") or ""),
            whisper_model_effective=str(payload.get("whisper_model_effective") or ""),
            asr_provider_effective=str(payload.get("asr_provider_effective") or ""),
            asr_fallback_used=bool(payload.get("asr_fallback_used")),
            stage_durations_ms=payload.get("stage_durations_ms") if isinstance(payload.get("stage_durations_ms"), dict) else {},
            stage_order=payload.get("stage_order") if isinstance(payload.get("stage_order"), list) else [],
            stage_started_at=_from_ms(payload.get("stage_started_at_ms")),
            stage_detail=payload.get("stage_detail") if isinstance(payload.get("stage_detail"), dict) else {},
            recent_progress_events=(
                payload.get("recent_progress_events") if isinstance(payload.get("recent_progress_events"), list) else []
            ),
            status_revision=max(0, int(payload.get("status_revision") or 0)),
            sync_diagnostics=payload.get("sync_diagnostics") if isinstance(payload.get("sync_diagnostics"), dict) else {},
        )

    def _persist_record_locked(self, record: JobRecord) -> None:
        payload_json = json.dumps(self._record_to_persistence_payload(record), ensure_ascii=False)
        now_ms = _to_ms(_now())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO subtitle_jobs(job_id, user_id, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    user_id=excluded.user_id,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    record.job_id,
                    record.user_id,
                    payload_json,
                    _to_ms(record.created_at),
                    now_ms,
                ),
            )
            connection.commit()

    def _delete_record_locked(self, *, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM subtitle_jobs WHERE job_id=?", (job_id,))
            connection.commit()

    def _load_jobs_from_db_locked(self) -> None:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM subtitle_jobs ORDER BY updated_at DESC").fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except Exception:
                continue
            record = self._payload_to_record(payload)
            if not record:
                continue
            self._jobs[record.job_id] = record
            self._persist_record_locked(record)

    def _start_worker_locked(self) -> None:
        worker_index = len(self._workers) + 1
        thread = threading.Thread(target=self._worker_loop, daemon=True, name=f"subtitle-job-worker-{worker_index}")
        thread.start()
        self._workers.append(thread)

    def _ensure_worker_alive_locked(self) -> None:
        alive = [worker for worker in self._workers if worker.is_alive()]
        self._workers = alive
        while len(self._workers) < self._global_concurrency_limit:
            self._start_worker_locked()

    @staticmethod
    def _normalize_user_id(user_id: str | None) -> str:
        safe = str(user_id or "").strip()
        return safe or "legacy"

    def _can_start_job_locked(self, record: JobRecord) -> bool:
        if self._active_jobs_total >= self._global_concurrency_limit:
            return False
        active_by_user = int(self._active_by_user.get(record.user_id, 0))
        if active_by_user >= self._per_user_concurrency_limit:
            return False
        return True

    @staticmethod
    def _normalize_stage(stage: str | None, fallback: str = "queued") -> str:
        safe = str(stage or "").strip()
        return safe or fallback

    def _ensure_stage_entry_locked(self, record: JobRecord, stage: str) -> str:
        safe_stage = self._normalize_stage(stage)
        if safe_stage not in record.stage_order:
            record.stage_order.append(safe_stage)
        record.stage_durations_ms.setdefault(safe_stage, 0)
        return safe_stage

    def _close_active_stage_locked(self, record: JobRecord, now: datetime | None = None) -> None:
        if record.stage_started_at is None:
            return
        current_stage = self._ensure_stage_entry_locked(record, record.current_stage or "queued")
        safe_now = now or _now()
        elapsed_ms = max(0, int(round((safe_now - record.stage_started_at).total_seconds() * 1000)))
        if elapsed_ms > 0:
            record.stage_durations_ms[current_stage] = int(record.stage_durations_ms.get(current_stage, 0)) + elapsed_ms
        record.stage_started_at = safe_now

    def _transition_stage_locked(self, record: JobRecord, next_stage: str, now: datetime | None = None) -> None:
        safe_now = now or _now()
        safe_next = self._normalize_stage(next_stage)
        safe_current = self._normalize_stage(record.current_stage, fallback=safe_next)
        if safe_current != safe_next:
            self._close_active_stage_locked(record, safe_now)
            record.current_stage = safe_next
            self._ensure_stage_entry_locked(record, safe_next)
            record.stage_started_at = safe_now
            print(f"[DEBUG] Job {record.job_id} stage transition: {safe_current} -> {safe_next}")
            return
        record.current_stage = safe_current
        self._ensure_stage_entry_locked(record, safe_current)
        if record.stage_started_at is None:
            record.stage_started_at = safe_now

    def _finalize_stage_tracking_locked(self, record: JobRecord, now: datetime | None = None) -> None:
        safe_now = now or _now()
        self._close_active_stage_locked(record, safe_now)
        record.stage_started_at = None

    def _init_stage_tracking_locked(self, record: JobRecord) -> None:
        queued_stage = self._normalize_stage(record.current_stage, fallback="queued")
        record.current_stage = queued_stage
        record.stage_order = [queued_stage]
        record.stage_durations_ms = {queued_stage: 0}
        record.stage_started_at = record.created_at
        record.stage_detail = {
            "stage": queued_stage,
            "step_key": "queued",
            "step_label": "任务排队中",
            "done": 0,
            "total": 0,
            "unit": "",
            "percent_in_stage": 0,
            "eta_seconds": None,
            "updated_at": _iso(record.created_at),
        }
        record.recent_progress_events = [
            {
                "event_id": uuid.uuid4().hex[:12],
                "stage": queued_stage,
                "level": "info",
                "message": "任务已排队",
                "percent": int(record.progress_percent or 0),
                "at": _iso(record.created_at) or "",
            }
        ]
        record.status_revision = 1

    @staticmethod
    def _sanitize_detail_payload(detail: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(detail, dict):
            return None
        payload: dict[str, Any] = {}
        for key in ("step_key", "step_label", "unit"):
            value = str(detail.get(key) or "").strip()
            if value:
                payload[key] = value
        for key in ("done", "total", "percent_in_stage", "eta_seconds"):
            if key not in detail:
                continue
            value = detail.get(key)
            if value is None:
                payload[key] = None
                continue
            try:
                parsed = int(value)
            except Exception:
                continue
            if key != "eta_seconds":
                parsed = max(0, parsed)
            payload[key] = parsed
        if "percent_in_stage" in payload and payload["percent_in_stage"] is not None:
            payload["percent_in_stage"] = max(0, min(100, int(payload["percent_in_stage"])))
        return payload or None

    @staticmethod
    def _sanitize_event_level(level: str | None) -> str:
        safe = str(level or "").strip().lower()
        if safe not in {"info", "success", "warning", "error"}:
            return "info"
        return safe

    @staticmethod
    def _safe_sync_diagnostics(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        result = {
            "alignment_quality_score": 0.0,
            "global_offset_ms": 0,
            "drift_scale": 1.0,
            "correction_applied": False,
            "correction_method": "none",
        }
        try:
            result["alignment_quality_score"] = float(payload.get("alignment_quality_score") or 0.0)
        except Exception:
            pass
        try:
            result["global_offset_ms"] = int(payload.get("global_offset_ms") or 0)
        except Exception:
            pass
        try:
            result["drift_scale"] = float(payload.get("drift_scale") or 1.0)
        except Exception:
            pass
        result["correction_applied"] = bool(payload.get("correction_applied") or False)
        method = str(payload.get("correction_method") or "").strip() or "none"
        result["correction_method"] = method
        if "triggered" in payload:
            result["triggered"] = bool(payload.get("triggered"))
        if "correction_score" in payload:
            try:
                result["correction_score"] = float(payload.get("correction_score") or 0.0)
            except Exception:
                result["correction_score"] = 0.0
        return result

    @staticmethod
    def _extract_sync_diagnostics_from_result(result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {}
        direct = result.get("diagnostics")
        if isinstance(direct, dict):
            return SubtitleJobManager._safe_sync_diagnostics(direct)
        stats = result.get("stats")
        if isinstance(stats, dict) and isinstance(stats.get("sync_diagnostics"), dict):
            return SubtitleJobManager._safe_sync_diagnostics(stats.get("sync_diagnostics"))
        return {}

    @staticmethod
    def _is_translation_model_requested(options: Any) -> bool:
        if not isinstance(options, dict):
            return False
        llm = options.get("llm")
        if not isinstance(llm, dict):
            return False
        model = str(llm.get("model") or "").strip().lower()
        return model == "qwen-mt-flash"

    @classmethod
    def _map_stage_for_display(cls, stage: Any, *, translation_model_requested: bool) -> str:
        safe_stage = cls._normalize_stage(str(stage or ""))
        if translation_model_requested and safe_stage == "llm_translate":
            return "translate_chunks"
        return safe_stage

    @staticmethod
    def _map_message_for_display(message: Any, *, translation_model_requested: bool) -> str:
        safe_message = str(message or "")
        if not translation_model_requested:
            return safe_message
        if "直译" not in safe_message:
            return safe_message
        normalized = (
            safe_message
            .replace("正在执行 LLM 直译", "正在执行翻译模型直译")
            .replace("正在执行 LLM直译", "正在执行翻译模型直译")
            .replace("LLM 直译", "翻译模型直译")
            .replace("LLM直译", "翻译模型直译")
            .replace("LLM ", "翻译模型 ")
            .replace("LLM", "翻译模型")
        )
        return " ".join(normalized.split())

    @staticmethod
    def _bump_status_revision_locked(record: JobRecord) -> None:
        record.status_revision = max(0, int(record.status_revision or 0)) + 1

    def _set_stage_detail_locked(
        self,
        record: JobRecord,
        stage: str,
        now: datetime,
        detail: dict[str, Any] | None = None,
    ) -> None:
        safe_stage = self._normalize_stage(stage)
        payload = self._sanitize_detail_payload(detail) or {}
        next_detail = {
            "stage": safe_stage,
            "step_key": str(payload.get("step_key") or "").strip(),
            "step_label": str(payload.get("step_label") or "").strip(),
            "done": max(0, int(payload.get("done") or 0)),
            "total": max(0, int(payload.get("total") or 0)),
            "unit": str(payload.get("unit") or "").strip(),
            "percent_in_stage": max(0, min(100, int(payload.get("percent_in_stage") or 0))),
            "eta_seconds": (
                None
                if payload.get("eta_seconds") is None
                else max(0, int(payload.get("eta_seconds") or 0))
            ),
            "updated_at": _iso(now),
        }
        record.stage_detail = next_detail

    def _append_progress_event_locked(
        self,
        record: JobRecord,
        *,
        stage: str,
        percent: int,
        message: str,
        level: str = "info",
        now: datetime | None = None,
    ) -> None:
        safe_message = str(message or "").strip()
        if not safe_message:
            return
        safe_now = now or _now()
        safe_stage = self._normalize_stage(stage)
        safe_percent = max(0, min(100, int(percent or 0)))
        safe_level = self._sanitize_event_level(level)
        if record.recent_progress_events:
            last = record.recent_progress_events[-1]
            if (
                str(last.get("stage") or "") == safe_stage
                and str(last.get("message") or "") == safe_message
                and int(last.get("percent") or 0) == safe_percent
                and str(last.get("level") or "") == safe_level
            ):
                last["at"] = _iso(safe_now) or ""
                return
        record.recent_progress_events.append(
            {
                "event_id": uuid.uuid4().hex[:12],
                "stage": safe_stage,
                "level": safe_level,
                "message": safe_message,
                "percent": safe_percent,
                "at": _iso(safe_now) or "",
            }
        )
        if len(record.recent_progress_events) > _RECENT_PROGRESS_EVENT_LIMIT:
            record.recent_progress_events = record.recent_progress_events[-_RECENT_PROGRESS_EVENT_LIMIT:]

    def create_job(
        self,
        user_id: str,
        video_path: str,
        options: dict,
        *,
        job_id: str | None = None,
        work_dir: str | None = None,
        enqueue: bool = True,
    ) -> JobRecord:
        job_id = job_id or uuid.uuid4().hex
        work_dir = work_dir or str(self.runtime_root / job_id)
        safe_user_id = self._normalize_user_id(user_id)
        record = JobRecord(
            job_id=job_id,
            user_id=safe_user_id,
            work_dir=work_dir,
            video_path=video_path,
            options=options,
            job_kind="full",
            source_mode="file",
            message="任务已排队",
            whisper_runtime=str(((options or {}).get("whisper") or {}).get("runtime") or "cloud").strip().lower() or "cloud",
            whisper_model_requested=(
                str(((options or {}).get("whisper") or {}).get("model") or "paraformer-v2").strip()
                or "paraformer-v2"
            ),
            whisper_model_effective=(
                str(((options or {}).get("whisper") or {}).get("model") or "paraformer-v2").strip()
                or "paraformer-v2"
            ),
        )
        with self._lock:
            self._ensure_worker_alive_locked()
            self._init_stage_tracking_locked(record)
            self._jobs[job_id] = record
            self._persist_record_locked(record)
            if enqueue:
                self._queue.put(job_id)
        return record

    def create_url_job(
        self,
        *,
        user_id: str,
        source_url: str,
        options: dict,
        job_id: str | None = None,
        work_dir: str | None = None,
        enqueue: bool = True,
    ) -> JobRecord:
        job_id = job_id or uuid.uuid4().hex
        work_dir = work_dir or str(self.runtime_root / job_id)
        safe_user_id = self._normalize_user_id(user_id)
        record = JobRecord(
            job_id=job_id,
            user_id=safe_user_id,
            work_dir=work_dir,
            video_path="",
            source_url=source_url,
            source_mode="url",
            options=options,
            job_kind="url",
            message="任务已排队",
            whisper_runtime=str(((options or {}).get("whisper") or {}).get("runtime") or "cloud").strip().lower() or "cloud",
            whisper_model_requested=(
                str(((options or {}).get("whisper") or {}).get("model") or "paraformer-v2").strip()
                or "paraformer-v2"
            ),
            whisper_model_effective=(
                str(((options or {}).get("whisper") or {}).get("model") or "paraformer-v2").strip()
                or "paraformer-v2"
            ),
        )
        with self._lock:
            self._ensure_worker_alive_locked()
            self._init_stage_tracking_locked(record)
            self._jobs[job_id] = record
            self._persist_record_locked(record)
            if enqueue:
                self._queue.put(job_id)
        return record

    def create_llm_resume_job(
        self,
        *,
        user_id: str,
        sentences: list[dict],
        word_segments: list[dict],
        options: dict,
        job_id: str | None = None,
        work_dir: str | None = None,
        enqueue: bool = True,
    ) -> JobRecord:
        job_id = job_id or uuid.uuid4().hex
        work_dir = work_dir or str(self.runtime_root / job_id)
        safe_user_id = self._normalize_user_id(user_id)
        record = JobRecord(
            job_id=job_id,
            user_id=safe_user_id,
            work_dir=work_dir,
            video_path="",
            options=options,
            job_kind="llm_resume",
            source_mode="resume",
            resume_sentences=sentences or [],
            resume_word_segments=word_segments or [],
            message="任务已排队",
            whisper_runtime=str(((options or {}).get("whisper") or {}).get("runtime") or "cloud").strip().lower() or "cloud",
            whisper_model_requested=(
                str(((options or {}).get("whisper") or {}).get("model") or "paraformer-v2").strip()
                or "paraformer-v2"
            ),
            whisper_model_effective=(
                str(((options or {}).get("whisper") or {}).get("model") or "paraformer-v2").strip()
                or "paraformer-v2"
            ),
        )
        with self._lock:
            self._ensure_worker_alive_locked()
            self._init_stage_tracking_locked(record)
            self._jobs[job_id] = record
            self._persist_record_locked(record)
            if enqueue:
                self._queue.put(job_id)
        return record

    def enqueue(self, job_id: str) -> None:
        with self._lock:
            self._ensure_worker_alive_locked()
            record = self._jobs.get(job_id)
            if not record:
                raise KeyError(job_id)
            if record.status != "queued":
                return
            self._queue.put(job_id)

    def get_status(self, job_id: str, *, user_id: str | None = None) -> JobRecord | None:
        safe_user_id = self._normalize_user_id(user_id)
        with self._lock:
            self._ensure_worker_alive_locked()
            self._cleanup_expired_locked()
            record = self._jobs.get(job_id)
            if not record:
                return None
            if user_id is not None and record.user_id != safe_user_id:
                return None
            return record

    def consume_result(self, job_id: str, *, user_id: str | None = None) -> dict | None:
        safe_user_id = self._normalize_user_id(user_id)
        with self._lock:
            record = self._jobs.get(job_id)
            if not record or record.status != "completed" or not record.result:
                return None
            if user_id is not None and record.user_id != safe_user_id:
                return None
            payload = record.result
            record.result_consumed = True
            record.updated_at = _now()
            self._persist_record_locked(record)
            # URL 素材模式需要在前端拉取一次下载后的视频，因此延迟清理到过期回收阶段。
            if record.source_mode != "url":
                safe_rmtree(record.work_dir)
            return payload

    def find_active_job(self, *, user_id: str | None = None) -> JobRecord | None:
        safe_user_id = self._normalize_user_id(user_id)
        with self._lock:
            self._ensure_worker_alive_locked()
            self._cleanup_expired_locked()
            active_records = [
                item for item in self._jobs.values()
                if item.status in ("queued", "running")
                and (user_id is None or item.user_id == safe_user_id)
            ]
            if not active_records:
                return None
            return sorted(active_records, key=lambda item: item.created_at, reverse=True)[0]

    def check_submit_capacity(self, *, user_id: str) -> dict[str, Any]:
        safe_user_id = self._normalize_user_id(user_id)
        with self._lock:
            self._ensure_worker_alive_locked()
            self._cleanup_expired_locked()
            active = [item for item in self._jobs.values() if item.status in ("queued", "running")]
            user_active = [item for item in active if item.user_id == safe_user_id]
            if len(user_active) >= self._per_user_concurrency_limit:
                latest = sorted(user_active, key=lambda item: item.created_at, reverse=True)[0]
                return {
                    "ok": False,
                    "code": "user_concurrency_limit",
                    "message": f"当前用户最多允许 {self._per_user_concurrency_limit} 个进行中任务",
                    "active_job_id": latest.job_id,
                    "active_job_status": latest.status,
                }
            if len(active) >= self._global_concurrency_limit:
                return {
                    "ok": False,
                    "code": "global_concurrency_limit",
                    "message": f"全局任务容量已达上限（{self._global_concurrency_limit}）",
                    "active_count": len(active),
                }
            return {
                "ok": True,
                "active_count": len(active),
                "user_active_count": len(user_active),
            }

    def delete_job(self, job_id: str, *, user_id: str | None = None) -> dict[str, Any] | None:
        safe_user_id = self._normalize_user_id(user_id)
        with self._lock:
            self._ensure_worker_alive_locked()
            record = self._jobs.get(job_id)
            if not record:
                return None
            if user_id is not None and record.user_id != safe_user_id:
                return None
            if record.status == "running":
                now = _now()
                record.cancel_requested = True
                self._transition_stage_locked(record, "cancelling", now=now)
                record.message = "正在取消，等待当前步骤结束"
                record.updated_at = now
                self._set_stage_detail_locked(
                    record,
                    stage="cancelling",
                    now=now,
                    detail={"step_key": "cancelling", "step_label": "正在取消", "percent_in_stage": 100},
                )
                self._append_progress_event_locked(
                    record,
                    stage="cancelling",
                    percent=record.progress_percent,
                    message="正在取消，等待当前步骤结束",
                    level="warning",
                    now=now,
                )
                self._bump_status_revision_locked(record)
                self._persist_record_locked(record)
                return {"job_id": job_id, "status": "cancel_requested", "cancel_requested": True}
            if record.status == "queued":
                now = _now()
                record.status = "cancelled"
                record.cancel_requested = False
                record.progress_percent = max(0, min(100, int(record.progress_percent or 0)))
                self._transition_stage_locked(record, "cancelled", now=now)
                record.message = "任务已取消"
                record.completed_at = now
                record.updated_at = now
                self._set_stage_detail_locked(
                    record,
                    stage="cancelled",
                    now=now,
                    detail={"step_key": "cancelled", "step_label": "任务已取消", "percent_in_stage": 100},
                )
                self._append_progress_event_locked(
                    record,
                    stage="cancelled",
                    percent=record.progress_percent,
                    message="任务已取消",
                    level="warning",
                    now=now,
                )
                self._bump_status_revision_locked(record)
                self._finalize_stage_tracking_locked(record, now=now)
                self._persist_record_locked(record)
                return {"job_id": job_id, "status": "cancelled", "cancel_requested": False}
            safe_rmtree(record.work_dir)
            self._jobs.pop(job_id, None)
            self._delete_record_locked(job_id=job_id)
            return {"job_id": job_id, "status": "cancelled", "cancel_requested": False}

    def serialize_status(self, record: JobRecord) -> dict[str, Any]:
        message = record.message or ""
        queue_ahead = 0
        worker_alive = False
        stage_durations_ms: dict[str, int] = {}
        stage_order: list[str] = []
        total_duration_ms = 0
        stage_detail: dict[str, Any] | None = None
        recent_progress_events: list[dict[str, Any]] = []
        status_revision = 0
        sync_diagnostics: dict[str, Any] = {}
        translation_model_requested = self._is_translation_model_requested(record.options)
        current_stage = self._map_stage_for_display(record.current_stage, translation_model_requested=translation_model_requested)
        with self._lock:
            self._ensure_worker_alive_locked()
            worker_alive = any(worker.is_alive() for worker in self._workers)
            if record.status == "queued":
                ordered = sorted(self._jobs.values(), key=lambda item: item.created_at)
                for item in ordered:
                    if item.job_id == record.job_id:
                        break
                    if item.status in ("queued", "running"):
                        queue_ahead += 1
                if queue_ahead > 0:
                    message = f"任务排队中，前方还有 {queue_ahead} 个任务"
                else:
                    message = "任务即将开始"
            seen: set[str] = set()
            for raw_stage in record.stage_order:
                safe_stage = self._map_stage_for_display(raw_stage, translation_model_requested=translation_model_requested)
                if safe_stage in seen:
                    continue
                seen.add(safe_stage)
                stage_order.append(safe_stage)
            if current_stage not in seen:
                seen.add(current_stage)
                stage_order.append(current_stage)
            stage_durations_ms = {}
            for stage, duration_ms in (record.stage_durations_ms or {}).items():
                safe_stage = self._map_stage_for_display(stage, translation_model_requested=translation_model_requested)
                stage_durations_ms[safe_stage] = stage_durations_ms.get(safe_stage, 0) + max(0, int(duration_ms or 0))
            for stage in stage_order:
                stage_durations_ms.setdefault(stage, 0)
            if record.stage_started_at is not None and record.status in {"queued", "running"}:
                live_elapsed_ms = max(0, int(round((_now() - record.stage_started_at).total_seconds() * 1000)))
                if live_elapsed_ms > 0:
                    stage_durations_ms[current_stage] = int(stage_durations_ms.get(current_stage, 0)) + live_elapsed_ms
            safe_completed_at = record.completed_at or _now()
            total_duration_ms = max(0, int(round((safe_completed_at - record.created_at).total_seconds() * 1000)))
            if isinstance(record.stage_detail, dict) and record.stage_detail:
                stage_detail = dict(record.stage_detail)
                stage_detail["stage"] = self._map_stage_for_display(
                    stage_detail.get("stage"),
                    translation_model_requested=translation_model_requested,
                )
                if translation_model_requested:
                    step_key = str(stage_detail.get("step_key") or "").strip()
                    if step_key == "llm_translate":
                        stage_detail["step_key"] = "translate_chunks"
                    stage_detail["step_label"] = self._map_message_for_display(
                        stage_detail.get("step_label"),
                        translation_model_requested=translation_model_requested,
                    )
            events = record.recent_progress_events or []
            if isinstance(events, list):
                recent_progress_events = []
                for item in events[-_RECENT_PROGRESS_EVENT_RETURN_LIMIT:]:
                    normalized = dict(item)
                    normalized["stage"] = self._map_stage_for_display(
                        normalized.get("stage"),
                        translation_model_requested=translation_model_requested,
                    )
                    normalized["message"] = self._map_message_for_display(
                        normalized.get("message"),
                        translation_model_requested=translation_model_requested,
                    )
                    recent_progress_events.append(normalized)
            status_revision = max(0, int(record.status_revision or 0))
            if isinstance(record.sync_diagnostics, dict) and record.sync_diagnostics:
                sync_diagnostics = self._safe_sync_diagnostics(record.sync_diagnostics)
            elif record.status == "completed" and isinstance(record.result, dict):
                extracted = self._extract_sync_diagnostics_from_result(record.result)
                if extracted:
                    record.sync_diagnostics = extracted
                    sync_diagnostics = extracted
        if record.status == "failed" and record.partial_result is None:
            if record.job_kind == "llm_resume":
                partial = _build_partial_from_sentences(record.resume_sentences, record.resume_word_segments)
            else:
                partial = _build_partial_result(record.work_dir)
            if partial:
                partial["partial"] = True
                partial["partial_stage"] = record.current_stage or "llm"
                partial["partial_error"] = record.error or record.message or ""
                record.partial_result = partial
                print(f"[DEBUG] Lazily attached partial_result to failed job {record.job_id}.")

        payload = {
            "job_id": record.job_id,
            "status": record.status,
            "progress_percent": record.progress_percent,
            "current_stage": current_stage,
            "message": self._map_message_for_display(message, translation_model_requested=translation_model_requested),
            "error": record.error,
            "error_code": str(record.error_code or "").strip(),
            "error_detail": dict(record.error_detail) if isinstance(record.error_detail, dict) else None,
            "started_at": _iso(record.started_at),
            "updated_at": _iso(record.updated_at),
            "cancel_requested": bool(record.cancel_requested),
            "whisper_runtime": record.whisper_runtime or "",
            "whisper_model_requested": record.whisper_model_requested or "",
            "whisper_model_effective": record.whisper_model_effective or "",
            "asr_provider_effective": record.asr_provider_effective or "",
            "asr_fallback_used": bool(record.asr_fallback_used),
            "test_simplified_flow": bool((record.options or {}).get("test_simplified_flow", False)),
            "queue_ahead": queue_ahead,
            "worker_alive": worker_alive,
            "stage_durations_ms": stage_durations_ms,
            "stage_order": stage_order,
            "total_duration_ms": total_duration_ms,
            "stage_detail": stage_detail,
            "recent_progress_events": recent_progress_events,
            "status_revision": status_revision,
            "poll_interval_ms_hint": _POLL_INTERVAL_MS_HINT,
        }
        if sync_diagnostics:
            payload["sync_diagnostics"] = sync_diagnostics
        if record.status == "failed" and record.partial_result:
            payload["partial_result"] = record.partial_result
        return payload

    def _update_progress(
        self,
        job_id: str,
        percent: int,
        stage: str,
        message: str,
        detail: dict[str, Any] | None = None,
        *,
        level: str = "info",
    ) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record or record.status == "cancelled" or record.cancel_requested:
                return
            now = _now()
            self._transition_stage_locked(record, stage, now=now)
            record.progress_percent = max(0, min(100, int(percent)))
            record.message = message
            record.updated_at = now
            self._set_stage_detail_locked(record, stage=stage, now=now, detail=detail)
            self._append_progress_event_locked(
                record,
                stage=stage,
                percent=record.progress_percent,
                message=message,
                level=level,
                now=now,
            )
            self._bump_status_revision_locked(record)
            self._persist_record_locked(record)

    def _should_cancel_job(self, job_id: str) -> bool:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record:
                return True
            return bool(record.cancel_requested or record.status == "cancelled")

    def _worker_loop(self) -> None:
        while True:
            job_id = self._queue.get()
            should_wait = False
            job_started = False
            active_user_id = ""
            with self._lock:
                record = self._jobs.get(job_id)
                if not record:
                    self._queue.task_done()
                    continue
                if record.status == "cancelled" or record.cancel_requested:
                    self._queue.task_done()
                    continue
                if not self._can_start_job_locked(record):
                    self._queue.put(job_id)
                    self._queue.task_done()
                    should_wait = True
                else:
                    self._active_jobs_total += 1
                    self._active_by_user[record.user_id] = int(self._active_by_user.get(record.user_id, 0)) + 1
                    active_user_id = record.user_id
                    job_started = True
                    now = _now()
                    record.status = "running"
                    self._transition_stage_locked(record, "running", now=now)
                    record.message = "任务开始执行"
                    record.error = None
                    record.error_code = ""
                    record.error_detail = None
                    record.sync_diagnostics = {}
                    record.partial_result = None
                    record.result = None
                    record.result_consumed = False
                    record.started_at = now
                    record.updated_at = now
                    self._set_stage_detail_locked(
                        record,
                        stage="running",
                        now=now,
                        detail={"step_key": "running", "step_label": "任务开始执行"},
                    )
                    self._append_progress_event_locked(
                        record,
                        stage="running",
                        percent=record.progress_percent,
                        message="任务开始执行",
                        level="info",
                        now=now,
                    )
                    self._bump_status_revision_locked(record)
                    self._persist_record_locked(record)
                    Path(record.work_dir).mkdir(parents=True, exist_ok=True)
            if should_wait:
                time.sleep(0.2)
                continue

            try:
                if record.job_kind == "llm_resume":
                    result = run_llm_resume_pipeline(
                        sentences=record.resume_sentences,
                        options=record.options,
                        word_segments=record.resume_word_segments,
                        progress=lambda p, s, m, d=None: self._update_progress(job_id, p, s, m, d),
                        should_cancel=lambda: self._should_cancel_job(job_id),
                    )
                else:
                    if record.job_kind == "url":
                        def report_download_progress(download_percent: int, message: str) -> None:
                            normalized = max(0, min(100, int(download_percent)))
                            mapped_percent = 3 + int(round((normalized / 100) * 9))
                            self._update_progress(
                                job_id,
                                mapped_percent,
                                "download_source",
                                message or "正在解析并下载素材链接",
                            )

                        def report_url_pipeline_progress(
                            percent: int,
                            stage: str,
                            message: str,
                            detail: dict[str, Any] | None = None,
                        ) -> None:
                            normalized = max(0, min(100, int(percent)))
                            if normalized <= 8:
                                mapped_percent = 12
                            else:
                                mapped_percent = 12 + int(round(((normalized - 8) / 92) * 88))
                            self._update_progress(job_id, mapped_percent, stage, message, detail)

                        self._update_progress(job_id, 3, "download_source", "正在解析并下载素材链接")
                        downloaded_video = download_video_from_url(
                            record.source_url,
                            Path(record.work_dir) / "input",
                            should_cancel=lambda: self._should_cancel_job(job_id),
                            on_progress=report_download_progress,
                        )
                        record.video_path = downloaded_video
                        self._update_progress(job_id, 12, "download_source", "素材下载完成，准备提取音频")
                    validate_video_file(record.video_path)
                    progress_callback = (
                        report_url_pipeline_progress
                        if record.job_kind == "url"
                        else (lambda p, s, m, d=None: self._update_progress(job_id, p, s, m, d))
                    )
                    result = run_subtitle_pipeline(
                        video_path=record.video_path,
                        work_dir=record.work_dir,
                        options=record.options,
                        progress=progress_callback,
                        should_cancel=lambda: self._should_cancel_job(job_id),
                    )
                with self._lock:
                    current = self._jobs.get(job_id)
                    if not current:
                        continue
                    if current.status == "cancelled" or current.cancel_requested:
                        now = _now()
                        current.status = "cancelled"
                        self._transition_stage_locked(current, "cancelled", now=now)
                        current.message = "任务已取消"
                        current.error = None
                        current.error_code = "cancel_requested"
                        current.error_detail = None
                        current.sync_diagnostics = {}
                        current.completed_at = now
                        current.updated_at = now
                        self._set_stage_detail_locked(
                            current,
                            stage="cancelled",
                            now=now,
                            detail={"step_key": "cancelled", "step_label": "任务已取消", "percent_in_stage": 100},
                        )
                        self._append_progress_event_locked(
                            current,
                            stage="cancelled",
                            percent=current.progress_percent,
                            message="任务已取消",
                            level="warning",
                            now=now,
                        )
                        self._bump_status_revision_locked(current)
                        self._finalize_stage_tracking_locked(current, now=now)
                        self._persist_record_locked(current)
                        continue
                    stats = result.get("stats") if isinstance(result, dict) else {}
                    if isinstance(stats, dict):
                        current.whisper_runtime = str(stats.get("whisper_runtime") or current.whisper_runtime or "")
                        current.whisper_model_requested = str(
                            stats.get("whisper_model_requested") or current.whisper_model_requested or ""
                        )
                        current.whisper_model_effective = str(
                            stats.get("whisper_model_effective") or current.whisper_model_effective or ""
                        )
                        current.asr_provider_effective = str(
                            stats.get("asr_provider_effective") or current.asr_provider_effective or ""
                        )
                        current.asr_fallback_used = bool(stats.get("asr_fallback_used") or False)
                    now = _now()
                    current.status = "completed"
                    current.progress_percent = 100
                    self._transition_stage_locked(current, "completed", now=now)
                    current.message = "任务完成"
                    current.error = None
                    current.error_code = ""
                    current.error_detail = None
                    current.result = result
                    current.partial_result = None
                    current.sync_diagnostics = self._extract_sync_diagnostics_from_result(result)
                    current.completed_at = now
                    current.updated_at = now
                    self._set_stage_detail_locked(
                        current,
                        stage="completed",
                        now=now,
                        detail={"step_key": "completed", "step_label": "任务完成", "percent_in_stage": 100},
                    )
                    self._append_progress_event_locked(
                        current,
                        stage="completed",
                        percent=100,
                        message="任务完成",
                        level="success",
                        now=now,
                    )
                    self._bump_status_revision_locked(current)
                    self._finalize_stage_tracking_locked(current, now=now)
                    self._persist_record_locked(current)
                    if isinstance(stats, dict):
                        try:
                            append_asr_cost_record(
                                job_id=current.job_id,
                                stats=stats,
                                whisper_runtime=current.whisper_runtime,
                                whisper_model_effective=current.whisper_model_effective,
                                asr_provider_effective=current.asr_provider_effective,
                            )
                        except Exception as exc:
                            print(f"[DEBUG] Failed to append ASR cost ledger job_id={current.job_id}: {exc}")
                        try:
                            append_llm_cost_record(
                                scene="subtitle_pipeline",
                                owner_id=current.job_id,
                                stats=stats,
                                llm_base_url=str(stats.get("llm_base_url") or ""),
                                llm_provider_effective=str(stats.get("llm_provider_effective") or ""),
                                llm_model_effective=str(stats.get("llm_model_effective") or ""),
                                provider_request_id=str(stats.get("provider_request_id") or ""),
                            )
                        except Exception as exc:
                            print(f"[DEBUG] Failed to append llm cost ledger job_id={current.job_id}: {exc}")
                        try:
                            append_translation_cost_record(
                                job_id=current.job_id,
                                stats=stats,
                                translation_provider_effective=str(stats.get("translation_provider_effective") or ""),
                                translation_model_effective=str(stats.get("translation_model_effective") or ""),
                            )
                        except Exception as exc:
                            print(f"[DEBUG] Failed to append translation cost ledger job_id={current.job_id}: {exc}")
                    # URL 素材模式需保留下载视频用于前端回传，其他模式仍立即清理。
                    if current.source_mode != "url":
                        safe_rmtree(current.work_dir)
            except PipelineError as exc:
                with self._lock:
                    current = self._jobs.get(job_id)
                    if not current:
                        continue
                    if exc.code == "cancel_requested" or current.cancel_requested:
                        now = _now()
                        current.status = "cancelled"
                        self._transition_stage_locked(current, "cancelled", now=now)
                        current.message = "任务已取消"
                        current.error = None
                        current.error_code = "cancel_requested"
                        current.error_detail = {
                            "stage": "cancelled",
                            "code": "cancel_requested",
                            "message": exc.message,
                            "detail": exc.detail,
                        }
                        current.sync_diagnostics = {}
                        current.completed_at = now
                        current.updated_at = now
                        self._set_stage_detail_locked(
                            current,
                            stage="cancelled",
                            now=now,
                            detail={"step_key": "cancelled", "step_label": "任务已取消", "percent_in_stage": 100},
                        )
                        self._append_progress_event_locked(
                            current,
                            stage="cancelled",
                            percent=current.progress_percent,
                            message="任务已取消",
                            level="warning",
                            now=now,
                        )
                        self._bump_status_revision_locked(current)
                        self._finalize_stage_tracking_locked(current, now=now)
                        self._persist_record_locked(current)
                    else:
                        if exc.code == "llm_invalid_json":
                            if current.job_kind == "llm_resume":
                                partial = _build_partial_from_sentences(
                                    current.resume_sentences,
                                    current.resume_word_segments,
                                )
                            else:
                                partial = _build_partial_result(current.work_dir)
                            if partial:
                                partial["partial"] = True
                                partial["partial_stage"] = exc.stage
                                partial["partial_error"] = exc.message
                                now = _now()
                                current.status = "completed"
                                current.progress_percent = 100
                                self._transition_stage_locked(current, "completed", now=now)
                                current.message = "任务完成（LLM 输出异常，已保留基础字幕）"
                                current.error = None
                                current.error_code = str(exc.code or "llm_invalid_json")
                                current.error_detail = exc.to_dict()
                                current.result = partial
                                current.sync_diagnostics = self._extract_sync_diagnostics_from_result(partial)
                                current.completed_at = now
                                current.updated_at = now
                                self._set_stage_detail_locked(
                                    current,
                                    stage="completed",
                                    now=now,
                                    detail={
                                        "step_key": "completed_with_partial",
                                        "step_label": "任务完成（保留基础字幕）",
                                        "percent_in_stage": 100,
                                    },
                                )
                                self._append_progress_event_locked(
                                    current,
                                    stage="completed",
                                    percent=100,
                                    message="任务完成（LLM 输出异常，已保留基础字幕）",
                                    level="warning",
                                    now=now,
                                )
                                self._bump_status_revision_locked(current)
                                self._finalize_stage_tracking_locked(current, now=now)
                                self._persist_record_locked(current)
                                print(f"[DEBUG] Completed job {job_id} with partial result due to LLM JSON error.")
                                continue
                        now = _now()
                        current.status = "failed"
                        self._transition_stage_locked(current, exc.stage, now=now)
                        current.message = exc.message
                        current.error = exc.message
                        current.error_code = str(exc.code or "pipeline_failed")
                        current.error_detail = exc.to_dict()
                        if current.job_kind == "llm_resume":
                            partial = _build_partial_from_sentences(
                                current.resume_sentences,
                                current.resume_word_segments,
                            )
                        else:
                            partial = _build_partial_result(current.work_dir)
                        if partial:
                            partial["partial"] = True
                            partial["partial_stage"] = exc.stage
                            partial["partial_error"] = exc.message
                            current.partial_result = partial
                            current.sync_diagnostics = self._extract_sync_diagnostics_from_result(partial)
                        else:
                            current.sync_diagnostics = {}
                        current.completed_at = now
                        current.updated_at = now
                        self._set_stage_detail_locked(
                            current,
                            stage=exc.stage,
                            now=now,
                            detail={"step_key": "failed", "step_label": exc.message, "percent_in_stage": 100},
                        )
                        self._append_progress_event_locked(
                            current,
                            stage=exc.stage,
                            percent=current.progress_percent,
                            message=exc.message,
                            level="error",
                            now=now,
                        )
                        self._bump_status_revision_locked(current)
                        self._finalize_stage_tracking_locked(current, now=now)
                        self._persist_record_locked(current)
            except Exception as exc:
                with self._lock:
                    current = self._jobs.get(job_id)
                    if not current:
                        continue
                    now = _now()
                    current.status = "failed"
                    self._transition_stage_locked(current, "pipeline", now=now)
                    current.message = "任务执行失败"
                    current.error = str(exc)
                    current.error_code = "unexpected"
                    current.error_detail = {
                        "stage": "pipeline",
                        "code": "unexpected",
                        "message": str(exc),
                    }
                    current.sync_diagnostics = {}
                    current.completed_at = now
                    current.updated_at = now
                    self._set_stage_detail_locked(
                        current,
                        stage="pipeline",
                        now=now,
                        detail={"step_key": "failed", "step_label": "任务执行失败", "percent_in_stage": 100},
                    )
                    self._append_progress_event_locked(
                        current,
                        stage="pipeline",
                        percent=current.progress_percent,
                        message="任务执行失败",
                        level="error",
                        now=now,
                    )
                    self._bump_status_revision_locked(current)
                    self._finalize_stage_tracking_locked(current, now=now)
                    self._persist_record_locked(current)
            finally:
                with self._lock:
                    if job_started:
                        self._active_jobs_total = max(0, self._active_jobs_total - 1)
                        if active_user_id:
                            next_count = max(0, int(self._active_by_user.get(active_user_id, 0)) - 1)
                            if next_count <= 0:
                                self._active_by_user.pop(active_user_id, None)
                            else:
                                self._active_by_user[active_user_id] = next_count
                    self._cleanup_expired_locked()
                self._queue.task_done()

    def _cleanup_expired_locked(self) -> None:
        now = _now()
        expire_failed = now - timedelta(days=7)
        expire_cancelled = now - timedelta(days=7)
        expire_consumed = now - timedelta(minutes=10)
        remove_ids: list[str] = []
        for job_id, record in self._jobs.items():
            if record.status == "failed" and record.updated_at < expire_failed:
                safe_rmtree(record.work_dir)
                remove_ids.append(job_id)
                continue
            if record.status == "cancelled" and record.updated_at < expire_cancelled:
                safe_rmtree(record.work_dir)
                remove_ids.append(job_id)
                continue
            if record.result_consumed and record.updated_at < expire_consumed:
                safe_rmtree(record.work_dir)
                remove_ids.append(job_id)
        for job_id in remove_ids:
            self._jobs.pop(job_id, None)
            self._delete_record_locked(job_id=job_id)
