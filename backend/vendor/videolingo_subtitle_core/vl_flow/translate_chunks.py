from __future__ import annotations

import json
import time
from difflib import SequenceMatcher

from .prompts import get_translate_chunk_prompt
from .summary_terms import search_terms_in_text
from .types import CancelGuard, FlowConfig, FlowError, JsonChatFn, ProgressReporter, SummaryTerms


def _split_chunks_by_chars(lines: list[str], *, chunk_size: int, max_lines: int) -> list[tuple[int, int]]:
    if not lines:
        return []
    chunks: list[tuple[int, int]] = []
    start = 0
    cursor = 0
    char_count = 0
    line_count = 0
    while cursor < len(lines):
        line = lines[cursor]
        line_chars = len(line) + 1
        should_flush = False
        if line_count >= max(1, int(max_lines)):
            should_flush = True
        elif line_count > 0 and char_count + line_chars > max(1, int(chunk_size)):
            should_flush = True

        if should_flush:
            chunks.append((start, cursor))
            start = cursor
            char_count = 0
            line_count = 0
            continue

        char_count += line_chars
        line_count += 1
        cursor += 1

    if start < len(lines):
        chunks.append((start, len(lines)))
    return chunks


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def _validate_chunk_result(payload: dict, lines: list[str], stage: str) -> list[str]:
    if not isinstance(payload, dict):
        raise FlowError(stage, "translation_invalid", "翻译返回格式错误", detail=str(payload)[:600])

    translations: list[str] = []
    for idx, line in enumerate(lines, start=1):
        key = str(idx)
        item = payload.get(key)
        if not isinstance(item, dict):
            raise FlowError(stage, "translation_invalid", "翻译返回缺少行结果", detail=str(payload)[:600])

        origin = str(item.get("origin") or "").strip()
        translation = str(item.get("translation") or "").strip()
        if not translation:
            raise FlowError(stage, "translation_invalid", "翻译返回空文本", detail=str(payload)[:600])

        if origin:
            if _similarity(origin.lower(), line.lower()) < 0.9:
                raise FlowError(
                    stage,
                    "translation_mismatch",
                    "翻译校验失败：原文映射不一致",
                    detail=json.dumps({"expected": line, "actual": origin}, ensure_ascii=False)[:600],
                )

        translations.append(translation)
    return translations


def translate_sentences_by_chunks(
    *,
    sentences: list[dict],
    config: FlowConfig,
    summary: SummaryTerms,
    chat_json: JsonChatFn,
    cancel_guard: CancelGuard | None = None,
    progress_reporter: ProgressReporter | None = None,
) -> tuple[list[dict], int, list[dict]]:
    rows: list[dict] = []
    for row in sentences or []:
        text = str((row or {}).get("text") or "").strip()
        if not text:
            continue
        rows.append({"text": text, "translation": str((row or {}).get("translation") or "").strip()})

    if not rows:
        return [], 0, []

    lines = [row["text"] for row in rows]
    chunks = _split_chunks_by_chars(
        lines,
        chunk_size=config.translate_chunk_chars,
        max_lines=config.translate_chunk_max_lines,
    )

    translated_lines: list[str] = [""] * len(lines)
    debug_chunks: list[dict] = []
    total_chunks = len(chunks)
    chunk_started_at = time.monotonic()
    if progress_reporter:
        progress_reporter(
            {
                "step_key": "translate_chunk",
                "step_label": "分块翻译",
                "done": 0,
                "total": total_chunks,
                "unit": "chunk",
                "percent_in_stage": 0,
                "eta_seconds": None,
            }
        )
    for chunk_index, (start, end) in enumerate(chunks):
        if cancel_guard:
            cancel_guard()

        chunk_lines = lines[start:end]
        previous_lines = lines[max(0, start - config.translate_context_prev) : start]
        after_lines = lines[end : min(len(lines), end + config.translate_context_next)]
        matched_terms = search_terms_in_text("\n".join(chunk_lines), summary.terms)
        prompt = get_translate_chunk_prompt(
            lines=chunk_lines,
            previous_lines=previous_lines,
            after_lines=after_lines,
            theme=summary.theme,
            terms=matched_terms,
            source_language=config.source_language,
            target_language=config.target_language,
        )

        payload = chat_json(prompt)
        chunk_translations = _validate_chunk_result(payload, chunk_lines, stage="translate_chunks")
        for idx, text in enumerate(chunk_translations):
            translated_lines[start + idx] = text

        debug_chunks.append(
            {
                "chunk_index": chunk_index,
                "start": start,
                "end": end,
                "line_count": end - start,
            }
        )
        if progress_reporter:
            done = chunk_index + 1
            percent_in_stage = int(round((done / max(1, total_chunks)) * 100))
            elapsed = max(0.0, time.monotonic() - chunk_started_at)
            eta_seconds = None
            if done > 0 and done < total_chunks and elapsed > 0:
                remaining_chunks = max(0, total_chunks - done)
                eta_seconds = int(round((elapsed / done) * remaining_chunks))
            progress_reporter(
                {
                    "step_key": "translate_chunk",
                    "step_label": "分块翻译",
                    "done": done,
                    "total": total_chunks,
                    "unit": "chunk",
                    "percent_in_stage": max(0, min(100, percent_in_stage)),
                    "eta_seconds": eta_seconds,
                }
            )

    for idx, row in enumerate(rows):
        row["translation"] = translated_lines[idx]

    return rows, len(chunks), debug_chunks
