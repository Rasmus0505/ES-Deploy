from __future__ import annotations

import bisect
import difflib
import json
import re
import time

from .types import FlowError, ProgressReporter


def remove_punctuation(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or ""))
    value = re.sub(r"[^\w\s]", "", value)
    return value.strip()


def _compact_text(value: str) -> str:
    return remove_punctuation(value.lower()).replace(" ", "")


def _tokenize_text(value: str) -> list[str]:
    normalized = remove_punctuation(value.lower())
    return [item for item in normalized.split(" ") if item]


def _to_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except Exception:
        return None
    if parsed != parsed:
        return None
    if parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _build_word_index(word_segments: list[dict]) -> tuple[str, list[int], list[dict]]:
    full_words = ""
    word_char_starts: list[int] = []
    words: list[dict] = []

    for item in word_segments or []:
        if not isinstance(item, dict):
            continue
        raw_word = str(item.get("word") or item.get("text") or "").strip()
        clean_word = _compact_text(raw_word)
        if not clean_word:
            continue
        start = _to_float(item.get("start"))
        end = _to_float(item.get("end"))
        if start is None or end is None or end <= start:
            continue

        words.append({"word": clean_word, "start": float(start), "end": float(end)})
        word_char_starts.append(len(full_words))
        full_words += clean_word

    return full_words, word_char_starts, words


def _char_pos_to_word_idx(word_char_starts: list[int], char_pos: int) -> int | None:
    if not word_char_starts:
        return None
    idx = bisect.bisect_right(word_char_starts, max(0, int(char_pos))) - 1
    if idx < 0 or idx >= len(word_char_starts):
        return None
    return idx


def _similarity_ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return float(difflib.SequenceMatcher(None, a, b).ratio())


def _find_fuzzy_match_window(
    *,
    sentence_tokens: list[str],
    words: list[dict],
    start_word_idx: int,
    search_window_words: int = 180,
) -> tuple[int, int, float] | None:
    if not sentence_tokens or not words:
        return None

    expected_len = max(1, len(sentence_tokens))
    target_compact = "".join(sentence_tokens)
    token_min_len = max(1, expected_len - 3)
    token_max_len = expected_len + 4

    safe_start = max(0, int(start_word_idx))
    window_start = max(0, safe_start - 3)
    window_end = min(len(words), safe_start + search_window_words)
    if window_end <= window_start:
        return None

    best_start = -1
    best_end = -1
    best_score = 0.0
    for candidate_start in range(window_start, window_end):
        for token_len in range(token_min_len, token_max_len + 1):
            candidate_end = candidate_start + token_len
            if candidate_end > window_end:
                break
            compact = "".join(str(item.get("word") or "") for item in words[candidate_start:candidate_end])
            score = _similarity_ratio(target_compact, compact)
            if score > best_score:
                best_score = score
                best_start = candidate_start
                best_end = candidate_end

    if best_start < 0 or best_end <= best_start:
        return None
    return best_start, best_end - 1, best_score


def _count_remaining_rows_and_tokens(rows: list[dict], start_index: int) -> tuple[int, int]:
    remaining_rows = 0
    total_tokens = 0
    safe_start = max(0, int(start_index))
    for row in (rows or [])[safe_start:]:
        text = str((row or {}).get("text") or "").strip()
        if not text:
            continue
        if not _compact_text(text):
            continue
        remaining_rows += 1
        total_tokens += max(1, len(_tokenize_text(text)))
    return remaining_rows, total_tokens


def align_rows_with_word_segments(
    *,
    rows: list[dict],
    word_segments: list[dict],
    stage: str,
    progress_reporter: ProgressReporter | None = None,
    return_diagnostics: bool = False,
    allow_word_stream_fallback: bool = False,
) -> list[dict] | tuple[list[dict], dict]:
    full_words, word_char_starts, words = _build_word_index(word_segments)
    if not full_words or not words:
        raise FlowError(
            stage,
            "timestamp_alignment_failed",
            "词级时间戳缺失，无法对齐字幕",
            detail=json.dumps({"reason": "word_segments_empty"}, ensure_ascii=False),
        )

    aligned: list[dict] = []
    alignment_scores: list[float] = []
    exact_match_rows = 0
    fuzzy_match_rows = 0
    fallback_rows = 0
    alignment_mode = "strict"
    current_pos = 0
    current_word_idx = 0
    total_rows = max(1, len(rows or []))
    started_at = time.monotonic()
    for sentence_index, row in enumerate(rows or []):
        text = str((row or {}).get("text") or "").strip()
        translation = str((row or {}).get("translation") or "").strip()
        if not text:
            continue

        clean_sentence = _compact_text(text)
        sentence_len = len(clean_sentence)
        if sentence_len == 0:
            continue

        match_found = False
        row_score = 0.0
        exact_pos = full_words.find(clean_sentence, current_pos)
        if exact_pos >= 0:
            start_idx = _char_pos_to_word_idx(word_char_starts, exact_pos)
            end_idx = _char_pos_to_word_idx(word_char_starts, exact_pos + sentence_len - 1)
            if start_idx is not None and end_idx is not None and end_idx >= start_idx:
                start = float(words[start_idx]["start"])
                end = float(words[end_idx]["end"])
                if end < start:
                    end = start
                aligned.append(
                    {
                        "text": text,
                        "translation": translation,
                        "start": round(start, 3),
                        "end": round(end, 3),
                    }
                )
                current_pos = word_char_starts[end_idx] + len(words[end_idx]["word"])
                current_word_idx = end_idx + 1
                match_found = True
                row_score = 1.0
                exact_match_rows += 1

        if not match_found:
            sentence_tokens = _tokenize_text(text)
            fuzzy = _find_fuzzy_match_window(
                sentence_tokens=sentence_tokens,
                words=words,
                start_word_idx=current_word_idx,
            )
            if fuzzy:
                start_idx, end_idx, fuzzy_score = fuzzy
                min_accept = 0.70 if len(sentence_tokens) >= 3 else 0.78
                if fuzzy_score >= min_accept:
                    start = float(words[start_idx]["start"])
                    end = float(words[end_idx]["end"])
                    if end < start:
                        end = start
                    aligned.append(
                        {
                            "text": text,
                            "translation": translation,
                            "start": round(start, 3),
                            "end": round(end, 3),
                        }
                    )
                    current_pos = word_char_starts[end_idx] + len(words[end_idx]["word"])
                    current_word_idx = end_idx + 1
                    match_found = True
                    row_score = float(round(fuzzy_score, 4))
                    fuzzy_match_rows += 1

        if not match_found and allow_word_stream_fallback:
            remaining_words = len(words) - current_word_idx
            remaining_rows, remaining_tokens = _count_remaining_rows_and_tokens(rows, sentence_index)
            token_count = max(1, len(_tokenize_text(text)))
            if remaining_words > 0 and remaining_rows > 0 and remaining_tokens > 0:
                proportional_words = int(round((remaining_words * token_count) / remaining_tokens))
                reserve_for_future = max(0, remaining_rows - 1)
                max_words_for_current = max(1, remaining_words - reserve_for_future)
                allocated_words = max(1, proportional_words)
                allocated_words = min(max_words_for_current, allocated_words)
                start_idx = current_word_idx
                end_idx = min(len(words) - 1, start_idx + allocated_words - 1)
                if end_idx >= start_idx:
                    start = float(words[start_idx]["start"])
                    end = float(words[end_idx]["end"])
                    if end < start:
                        end = start
                    aligned.append(
                        {
                            "text": text,
                            "translation": translation,
                            "start": round(start, 3),
                            "end": round(end, 3),
                        }
                    )
                    current_pos = word_char_starts[end_idx] + len(words[end_idx]["word"])
                    current_word_idx = end_idx + 1
                    match_found = True
                    row_score = 0.35
                    fallback_rows += 1
                    alignment_mode = "qwen_word_stream_fallback"

        if not match_found:
            context_start = max(0, current_pos - 30)
            context_end = min(len(full_words), current_pos + sentence_len + 30)
            raise FlowError(
                stage,
                "timestamp_alignment_failed",
                "词级时间戳对齐失败",
                detail=json.dumps(
                    {
                        "sentence_index": sentence_index,
                        "sentence": text,
                        "normalized_sentence": clean_sentence,
                        "search_position": current_pos,
                        "context": full_words[context_start:context_end],
                        "aligned_rows": len(aligned),
                        "exact_match_rows": exact_match_rows,
                        "fuzzy_match_rows": fuzzy_match_rows,
                        "fallback_rows": fallback_rows,
                        "allow_word_stream_fallback": bool(allow_word_stream_fallback),
                    },
                    ensure_ascii=False,
                ),
            )
        alignment_scores.append(row_score)

        if progress_reporter:
            done = sentence_index + 1
            percent_in_stage = int(round((done / total_rows) * 100))
            elapsed = max(0.0, time.monotonic() - started_at)
            eta_seconds = None
            if done > 0 and done < total_rows and elapsed > 0:
                eta_seconds = int(round((elapsed / done) * (total_rows - done)))
            progress_reporter(
                {
                    "step_key": "align_rows",
                    "step_label": "时间戳对齐",
                    "done": done,
                    "total": total_rows,
                    "unit": "row",
                    "percent_in_stage": max(0, min(100, percent_in_stage)),
                    "eta_seconds": eta_seconds,
                }
            )

    for idx in range(len(aligned) - 1):
        current = aligned[idx]
        following = aligned[idx + 1]
        gap = float(following["start"]) - float(current["end"])
        if 0 < gap < 1:
            current["end"] = round(float(following["start"]), 3)
        if float(current["end"]) < float(current["start"]):
            current["end"] = round(float(current["start"]), 3)

    diagnostics = {
        "alignment_quality_score": round(
            (sum(alignment_scores) / len(alignment_scores)) if alignment_scores else 0.0,
            4,
        ),
        "aligned_rows": len(aligned),
        "total_rows": len(rows or []),
        "exact_match_rows": exact_match_rows,
        "fuzzy_match_rows": fuzzy_match_rows,
        "fallback_rows": fallback_rows,
        "fallback_ratio": round((fallback_rows / max(1, len(rows or []))), 4),
        "alignment_mode": alignment_mode,
    }
    if return_diagnostics:
        return aligned, diagnostics
    return aligned
