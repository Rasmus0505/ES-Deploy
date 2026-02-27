from __future__ import annotations

import json
import re
import time
from difflib import SequenceMatcher

from .prompts import get_align_prompt, get_split_prompt
from .types import CancelGuard, FlowConfig, FlowError, JsonChatFn, ProgressReporter


def calc_weighted_length(text: str) -> float:
    value = str(text or "")

    def char_weight(char: str) -> float:
        code = ord(char)
        if 0x4E00 <= code <= 0x9FFF or 0x3040 <= code <= 0x30FF:
            return 1.75
        if 0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF:
            return 1.5
        if 0x0E00 <= code <= 0x0E7F:
            return 1.0
        if 0xFF01 <= code <= 0xFF5E:
            return 1.75
        return 1.0

    return sum(char_weight(char) for char in value)


def needs_secondary_split(*, source_text: str, translation: str, config: FlowConfig) -> bool:
    if len(str(source_text or "")) > max(1, int(config.subtitle_max_length)):
        return True
    target_weighted = calc_weighted_length(str(translation or ""))
    return target_weighted * float(config.subtitle_target_multiplier) > max(1, int(config.subtitle_max_length))


def _rule_split_source(text: str) -> list[str]:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return []
    midpoint = len(value) // 2
    candidates = [m.end() for m in re.finditer(r"[,，。！？!?;；:]", value)]
    if not candidates:
        candidates = [m.start() for m in re.finditer(r"\s+", value)]
    if not candidates:
        return [value]

    split_at = min(candidates, key=lambda pos: abs(pos - midpoint))
    left = value[:split_at].strip()
    right = value[split_at:].strip()
    if not left or not right:
        return [value]
    return [left, right]


def _find_split_positions(original: str, split_with_br: str) -> list[int]:
    compact_original = re.sub(r"\s+", "", original)
    parts = [re.sub(r"\s+", "", item) for item in split_with_br.split("[br]")]
    if len(parts) <= 1:
        return []

    split_positions: list[int] = []
    start = 0
    for idx in range(len(parts) - 1):
        max_similarity = 0.0
        best_split = None
        target = parts[idx]
        for current in range(start, len(compact_original) + 1):
            original_left = compact_original[start:current]
            score = SequenceMatcher(None, original_left, target).ratio()
            if score >= max_similarity:
                max_similarity = score
                best_split = current
        if best_split is None:
            continue
        split_positions.append(best_split)
        start = best_split
    return split_positions


def _remap_to_original_indices(original: str, compact_positions: list[int]) -> list[int]:
    if not compact_positions:
        return []

    points = list(compact_positions)
    output: list[int] = []
    compact_idx = 0
    for raw_idx, char in enumerate(original):
        if char.isspace():
            continue
        compact_idx += 1
        while points and compact_idx == points[0]:
            output.append(raw_idx + 1)
            points.pop(0)
            if not points:
                return output
    return output


def _split_source_with_llm(text: str, config: FlowConfig, chat_json: JsonChatFn) -> list[str]:
    prompt = get_split_prompt(
        sentence=text,
        num_parts=2,
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
        return _rule_split_source(text)

    compact_positions = _find_split_positions(text, split_candidate)
    mapped_positions = _remap_to_original_indices(text, compact_positions)
    if not mapped_positions:
        parts = [item.strip() for item in split_candidate.split("[br]") if item and item.strip()]
        return parts if len(parts) >= 2 else _rule_split_source(text)

    start = 0
    out: list[str] = []
    for pos in mapped_positions:
        out.append(text[start:pos].strip())
        start = pos
    out.append(text[start:].strip())
    normalized = [item for item in out if item]
    return normalized if len(normalized) >= 2 else _rule_split_source(text)


def _align_translation_parts(
    *,
    source_text: str,
    translation: str,
    source_parts: list[str],
    config: FlowConfig,
    chat_json: JsonChatFn,
) -> list[str]:
    prompt = get_align_prompt(
        source_text=source_text,
        translation=translation,
        source_parts=source_parts,
        source_language=config.source_language,
        target_language=config.target_language,
    )
    payload = chat_json(prompt)
    if not isinstance(payload, dict):
        raise FlowError("split_subtitles", "subtitle_split_align_invalid", "字幕二次切分失败", detail=str(payload)[:600])

    align_rows = payload.get("align")
    if not isinstance(align_rows, list) or len(align_rows) < len(source_parts):
        raise FlowError(
            "split_subtitles",
            "subtitle_split_align_invalid",
            "字幕二次切分失败：译文对齐返回格式错误",
            detail=json.dumps(payload, ensure_ascii=False)[:600],
        )

    translations: list[str] = []
    for idx in range(len(source_parts)):
        row = align_rows[idx]
        key = f"target_part_{idx + 1}"
        value = str((row or {}).get(key) or "").strip()
        if not value:
            raise FlowError(
                "split_subtitles",
                "subtitle_split_align_invalid",
                "字幕二次切分失败：译文分段包含空文本",
                detail=json.dumps(payload, ensure_ascii=False)[:600],
            )
        translations.append(value)

    return translations


def split_subtitles(
    *,
    rows: list[dict],
    config: FlowConfig,
    chat_json: JsonChatFn,
    cancel_guard: CancelGuard | None = None,
    progress_reporter: ProgressReporter | None = None,
) -> tuple[list[dict], list[dict]]:
    current: list[dict] = []
    for row in rows or []:
        text = str((row or {}).get("text") or "").strip()
        if not text:
            continue
        current.append({"text": text, "translation": str((row or {}).get("translation") or "").strip()})

    total_rounds = max(1, int(config.subtitle_split_rounds))
    started_at = time.monotonic()
    debug_rounds: list[dict] = []
    if progress_reporter:
        progress_reporter(
            {
                "step_key": "split_subtitles",
                "step_label": "长句拆分",
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
        split_count = 0
        round_total = max(1, len(current))

        for row_index, row in enumerate(current):
            if cancel_guard:
                cancel_guard()

            text = str(row.get("text") or "").strip()
            translation = str(row.get("translation") or "").strip()
            if not needs_secondary_split(source_text=text, translation=translation, config=config):
                next_rows.append({"text": text, "translation": translation})
            else:
                source_parts = _split_source_with_llm(text, config, chat_json)
                if len(source_parts) < 2:
                    next_rows.append({"text": text, "translation": translation})
                else:
                    translation_parts = _align_translation_parts(
                        source_text=text,
                        translation=translation,
                        source_parts=source_parts,
                        config=config,
                        chat_json=chat_json,
                    )
                    if len(translation_parts) != len(source_parts):
                        raise FlowError(
                            "split_subtitles",
                            "subtitle_split_align_invalid",
                            "字幕二次切分失败：译文分段数量与原文不一致",
                            detail=json.dumps({"source_parts": len(source_parts), "target_parts": len(translation_parts)}),
                        )

                    changed = True
                    split_count += 1
                    for idx, part in enumerate(source_parts):
                        next_rows.append({"text": part.strip(), "translation": translation_parts[idx].strip()})

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
                        "step_key": "split_subtitles",
                        "step_label": f"长句拆分 第{round_index + 1}轮",
                        "done": done,
                        "total": round_total,
                        "unit": "row",
                        "percent_in_stage": max(0, min(100, percent_in_stage)),
                        "eta_seconds": eta_seconds,
                    }
                )

        current = [row for row in next_rows if str(row.get("text") or "").strip()]
        debug_rounds.append(
            {
                "round": round_index + 1,
                "changed": changed,
                "split_count": split_count,
                "row_count": len(current),
            }
        )
        if not changed:
            break

    return current, debug_rounds
