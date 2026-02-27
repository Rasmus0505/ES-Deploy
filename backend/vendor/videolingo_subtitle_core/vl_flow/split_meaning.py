from __future__ import annotations

import math
import re
import time
from difflib import SequenceMatcher

from .prompts import get_split_prompt
from .types import CancelGuard, FlowConfig, FlowError, JsonChatFn, ProgressReporter


def _token_count(text: str) -> int:
    tokens = re.findall(r"\S+", str(text or ""))
    if len(tokens) > 1:
        return len(tokens)
    return len(str(text or ""))


def _fallback_split(text: str, num_parts: int) -> list[str]:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value or num_parts <= 1:
        return [value] if value else []

    boundaries = [m.end() for m in re.finditer(r"[,，。！？!?;；:]", value)]
    if not boundaries:
        boundaries = [m.start() for m in re.finditer(r"\s+", value)]
    if not boundaries:
        part_length = max(1, len(value) // num_parts)
        boundaries = [part_length * idx for idx in range(1, num_parts)]

    points: list[int] = []
    used = set()
    for idx in range(1, num_parts):
        target = (len(value) * idx) // num_parts
        split_at = min(boundaries, key=lambda pos: abs(pos - target))
        if split_at in used:
            continue
        used.add(split_at)
        points.append(split_at)

    points = sorted(pos for pos in points if 0 < pos < len(value))
    if not points:
        return [value]

    start = 0
    parts: list[str] = []
    for pos in points:
        parts.append(value[start:pos].strip())
        start = pos
    parts.append(value[start:].strip())
    return [item for item in parts if item]


def _find_split_positions(original: str, split_with_br: str) -> list[int]:
    compact_original = re.sub(r"\s+", "", original)
    parts = [re.sub(r"\s+", "", item) for item in split_with_br.split("[br]")]
    if len(parts) <= 1:
        return []

    positions: list[int] = []
    cursor = 0
    for idx in range(len(parts) - 1):
        part = parts[idx]
        best_pos = None
        best_score = 0.0
        for current in range(cursor, len(compact_original) + 1):
            left = compact_original[cursor:current]
            score = SequenceMatcher(None, left, part).ratio()
            if score >= best_score:
                best_score = score
                best_pos = current
        if best_pos is None:
            continue
        positions.append(best_pos)
        cursor = best_pos
    return positions


def _remap_positions_to_original(original: str, compact_positions: list[int]) -> list[int]:
    if not compact_positions:
        return []

    mapping: list[int] = []
    compact_index = 0
    for raw_index, char in enumerate(original):
        if char.isspace():
            continue
        compact_index += 1
        while compact_positions and compact_index == compact_positions[0]:
            mapping.append(raw_index + 1)
            compact_positions.pop(0)
            if not compact_positions:
                return mapping
    return mapping


def _split_with_llm(
    *,
    text: str,
    num_parts: int,
    config: FlowConfig,
    chat_json: JsonChatFn,
) -> list[str]:
    prompt = get_split_prompt(
        sentence=text,
        num_parts=max(2, int(num_parts)),
        word_limit=max(8, int(config.max_split_length)),
        source_language=config.source_language,
    )
    payload = chat_json(prompt)

    split_candidate = ""
    if isinstance(payload, dict):
        if "split" in payload:
            split_candidate = str(payload.get("split") or "")
        elif "choice" in payload:
            choice = str(payload.get("choice") or "").strip()
            split_candidate = str(payload.get(f"split{choice}") or "")
        elif "split1" in payload:
            split_candidate = str(payload.get("split1") or "")
    if "[br]" not in split_candidate:
        raise FlowError(
            "meaning_split",
            "meaning_split_invalid",
            "语义分句返回格式错误",
            detail=str(payload)[:600],
        )

    compact_positions = _find_split_positions(text, split_candidate)
    mapped_positions = _remap_positions_to_original(text, compact_positions)
    if not mapped_positions:
        parts = [item.strip() for item in split_candidate.split("[br]") if item and item.strip()]
        if len(parts) >= 2:
            return parts
        raise FlowError(
            "meaning_split",
            "meaning_split_invalid",
            "语义分句失败：未找到可用切分位置",
            detail=split_candidate[:600],
        )

    start = 0
    output: list[str] = []
    for pos in mapped_positions:
        output.append(text[start:pos].strip())
        start = pos
    output.append(text[start:].strip())

    normalized = [item for item in output if item]
    if len(normalized) < 2:
        raise FlowError(
            "meaning_split",
            "meaning_split_invalid",
            "语义分句失败：切分结果不足 2 段",
            detail=split_candidate[:600],
        )
    return normalized


def split_sentences_by_meaning(
    *,
    sentences: list[dict],
    config: FlowConfig,
    chat_json: JsonChatFn,
    cancel_guard: CancelGuard | None = None,
    progress_reporter: ProgressReporter | None = None,
) -> list[dict]:
    current: list[dict] = []
    for row in sentences or []:
        text = str((row or {}).get("text") or "").strip()
        if not text:
            continue
        current.append({"text": text, "translation": str((row or {}).get("translation") or "").strip()})

    if not current:
        return []

    total_rounds = max(1, int(config.meaning_split_rounds))
    started_at = time.monotonic()
    if progress_reporter:
        progress_reporter(
            {
                "step_key": "meaning_split",
                "step_label": "语义分句",
                "done": 0,
                "total": len(current),
                "unit": "row",
                "percent_in_stage": 0,
                "eta_seconds": None,
            }
        )
    for round_index in range(total_rounds):
        changed = False
        next_rows: list[dict] = []
        round_total = max(1, len(current))
        for row_index, row in enumerate(current):
            if cancel_guard:
                cancel_guard()
            text = str(row.get("text") or "").strip()
            translation = str(row.get("translation") or "").strip()
            if not text:
                continue

            token_count = _token_count(text)
            if token_count <= max(1, int(config.max_split_length)):
                next_rows.append({"text": text, "translation": translation})
            else:
                num_parts = max(2, int(math.ceil(token_count / max(1, int(config.max_split_length)))))
                try:
                    parts = _split_with_llm(
                        text=text,
                        num_parts=num_parts,
                        config=config,
                        chat_json=chat_json,
                    )
                except FlowError:
                    raise
                except Exception:
                    parts = _fallback_split(text, num_parts)

                if len(parts) < 2:
                    next_rows.append({"text": text, "translation": translation})
                else:
                    changed = True
                    for idx, part in enumerate(parts):
                        next_rows.append(
                            {
                                "text": str(part).strip(),
                                "translation": translation if idx == 0 else "",
                            }
                        )

            if progress_reporter:
                done = row_index + 1
                round_base = (round_index / total_rounds) * 100
                round_progress = (done / round_total) * (100 / total_rounds)
                percent_in_stage = int(round(round_base + round_progress))
                elapsed = max(0.0, time.monotonic() - started_at)
                eta_seconds = None
                completed_units = (round_index * round_total) + done
                expected_units = max(1, total_rounds * round_total)
                if completed_units > 0 and completed_units < expected_units and elapsed > 0:
                    eta_seconds = int(round((elapsed / completed_units) * (expected_units - completed_units)))
                progress_reporter(
                    {
                        "step_key": "meaning_split",
                        "step_label": f"语义分句 第{round_index + 1}轮",
                        "done": done,
                        "total": round_total,
                        "unit": "row",
                        "percent_in_stage": max(0, min(100, percent_in_stage)),
                        "eta_seconds": eta_seconds,
                    }
                )

        current = [row for row in next_rows if str(row.get("text") or "").strip()]
        if not changed:
            break

    return current
