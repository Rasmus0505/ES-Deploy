from __future__ import annotations

import json
import math
import sqlite3
import threading
from pathlib import Path
from typing import Any


def _now_ms() -> int:
    from time import time

    return int(round(time() * 1000))


def _normalize_route_mode(value: Any, *, fallback: str) -> str:
    safe = str(value or "").strip().lower()
    if safe not in {"dashscope_direct", "oneapi_fallback"}:
        return fallback
    return safe


def _safe_non_negative_float(value: Any, *, fallback: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return fallback
    if not math.isfinite(parsed) or parsed < 0:
        return fallback
    return parsed


def _safe_positive_int(value: Any, *, fallback: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return fallback
    return parsed if parsed > 0 else fallback


def _normalize_model_multipliers(payload: Any) -> dict[str, float]:
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, float] = {}
    for raw_key, raw_value in payload.items():
        key = str(raw_key or "").strip().lower()
        if not key:
            continue
        try:
            value = float(raw_value)
        except Exception:
            continue
        if not math.isfinite(value) or value < 0:
            continue
        normalized[key] = value
    return normalized


def _normalize_model_enabled(
    payload: Any, *, defaults: dict[str, bool]
) -> dict[str, bool]:
    normalized = {
        str(key).strip().lower(): bool(value) for key, value in defaults.items()
    }
    if not isinstance(payload, dict):
        return normalized
    for raw_key, raw_value in payload.items():
        key = str(raw_key or "").strip().lower()
        if not key:
            continue
        normalized[key] = bool(raw_value)
    return normalized


class AsrRuntimeConfigStore:
    def __init__(
        self,
        *,
        db_path: str,
        default_route_mode: str,
        default_dashscope_base_url: str,
        default_global_multiplier: float,
        default_submit_min_remaining_quota: int,
        default_model_enabled: dict[str, bool] | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._default_route_mode = _normalize_route_mode(
            default_route_mode,
            fallback="oneapi_fallback",
        )
        self._default_dashscope_base_url = str(default_dashscope_base_url or "").strip()
        self._default_global_multiplier = max(
            0.0, float(default_global_multiplier or 0.0)
        )
        self._default_submit_min_remaining_quota = max(
            1,
            int(default_submit_min_remaining_quota or 1),
        )
        self._default_model_enabled = {
            "paraformer-v2": True,
            "qwen3-asr-flash-filetrans": True,
        }
        if isinstance(default_model_enabled, dict):
            for raw_key, raw_value in default_model_enabled.items():
                key = str(raw_key or "").strip().lower()
                if not key:
                    continue
                self._default_model_enabled[key] = bool(raw_value)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self._db_path), timeout=30, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS subtitle_asr_runtime_config (
                        id INTEGER PRIMARY KEY CHECK(id = 1),
                        route_mode TEXT NOT NULL DEFAULT 'oneapi_fallback',
                        dashscope_base_url TEXT NOT NULL DEFAULT '',
                        global_multiplier REAL NOT NULL DEFAULT 0,
                        model_multipliers_json TEXT NOT NULL DEFAULT '{}',
                        model_enabled_json TEXT NOT NULL DEFAULT '{}',
                        submit_min_remaining_quota INTEGER NOT NULL DEFAULT 1,
                        updated_by TEXT NOT NULL DEFAULT '',
                        note TEXT NOT NULL DEFAULT '',
                        updated_at INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                row = connection.execute(
                    "SELECT id FROM subtitle_asr_runtime_config WHERE id=1 LIMIT 1"
                ).fetchone()
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO subtitle_asr_runtime_config(
                            id,
                            route_mode,
                            dashscope_base_url,
                            global_multiplier,
                            model_multipliers_json,
                            model_enabled_json,
                            submit_min_remaining_quota,
                            updated_by,
                            note,
                            updated_at
                        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            self._default_route_mode,
                            self._default_dashscope_base_url,
                            self._default_global_multiplier,
                            "{}",
                            json.dumps(self._default_model_enabled, ensure_ascii=False),
                            self._default_submit_min_remaining_quota,
                            "system:init",
                            "",
                            _now_ms(),
                        ),
                    )
                connection.commit()

    def _defaults_payload(self) -> dict[str, Any]:
        return {
            "route_mode": self._default_route_mode,
            "dashscope_base_url": self._default_dashscope_base_url,
            "global_multiplier": self._default_global_multiplier,
            "model_multipliers": {},
            "model_enabled": dict(self._default_model_enabled),
            "submit_min_remaining_quota": self._default_submit_min_remaining_quota,
            "updated_by": "",
            "note": "",
            "updated_at": 0,
        }

    @staticmethod
    def _json_loads_dict(raw: Any) -> dict[str, Any]:
        try:
            payload = json.loads(str(raw or "{}"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        defaults = self._defaults_payload()
        route_mode = _normalize_route_mode(
            payload.get("route_mode"),
            fallback=str(defaults["route_mode"]),
        )
        dashscope_base_url = str(
            payload.get("dashscope_base_url") or defaults["dashscope_base_url"]
        ).strip() or str(defaults["dashscope_base_url"])
        global_multiplier = _safe_non_negative_float(
            payload.get("global_multiplier"),
            fallback=float(defaults["global_multiplier"]),
        )
        model_multipliers = _normalize_model_multipliers(
            payload.get("model_multipliers")
        )
        model_enabled = _normalize_model_enabled(
            payload.get("model_enabled"),
            defaults=defaults["model_enabled"],
        )
        submit_min_remaining_quota = _safe_positive_int(
            payload.get("submit_min_remaining_quota"),
            fallback=int(defaults["submit_min_remaining_quota"]),
        )
        return {
            "route_mode": route_mode,
            "dashscope_base_url": dashscope_base_url,
            "global_multiplier": global_multiplier,
            "model_multipliers": model_multipliers,
            "model_enabled": model_enabled,
            "submit_min_remaining_quota": submit_min_remaining_quota,
            "updated_by": str(payload.get("updated_by") or "").strip(),
            "note": str(payload.get("note") or "").strip(),
            "updated_at": max(0, int(payload.get("updated_at") or 0)),
        }

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT
                        route_mode,
                        dashscope_base_url,
                        global_multiplier,
                        model_multipliers_json,
                        model_enabled_json,
                        submit_min_remaining_quota,
                        updated_by,
                        note,
                        updated_at
                    FROM subtitle_asr_runtime_config
                    WHERE id=1
                    LIMIT 1
                    """
                ).fetchone()
        if row is None:
            return self._defaults_payload()
        raw_payload = {
            "route_mode": row["route_mode"],
            "dashscope_base_url": row["dashscope_base_url"],
            "global_multiplier": row["global_multiplier"],
            "model_multipliers": self._json_loads_dict(row["model_multipliers_json"]),
            "model_enabled": self._json_loads_dict(row["model_enabled_json"]),
            "submit_min_remaining_quota": row["submit_min_remaining_quota"],
            "updated_by": row["updated_by"],
            "note": row["note"],
            "updated_at": row["updated_at"],
        }
        return self._normalize_payload(raw_payload)

    def update_config(
        self,
        *,
        patch: dict[str, Any],
        updated_by: str,
        note: str = "",
    ) -> dict[str, Any]:
        safe_patch = patch if isinstance(patch, dict) else {}
        with self._lock:
            current = self.get_config()
            merged = {
                **current,
                **safe_patch,
                "updated_by": str(updated_by or "").strip()
                or current.get("updated_by")
                or "admin",
                "note": str(note or safe_patch.get("note") or "").strip(),
                "updated_at": _now_ms(),
            }
            normalized = self._normalize_payload(merged)
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE subtitle_asr_runtime_config
                    SET
                        route_mode=?,
                        dashscope_base_url=?,
                        global_multiplier=?,
                        model_multipliers_json=?,
                        model_enabled_json=?,
                        submit_min_remaining_quota=?,
                        updated_by=?,
                        note=?,
                        updated_at=?
                    WHERE id=1
                    """,
                    (
                        normalized["route_mode"],
                        normalized["dashscope_base_url"],
                        float(normalized["global_multiplier"]),
                        json.dumps(normalized["model_multipliers"], ensure_ascii=False),
                        json.dumps(normalized["model_enabled"], ensure_ascii=False),
                        int(normalized["submit_min_remaining_quota"]),
                        str(normalized["updated_by"] or "admin"),
                        str(normalized["note"] or ""),
                        int(normalized["updated_at"]),
                    ),
                )
                connection.commit()
        return normalized

    def resolve_multiplier(self, *, model: str) -> float:
        config = self.get_config()
        model_key = str(model or "").strip().lower()
        model_multipliers = (
            config.get("model_multipliers")
            if isinstance(config.get("model_multipliers"), dict)
            else {}
        )
        if model_key and model_key in model_multipliers:
            value = _safe_non_negative_float(
                model_multipliers.get(model_key),
                fallback=float(config.get("global_multiplier") or 0.0),
            )
            return max(0.0, value)
        return max(0.0, float(config.get("global_multiplier") or 0.0))

    def is_model_enabled(self, *, model: str) -> bool:
        config = self.get_config()
        model_key = str(model or "").strip().lower()
        model_enabled = (
            config.get("model_enabled")
            if isinstance(config.get("model_enabled"), dict)
            else {}
        )
        if not model_key:
            return True
        if model_key not in model_enabled:
            return True
        return bool(model_enabled.get(model_key))
