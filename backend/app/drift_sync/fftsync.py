from __future__ import annotations

import math
from typing import Iterable

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None


def _safe_range(items: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    rows: list[tuple[float, float]] = []
    for start, end in items:
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


def _build_activity_array(ranges: list[tuple[float, float]], *, sample_rate_hz: int, total_seconds: float):
    if np is None:
        return None
    total_len = max(1, int(math.ceil(max(0.1, total_seconds) * sample_rate_hz)) + 1)
    series = np.zeros(total_len, dtype=np.float32)
    for start, end in ranges:
        s_idx = max(0, int(math.floor(start * sample_rate_hz)))
        e_idx = min(total_len, int(math.ceil(end * sample_rate_hz)))
        if e_idx <= s_idx:
            continue
        series[s_idx:e_idx] = 1.0
    return series


def _fft_cross_correlation(ref, query):
    n = len(ref) + len(query) - 1
    size = 1 << int(math.ceil(math.log2(max(2, n))))
    fr = np.fft.fft(ref, size)
    fq = np.fft.fft(query, size)
    corr = np.fft.ifft(fr * np.conj(fq)).real
    return np.concatenate((corr[-(len(query) - 1) :], corr[: len(ref)]))


def estimate_offset_scale_fft(
    *,
    reference_ranges: list[tuple[float, float]],
    query_ranges: list[tuple[float, float]],
    sample_rate_hz: int = 100,
    max_offset_seconds: float = 12.0,
) -> dict:
    safe_reference = _safe_range(reference_ranges)
    safe_query = _safe_range(query_ranges)
    if not safe_reference or not safe_query:
        return {
            "ok": False,
            "offset_seconds": 0.0,
            "drift_scale": 1.0,
            "score": 0.0,
            "method": "fftsync",
            "reason": "empty_input",
        }
    if np is None:
        return {
            "ok": False,
            "offset_seconds": 0.0,
            "drift_scale": 1.0,
            "score": 0.0,
            "method": "fftsync",
            "reason": "numpy_unavailable",
        }

    ref_last = max(item[1] for item in safe_reference)
    scale_candidates = [0.985, 0.99, 0.995, 1.0, 1.005, 1.01, 1.015]

    best = {
        "ok": False,
        "offset_seconds": 0.0,
        "drift_scale": 1.0,
        "score": -1.0,
        "method": "fftsync",
        "reason": "no_match",
    }
    for scale in scale_candidates:
        scaled_query = [(start * scale, end * scale) for start, end in safe_query]
        total_seconds = max(ref_last, max(item[1] for item in scaled_query)) + max_offset_seconds + 1.0
        ref_series = _build_activity_array(safe_reference, sample_rate_hz=sample_rate_hz, total_seconds=total_seconds)
        query_series = _build_activity_array(scaled_query, sample_rate_hz=sample_rate_hz, total_seconds=total_seconds)
        if ref_series is None or query_series is None:
            continue
        if not np.any(ref_series) or not np.any(query_series):
            continue

        corr = _fft_cross_correlation(ref_series, query_series)
        lags = np.arange(-(len(query_series) - 1), len(ref_series))
        safe_max_offset = int(max(0, round(max_offset_seconds * sample_rate_hz)))
        mask = np.abs(lags) <= safe_max_offset
        if not np.any(mask):
            continue

        masked_corr = corr[mask]
        masked_lags = lags[mask]
        if masked_corr.size == 0:
            continue
        local_idx = int(np.argmax(masked_corr))
        best_lag = int(masked_lags[local_idx])
        raw_score = float(masked_corr[local_idx])
        denom = float(np.linalg.norm(ref_series) * np.linalg.norm(query_series)) + 1e-6
        score = raw_score / denom
        if score > float(best["score"]):
            best = {
                "ok": True,
                "offset_seconds": float(best_lag) / float(sample_rate_hz),
                "drift_scale": float(scale),
                "score": float(score),
                "method": "fftsync",
            }

    if not bool(best["ok"]):
        best["score"] = 0.0
    else:
        best["score"] = max(0.0, min(1.0, float(best["score"])))
    return best
