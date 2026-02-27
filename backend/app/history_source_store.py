from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
        return None


@dataclass(frozen=True)
class HistorySourceResolveResult:
    source_asset_id: str
    status: str
    video_path: str = ""
    expires_at: str = ""
    detail: str = ""


class HistorySourceStore:
    def __init__(self, root_dir: str, retention_days: int = 30) -> None:
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._retention_days = max(1, int(retention_days or 30))

    @property
    def root_dir(self) -> Path:
        return self._root

    @property
    def retention_days(self) -> int:
        return self._retention_days

    def _normalize_asset_id(self, source_asset_id: str) -> str:
        value = re.sub(r"[^0-9A-Za-z_\-.]", "_", str(source_asset_id or "").strip())
        return value[:120].strip("._-")

    def _asset_dir(self, source_asset_id: str) -> Path:
        safe_id = self._normalize_asset_id(source_asset_id)
        if not safe_id:
            raise ValueError("source_asset_id is required")
        return self._root / safe_id

    @staticmethod
    def _meta_path(asset_dir: Path) -> Path:
        return asset_dir / "meta.json"

    @staticmethod
    def _load_meta(meta_path: Path) -> dict[str, Any] | None:
        if not meta_path.exists():
            return None
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception as exc:  # noqa: BLE001
            print(f"history-source: failed to load meta {meta_path}: {exc}")
        return None

    @staticmethod
    def _find_video_file(asset_dir: Path) -> Path | None:
        candidates = [item for item in asset_dir.iterdir() if item.is_file() and item.name != "meta.json"]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        return candidates[0]

    def archive(self, source_asset_id: str, local_video_path: str) -> dict[str, Any]:
        asset_dir = self._asset_dir(source_asset_id)
        source = Path(local_video_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"source video not found: {source}")

        asset_dir.mkdir(parents=True, exist_ok=True)
        ext = source.suffix or ".mp4"
        target_video = asset_dir / f"source{ext}"
        shutil.copy2(source, target_video)

        now = _now_utc()
        expires_at = now + timedelta(days=self._retention_days)
        payload = {
            "source_asset_id": asset_dir.name,
            "video_file_name": target_video.name,
            "video_path": str(target_video),
            "created_at": _to_iso(now),
            "expires_at": _to_iso(expires_at),
        }
        self._meta_path(asset_dir).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return payload

    def resolve(self, source_asset_id: str) -> HistorySourceResolveResult:
        try:
            asset_dir = self._asset_dir(source_asset_id)
        except ValueError:
            return HistorySourceResolveResult(source_asset_id="", status="missing", detail="source_asset_id is required")

        if not asset_dir.exists():
            return HistorySourceResolveResult(source_asset_id=asset_dir.name, status="missing", detail="asset directory not found")

        meta = self._load_meta(self._meta_path(asset_dir)) or {}
        expires_at = _parse_iso(meta.get("expires_at"))
        if expires_at and _now_utc() >= expires_at:
            return HistorySourceResolveResult(
                source_asset_id=asset_dir.name,
                status="expired",
                expires_at=_to_iso(expires_at),
                detail="source expired",
            )

        video_file_name = str(meta.get("video_file_name") or "").strip()
        video_path = (asset_dir / video_file_name).resolve() if video_file_name else self._find_video_file(asset_dir)
        if not video_path or not Path(video_path).is_file():
            return HistorySourceResolveResult(source_asset_id=asset_dir.name, status="missing", detail="video file not found")

        return HistorySourceResolveResult(
            source_asset_id=asset_dir.name,
            status="ready",
            video_path=str(video_path),
            expires_at=_to_iso(expires_at) if expires_at else "",
        )

    def purge_expired(self) -> int:
        removed = 0
        for asset_dir in self._root.iterdir():
            if not asset_dir.is_dir():
                continue

            meta = self._load_meta(self._meta_path(asset_dir))
            expires_at = _parse_iso((meta or {}).get("expires_at"))
            if expires_at is None:
                continue
            if _now_utc() < expires_at:
                continue

            shutil.rmtree(asset_dir, ignore_errors=True)
            removed += 1
        return removed
