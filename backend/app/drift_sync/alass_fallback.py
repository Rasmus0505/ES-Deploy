from __future__ import annotations

import math


def _safe_ranges(items: list[tuple[float, float]]) -> list[tuple[float, float]]:
    rows: list[tuple[float, float]] = []
    for start, end in items or []:
        try:
            safe_start = float(start)
            safe_end = float(end)
        except Exception:
            continue
        if not math.isfinite(safe_start) or not math.isfinite(safe_end):
            continue
        if safe_end <= safe_start:
            continue
        rows.append((max(0.0, safe_start), max(0.0, safe_end)))
    return rows


def estimate_offset_scale_boundary(
    *,
    reference_ranges: list[tuple[float, float]],
    query_ranges: list[tuple[float, float]],
) -> dict:
    ref = _safe_ranges(reference_ranges)
    qry = _safe_ranges(query_ranges)
    if not ref or not qry:
        return {
            "ok": False,
            "offset_seconds": 0.0,
            "drift_scale": 1.0,
            "score": 0.0,
            "method": "alass_fallback",
            "reason": "empty_input",
        }

    ref_start = min(item[0] for item in ref)
    ref_end = max(item[1] for item in ref)
    qry_start = min(item[0] for item in qry)
    qry_end = max(item[1] for item in qry)

    qry_span = max(0.001, qry_end - qry_start)
    ref_span = max(0.001, ref_end - ref_start)
    drift_scale = max(0.90, min(1.10, ref_span / qry_span))
    offset_seconds = ref_start - (qry_start * drift_scale)

    # 边界误差反推一个简单置信分，范围 [0,1]。
    mapped_end = qry_end * drift_scale + offset_seconds
    err = abs(mapped_end - ref_end) + abs((qry_start * drift_scale + offset_seconds) - ref_start)
    score = max(0.0, min(1.0, 1.0 - (err / 2.5)))
    return {
        "ok": True,
        "offset_seconds": float(offset_seconds),
        "drift_scale": float(drift_scale),
        "score": float(score),
        "method": "alass_fallback",
    }
