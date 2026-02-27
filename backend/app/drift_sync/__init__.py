from __future__ import annotations

from typing import Any

from .alass_fallback import estimate_offset_scale_boundary
from .fftsync import estimate_offset_scale_fft


def _collect_ranges(rows: list[dict]) -> list[tuple[float, float]]:
    ranges: list[tuple[float, float]] = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start") or 0.0)
            end = float(item.get("end") or 0.0)
        except Exception:
            continue
        if end <= start:
            continue
        ranges.append((max(0.0, start), max(0.0, end)))
    return ranges


def _apply_transform(rows: list[dict], *, offset_seconds: float, drift_scale: float) -> list[dict]:
    corrected: list[dict] = []
    prev_end = 0.0
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        next_item = dict(item)
        start = float(item.get("start") or 0.0) * drift_scale + offset_seconds
        end = float(item.get("end") or 0.0) * drift_scale + offset_seconds
        start = max(0.0, start)
        end = max(start, end)
        if start < prev_end:
            start = prev_end
        if end < start:
            end = start
        next_item["start"] = round(start, 3)
        next_item["end"] = round(end, 3)
        corrected.append(next_item)
        prev_end = float(next_item["end"])
    return corrected


def _boundary_gaps(
    *,
    reference_ranges: list[tuple[float, float]],
    query_ranges: list[tuple[float, float]],
) -> tuple[float, float]:
    if not reference_ranges or not query_ranges:
        return 0.0, 0.0
    first_ref = min(item[0] for item in reference_ranges)
    last_ref = max(item[1] for item in reference_ranges)
    first_query = min(item[0] for item in query_ranges)
    last_query = max(item[1] for item in query_ranges)
    return first_query - first_ref, last_query - last_ref


def apply_adaptive_drift_correction(
    *,
    sentences: list[dict],
    word_segments: list[dict],
    alignment_quality_score: float | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    sentence_ranges = _collect_ranges(sentences)
    word_ranges = _collect_ranges(word_segments)
    start_gap_before, end_gap_before = _boundary_gaps(reference_ranges=word_ranges, query_ranges=sentence_ranges)
    quality = float(alignment_quality_score or 0.0)
    should_trigger = (
        abs(start_gap_before) >= 0.12
        or abs(end_gap_before) >= 0.18
        or quality < 0.92
    )
    diagnostics: dict[str, Any] = {
        "triggered": bool(should_trigger),
        "correction_applied": False,
        "correction_method": "none",
        "global_offset_ms": 0,
        "drift_scale": 1.0,
        "correction_score": 0.0,
        "boundary_start_gap_before": round(start_gap_before, 4),
        "boundary_end_gap_before": round(end_gap_before, 4),
        "boundary_start_gap_after": round(start_gap_before, 4),
        "boundary_end_gap_after": round(end_gap_before, 4),
    }
    if not should_trigger or not sentence_ranges or not word_ranges:
        return sentences, diagnostics

    fft_result = estimate_offset_scale_fft(reference_ranges=word_ranges, query_ranges=sentence_ranges)
    chosen = fft_result
    if (not bool(fft_result.get("ok"))) or float(fft_result.get("score") or 0.0) < 0.35:
        chosen = estimate_offset_scale_boundary(reference_ranges=word_ranges, query_ranges=sentence_ranges)

    if not bool(chosen.get("ok")):
        return sentences, diagnostics

    offset_seconds = float(chosen.get("offset_seconds") or 0.0)
    drift_scale = float(chosen.get("drift_scale") or 1.0)
    small_adjust = abs(offset_seconds) < 0.08 and abs(drift_scale - 1.0) < 0.002
    diagnostics.update(
        {
            "correction_method": str(chosen.get("method") or "none"),
            "global_offset_ms": int(round(offset_seconds * 1000)),
            "drift_scale": round(drift_scale, 6),
            "correction_score": round(float(chosen.get("score") or 0.0), 4),
        }
    )
    if small_adjust:
        return sentences, diagnostics

    corrected = _apply_transform(sentences, offset_seconds=offset_seconds, drift_scale=drift_scale)
    corrected_ranges = _collect_ranges(corrected)
    start_gap_after, end_gap_after = _boundary_gaps(reference_ranges=word_ranges, query_ranges=corrected_ranges)
    diagnostics.update(
        {
            "correction_applied": True,
            "boundary_start_gap_after": round(start_gap_after, 4),
            "boundary_end_gap_after": round(end_gap_after, 4),
        }
    )
    return corrected, diagnostics


__all__ = ["apply_adaptive_drift_correction"]
