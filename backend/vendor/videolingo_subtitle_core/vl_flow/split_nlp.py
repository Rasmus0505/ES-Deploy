from __future__ import annotations

import re
from typing import Iterable


_SPLIT_MARK_PATTERN = re.compile(r"(?<=[。！？!?;；\.])\s+")


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _split_by_marks(text: str) -> list[str]:
    value = _normalize_text(text)
    if not value:
        return []
    parts = [item.strip() for item in _SPLIT_MARK_PATTERN.split(value) if item and item.strip()]
    return parts or [value]


def _split_long_by_comma(text: str, max_chars: int = 80) -> list[str]:
    value = _normalize_text(text)
    if not value:
        return []
    if len(value) <= max_chars:
        return [value]

    comma_matches = [m.start() for m in re.finditer(r"[,，]", value)]
    if not comma_matches:
        return [value]

    midpoint = len(value) // 2
    split_at = min(comma_matches, key=lambda pos: abs(pos - midpoint))
    left = value[: split_at + 1].strip()
    right = value[split_at + 1 :].strip()
    if not left or not right:
        return [value]
    return [left, right]


def split_text(text: str) -> list[str]:
    sentences: list[str] = []
    for segment in _split_by_marks(text):
        sentences.extend(_split_long_by_comma(segment))
    return [item for item in sentences if item]


def split_segments(segments: Iterable[dict]) -> list[dict]:
    rows: list[dict] = []
    for segment in segments or []:
        text = str((segment or {}).get("text") or "").strip()
        if not text:
            continue
        for part in split_text(text):
            rows.append({"text": part})
    return rows
