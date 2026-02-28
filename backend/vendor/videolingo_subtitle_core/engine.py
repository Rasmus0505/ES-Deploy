from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import requests
from openai import OpenAI

from .vl_flow import align_rows_with_word_segments
from .vl_flow.types import FlowError


ProgressCallback = Callable[[int, str, str, dict[str, Any] | None], None]
CancelCheck = Callable[[], bool]


class PipelineError(RuntimeError):
    def __init__(self, stage: str, code: str, message: str, detail: str | None = None):
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.message = message
        self.detail = detail or ""

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
        }


def _raise_if_cancel_requested(should_cancel: CancelCheck | None) -> None:
    if callable(should_cancel) and bool(should_cancel()):
        raise PipelineError("cancelled", "cancel_requested", "任务取消请求已接收，已停止后续处理")


@dataclass
class LlmOptions:
    base_url: str
    api_key: str
    model: str
    llm_support_json: bool = False


@dataclass
class WhisperOptions:
    runtime: str
    model: str
    language: str
    base_url: str = "https://dashscope.aliyuncs.com"
    api_key: str = ""


@dataclass
class PipelineOptions:
    enable_demucs: bool
    asr_profile: str
    asr_fallback_enabled: bool
    asr_allow_cloud_fallback: bool
    asr_allow_local_fallback: bool
    enable_diarization: bool
    source_language: str
    target_language: str
    llm: LlmOptions
    whisper: WhisperOptions

    @staticmethod
    def from_dict(payload: dict) -> "PipelineOptions":
        llm_data = payload.get("llm") or {}
        whisper_data = payload.get("whisper") or {}
        whisper_runtime = str(whisper_data.get("runtime") or "cloud").strip() or "cloud"
        default_whisper_model = "small" if whisper_runtime.lower() == "local" else "paraformer-v2"
        raw_asr_profile = str(payload.get("asr_profile") or "balanced").strip().lower() or "balanced"
        asr_profile = raw_asr_profile if raw_asr_profile in {"fast", "balanced", "accurate"} else "balanced"
        return PipelineOptions(
            enable_demucs=bool(payload.get("enable_demucs", False)),
            asr_profile=asr_profile,
            asr_fallback_enabled=bool(payload.get("asr_fallback_enabled", True)),
            asr_allow_cloud_fallback=bool(payload.get("asr_allow_cloud_fallback", True)),
            asr_allow_local_fallback=bool(payload.get("asr_allow_local_fallback", True)),
            enable_diarization=bool(payload.get("enable_diarization", False)),
            source_language=str(payload.get("source_language") or "en"),
            target_language=str(payload.get("target_language") or "zh"),
            llm=LlmOptions(
                base_url=str(llm_data.get("base_url") or "https://api.siliconflow.cn/v1"),
                api_key=str(llm_data.get("api_key") or ""),
                model=str(llm_data.get("model") or "tencent/Hunyuan-MT-7B"),
                llm_support_json=bool(llm_data.get("llm_support_json", False)),
            ),
            whisper=WhisperOptions(
                runtime=whisper_runtime,
                model=str(whisper_data.get("model") or default_whisper_model),
                language=str(whisper_data.get("language") or payload.get("source_language") or "en"),
                base_url=str(whisper_data.get("base_url") or "https://dashscope.aliyuncs.com"),
                api_key=str(whisper_data.get("api_key") or ""),
            ),
        )


@dataclass
class AsrDispatchResult:
    segments: list[dict]
    provider_effective: str
    attempts: list[str]
    fallback_used: bool
    runtime_effective: str
    model_effective: str


_CACHE_LOCK = threading.RLock()
_LLM_USAGE_LOCAL = threading.local()
_FFMPEG_READY = False
_LLM_PROBE_TTL_SECONDS = 600
_LLM_PROBE_CACHE_MAX = 64
_LLM_PROBE_CACHE: "OrderedDict[str, float]" = OrderedDict()
_FASTER_WHISPER_MODEL_CACHE_MAX = 2
_WHISPERX_ASR_MODEL_CACHE_MAX = 1
_WHISPERX_ALIGN_MODEL_CACHE_MAX = 2
_SUBTITLE_MAX_LENGTH = 75
_SUBTITLE_TARGET_MULTIPLIER = 1.2
_CLOUD_ASR_MODEL = "paraformer-v2"
_CLOUD_QWEN_ASR_MODEL = "qwen3-asr-flash-filetrans"
_CLOUD_ASR_PROVIDER = "cloud_paraformer_v2"
_CLOUD_QWEN_ASR_PROVIDER = "cloud_qwen3_asr_flash_filetrans"
_QWEN_FALLBACK_RATIO_THRESHOLD = 0.10
_CLOUD_ASR_MODEL_ALIASES: dict[str, str] = {
    _CLOUD_ASR_MODEL: _CLOUD_ASR_MODEL,
    _CLOUD_QWEN_ASR_MODEL: _CLOUD_QWEN_ASR_MODEL,
}
_CLOUD_ASR_PROVIDER_BY_MODEL: dict[str, str] = {
    _CLOUD_ASR_MODEL: _CLOUD_ASR_PROVIDER,
    _CLOUD_QWEN_ASR_MODEL: _CLOUD_QWEN_ASR_PROVIDER,
}
_FASTER_WHISPER_MODEL_CACHE: "OrderedDict[tuple[str, str, str, int], Any]" = OrderedDict()
_WHISPERX_ASR_MODEL_CACHE: "OrderedDict[tuple[str, str, str, str], Any]" = OrderedDict()
_WHISPERX_ALIGN_MODEL_CACHE: "OrderedDict[tuple[str, str], tuple[Any, Any]]" = OrderedDict()


def _resolve_cloud_asr_model(requested_model: str) -> str:
    normalized = str(requested_model or "").strip().lower()
    if normalized in _CLOUD_ASR_MODEL_ALIASES:
        return _CLOUD_ASR_MODEL_ALIASES[normalized]
    return _CLOUD_ASR_MODEL


def _resolve_cloud_asr_provider(requested_model: str) -> str:
    model = _resolve_cloud_asr_model(requested_model)
    return _CLOUD_ASR_PROVIDER_BY_MODEL.get(model, _CLOUD_ASR_PROVIDER)


def _new_timing_ms() -> dict[str, int]:
    return {
        "extract_audio": 0,
        "asr": 0,
        "llm_translate": 0,
        "align_timestamps": 0,
        "align_and_build": 0,
        "total": 0,
    }


def _measure_started_at() -> float:
    return time.perf_counter()


def _measure_elapsed_ms(started_at: float) -> int:
    return max(0, int(round((time.perf_counter() - started_at) * 1000)))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_positive_int(value: Any) -> int:
    parsed = _safe_int(value, 0)
    return parsed if parsed > 0 else 0


def _infer_llm_provider(base_url: str) -> str:
    normalized = _normalize_base_url(base_url)
    try:
        host = str(urlparse(normalized).netloc or "").lower().strip()
    except Exception:
        host = ""
    if not host:
        return ""
    if "dashscope.aliyuncs.com" in host:
        return "dashscope"
    if "openai.com" in host:
        return "openai"
    if "openrouter.ai" in host:
        return "openrouter"
    if "siliconflow.cn" in host:
        return "siliconflow"
    return host.replace(".", "_")


def _start_llm_usage_collection(opts: LlmOptions) -> None:
    base_url = _normalize_base_url(opts.base_url)
    tracker = {
        "llm_base_url": base_url,
        "llm_provider_effective": _infer_llm_provider(base_url),
        "llm_model_effective": str(opts.model or "").strip(),
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "llm_request_count": 0,
        "provider_request_id": "",
    }
    setattr(_LLM_USAGE_LOCAL, "tracker", tracker)


def _append_llm_usage_sample(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    provider_request_id: str = "",
) -> None:
    tracker = getattr(_LLM_USAGE_LOCAL, "tracker", None)
    if not isinstance(tracker, dict):
        return
    safe_prompt_tokens = _safe_positive_int(prompt_tokens)
    safe_completion_tokens = _safe_positive_int(completion_tokens)
    safe_total_tokens = _safe_positive_int(total_tokens)
    if safe_total_tokens <= 0:
        safe_total_tokens = safe_prompt_tokens + safe_completion_tokens
    tracker["prompt_tokens"] = _safe_positive_int(tracker.get("prompt_tokens")) + safe_prompt_tokens
    tracker["completion_tokens"] = _safe_positive_int(tracker.get("completion_tokens")) + safe_completion_tokens
    tracker["total_tokens"] = _safe_positive_int(tracker.get("total_tokens")) + safe_total_tokens
    tracker["llm_request_count"] = _safe_positive_int(tracker.get("llm_request_count")) + 1
    safe_provider_request_id = str(provider_request_id or "").strip()
    if safe_provider_request_id:
        tracker["provider_request_id"] = safe_provider_request_id


def _get_llm_usage_snapshot() -> dict[str, Any]:
    tracker = getattr(_LLM_USAGE_LOCAL, "tracker", None)
    if not isinstance(tracker, dict):
        return {
            "llm_base_url": "",
            "llm_provider_effective": "",
            "llm_model_effective": "",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "llm_request_count": 0,
            "provider_request_id": "",
        }
    snapshot = dict(tracker)
    prompt_tokens = _safe_positive_int(snapshot.get("prompt_tokens"))
    completion_tokens = _safe_positive_int(snapshot.get("completion_tokens"))
    total_tokens = _safe_positive_int(snapshot.get("total_tokens"))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    snapshot["prompt_tokens"] = prompt_tokens
    snapshot["completion_tokens"] = completion_tokens
    snapshot["total_tokens"] = total_tokens
    snapshot["llm_request_count"] = _safe_positive_int(snapshot.get("llm_request_count"))
    snapshot["provider_request_id"] = str(snapshot.get("provider_request_id") or "").strip()
    snapshot["llm_base_url"] = str(snapshot.get("llm_base_url") or "").strip()
    snapshot["llm_provider_effective"] = str(snapshot.get("llm_provider_effective") or "").strip()
    snapshot["llm_model_effective"] = str(snapshot.get("llm_model_effective") or "").strip()
    return snapshot


def _extract_usage_from_response_payload(payload: dict[str, Any]) -> tuple[int, int, int, str]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt_tokens = _safe_positive_int(usage.get("input_tokens") or usage.get("prompt_tokens"))
    completion_tokens = _safe_positive_int(usage.get("output_tokens") or usage.get("completion_tokens"))
    total_tokens = _safe_positive_int(usage.get("total_tokens"))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    provider_request_id = str(payload.get("id") or "").strip()
    return prompt_tokens, completion_tokens, total_tokens, provider_request_id


def _extract_usage_from_chat_response(resp: Any) -> tuple[int, int, int, str]:
    usage_obj = getattr(resp, "usage", None)
    if isinstance(usage_obj, dict):
        usage = usage_obj
    else:
        usage = {
            "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0),
            "completion_tokens": getattr(usage_obj, "completion_tokens", 0),
            "total_tokens": getattr(usage_obj, "total_tokens", 0),
        }
    prompt_tokens = _safe_positive_int(usage.get("prompt_tokens"))
    completion_tokens = _safe_positive_int(usage.get("completion_tokens"))
    total_tokens = _safe_positive_int(usage.get("total_tokens"))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    provider_request_id = str(getattr(resp, "id", "") or "").strip()
    return prompt_tokens, completion_tokens, total_tokens, provider_request_id


def _clamp_percent(value: Any) -> int:
    return max(0, min(100, _safe_int(value, 0)))


def _normalize_progress_detail(detail: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(detail, dict):
        return None
    normalized: dict[str, Any] = {}
    for key in ("step_key", "step_label", "unit"):
        raw = str(detail.get(key) or "").strip()
        if raw:
            normalized[key] = raw
    for key in ("done", "total", "percent_in_stage", "eta_seconds"):
        if key in detail and detail.get(key) is not None:
            normalized[key] = _safe_int(detail.get(key), 0)
    return normalized or None


def _emit_progress(
    progress: ProgressCallback,
    percent: int,
    stage: str,
    message: str,
    detail: dict[str, Any] | None = None,
) -> None:
    safe_stage = str(stage or "").strip() or "running"
    safe_message = str(message or "").strip() or "处理中"
    progress(_clamp_percent(percent), safe_stage, safe_message, _normalize_progress_detail(detail))


def _map_stage_percent(stage_start: int, stage_end: int, percent_in_stage: Any) -> int:
    safe_start = _clamp_percent(stage_start)
    safe_end = max(safe_start, _clamp_percent(stage_end))
    stage_percent = _clamp_percent(percent_in_stage)
    span = max(0, safe_end - safe_start)
    return safe_start + int(round(span * (stage_percent / 100)))


def _build_detail_message(fallback_message: str, detail: dict[str, Any] | None) -> str:
    safe_fallback = str(fallback_message or "").strip() or "处理中"
    if not isinstance(detail, dict):
        return safe_fallback
    step_label = str(detail.get("step_label") or "").strip()
    done = max(0, _safe_int(detail.get("done"), 0))
    total = max(0, _safe_int(detail.get("total"), 0))
    unit = str(detail.get("unit") or "").strip()
    if not step_label:
        return safe_fallback
    if total > 0:
        if unit:
            return f"{step_label} {done}/{total}{unit}"
        return f"{step_label} {done}/{total}"
    return step_label


def _emit_stage_detail_progress(
    progress: ProgressCallback,
    *,
    stage: str,
    stage_start: int,
    stage_end: int,
    fallback_message: str,
    detail: dict[str, Any] | None,
) -> None:
    safe_detail = _normalize_progress_detail(detail)
    mapped_percent = _map_stage_percent(stage_start, stage_end, (safe_detail or {}).get("percent_in_stage", 0))
    _emit_progress(
        progress,
        mapped_percent,
        stage,
        _build_detail_message(fallback_message, safe_detail),
        safe_detail,
    )


def _cache_get(cache: OrderedDict, key: Any) -> Any | None:
    with _CACHE_LOCK:
        if key not in cache:
            return None
        cache.move_to_end(key)
        return cache[key]


def _cache_set(cache: OrderedDict, key: Any, value: Any, limit: int) -> Any:
    with _CACHE_LOCK:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > max(1, int(limit)):
            cache.popitem(last=False)
        return value


def resolve_whisper_runtime_models(whisper: WhisperOptions) -> tuple[str, str, str]:
    runtime = (whisper.runtime or "cloud").strip().lower() or "cloud"
    default_model = "small" if runtime == "local" else _CLOUD_ASR_MODEL
    requested_model = (whisper.model or default_model).strip() or default_model
    requested_model_key = requested_model.lower()
    effective_model = requested_model if runtime == "local" else _resolve_cloud_asr_model(requested_model)
    if runtime == "local" and requested_model_key in {
        _CLOUD_ASR_MODEL,
        _CLOUD_QWEN_ASR_MODEL,
        "distil-large-v2",
        "large-v3-turbo",
        "whisper-large-v3-turbo",
        "whisper-large-v3",
        "whisper-1",
        "whisperx",
    }:
        raise PipelineError(
            "asr",
            "invalid_whisper_model",
            f"local 模式不支持 Whisper 模型：{requested_model}",
            detail="请切换为 cloud 运行或选择 tiny/small/medium/large-v3 等本地可用模型。",
        )
    return runtime, requested_model, effective_model


def _pipeline_error_from_flow(exc: FlowError) -> PipelineError:
    return PipelineError(
        stage=str(exc.stage or "pipeline"),
        code=str(exc.code or "flow_error"),
        message=str(exc.message or "流水线执行失败"),
        detail=str(exc.detail or ""),
    )


def _apply_adaptive_drift_sync(
    *,
    sentences: list[dict],
    word_segments: list[dict],
    alignment_quality_score: float | None,
) -> tuple[list[dict], dict[str, Any]]:
    default = {
        "triggered": False,
        "correction_applied": False,
        "correction_method": "none",
        "global_offset_ms": 0,
        "drift_scale": 1.0,
        "correction_score": 0.0,
    }
    try:
        from app.drift_sync import apply_adaptive_drift_correction
    except Exception:
        return sentences, default
    try:
        corrected, diagnostics = apply_adaptive_drift_correction(
            sentences=sentences,
            word_segments=word_segments,
            alignment_quality_score=alignment_quality_score,
        )
        if not isinstance(diagnostics, dict):
            diagnostics = {}
        normalized = dict(default)
        normalized.update(diagnostics)
        return corrected, normalized
    except Exception:
        return sentences, default


def _transcribe_cloud_asr(audio_path: str, whisper: WhisperOptions) -> tuple[list[dict], str, str]:
    requested_model = (whisper.model or "").strip()
    effective_model = _resolve_cloud_asr_model(requested_model)
    if effective_model == _CLOUD_QWEN_ASR_MODEL:
        segments = _transcribe_qwen3_asr_flash_filetrans(audio_path, whisper)
    else:
        segments = _transcribe_paraformer_v2(audio_path, whisper)
    provider_effective = _resolve_cloud_asr_provider(effective_model)
    return segments, provider_effective, effective_model


def _dispatch_asr_videolingo(
    *,
    audio_path: str,
    whisper: WhisperOptions,
    enable_diarization: bool,
    asr_progress: Callable[[int, str], None] | None = None,
) -> AsrDispatchResult:
    runtime = (whisper.runtime or "cloud").strip().lower() or "cloud"
    if runtime == "cloud":
        requested_model = (whisper.model or "").strip()
        cloud_model = _resolve_cloud_asr_model(requested_model)
        if asr_progress:
            cloud_label = "Qwen3-FileTrans" if cloud_model == _CLOUD_QWEN_ASR_MODEL else "Paraformer"
            asr_progress(30, f"正在使用云端 {cloud_label} 识别")
        segments, provider_effective, model_effective = _transcribe_cloud_asr(audio_path, whisper)
        return AsrDispatchResult(
            segments=segments,
            provider_effective=provider_effective,
            attempts=[provider_effective],
            fallback_used=False,
            runtime_effective="cloud",
            model_effective=model_effective,
        )

    if runtime == "local":
        if asr_progress:
            asr_progress(30, "正在使用本地 WhisperX 识别")
        segments, model_effective = _transcribe_local_whisperx(
            audio_path,
            whisper,
            enable_diarization=enable_diarization,
            asr_progress=asr_progress,
        )
        return AsrDispatchResult(
            segments=segments,
            provider_effective="local_whisperx",
            attempts=["local_whisperx"],
            fallback_used=False,
            runtime_effective="local",
            model_effective=model_effective,
        )

    raise PipelineError("asr", "invalid_runtime", f"不支持的 whisper.runtime: {runtime}")


def run_pipeline(
    video_path: str,
    work_dir: str,
    options_payload: dict,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> dict:
    progress = progress or (lambda percent, stage, message, detail=None: None)
    pipeline_started_at = _measure_started_at()
    timing_ms = _new_timing_ms()
    translation_batch_count = 0
    options = PipelineOptions.from_dict(options_payload or {})
    _start_llm_usage_collection(options.llm)
    whisper_runtime, whisper_model_requested, whisper_model_effective = resolve_whisper_runtime_models(options.whisper)
    video = Path(video_path)
    root = Path(work_dir)
    log_dir = root / "log"
    out_dir = root / "output"
    audio_dir = root / "audio"
    log_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    stage_started_at = _measure_started_at()
    _raise_if_cancel_requested(should_cancel)
    _ensure_ffmpeg_available()
    audio_path = str(audio_dir / "raw.wav")
    _emit_progress(progress, 8, "extract_audio", "正在从视频提取音频")
    _raise_if_cancel_requested(should_cancel)
    _extract_audio(video, Path(audio_path))
    _raise_if_cancel_requested(should_cancel)
    timing_ms["extract_audio"] += _measure_elapsed_ms(stage_started_at)

    stage_started_at = _measure_started_at()
    _raise_if_cancel_requested(should_cancel)
    _emit_progress(progress, 30, "asr", "正在执行语音识别")
    asr_provider_effective = ""
    asr_provider_attempts: list[str] = []
    dispatch_result = _dispatch_asr_videolingo(
        audio_path=audio_path,
        whisper=options.whisper,
        enable_diarization=options.enable_diarization,
        asr_progress=lambda p, m: _emit_progress(progress, p, "asr", m),
    )
    segments = dispatch_result.segments
    asr_provider_effective = dispatch_result.provider_effective
    asr_provider_attempts = list(dispatch_result.attempts)
    if dispatch_result.runtime_effective:
        whisper_runtime = dispatch_result.runtime_effective
    if dispatch_result.model_effective:
        whisper_model_effective = dispatch_result.model_effective
    _raise_if_cancel_requested(should_cancel)
    if not segments:
        raise PipelineError("asr", "asr_empty_segments", "语音识别未返回有效片段")
    runtime_source = _provider_runtime_source(asr_provider_effective)
    word_segments = _flatten_word_segments(segments, source=runtime_source)
    if not word_segments:
        message = "云端 ASR 未返回词级时间戳，无法继续对齐字幕"
        if runtime_source != "cloud":
            message = "ASR 未返回词级时间戳，无法继续对齐字幕"
        raise PipelineError(
            "asr",
            "word_timestamps_missing",
            message,
            detail=json.dumps(
                {
                    "provider": asr_provider_effective,
                    "runtime": runtime_source,
                    "reason": "word_segments_empty",
                },
                ensure_ascii=False,
            ),
        )
    timing_ms["asr"] += _measure_elapsed_ms(stage_started_at)
    _emit_progress(progress, 42, "asr", f"语音识别完成，共 {len(segments)} 段，正在进入直译")
    _save_json(
        log_dir / "asr_segments.json",
        {
            "segments": segments,
            "provider_effective": asr_provider_effective,
            "provider_attempts": asr_provider_attempts,
            "fallback_used": False,
        },
    )
    _save_json(log_dir / "word_segments.json", {"word_segments": word_segments})

    alignment_diagnostics: dict[str, Any] = {}
    drift_diagnostics: dict[str, Any] = {}
    stage_started_at = _measure_started_at()
    _raise_if_cancel_requested(should_cancel)
    _emit_progress(progress, 72, "llm_translate", "正在执行 LLM 直译")
    sentences: list[dict] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(segment.get("start") or 0.0)
        except Exception:
            start = 0.0
        try:
            end = float(segment.get("end") or 0.0)
        except Exception:
            end = start + 0.8
        if end <= start:
            end = start + 0.5
        sentences.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
                "translation": "",
            }
        )
    if not sentences:
        raise PipelineError("llm_translate", "asr_text_empty", "语音识别结果缺少可翻译文本")

    source_texts = [str(line.get("text") or "").strip() for line in sentences]
    translations, translation_batch_count = _translate_sentences(
        texts=source_texts,
        source_language=options.source_language,
        target_language=options.target_language,
        llm_opts=options.llm,
        progress_callback=(
            lambda done, total: _emit_stage_detail_progress(
                progress,
                stage="llm_translate",
                stage_start=72,
                stage_end=90,
                fallback_message="正在执行 LLM 直译",
                detail={
                    "step_key": "llm_translate",
                    "step_label": "LLM 直译",
                    "done": done,
                    "total": total,
                    "unit": "row",
                    "percent_in_stage": int(round((done / max(1, total)) * 100)),
                },
            )
        ),
        should_cancel=should_cancel,
    )
    for index, translation in enumerate(translations):
        sentences[index]["translation"] = str(translation or "").strip()
    timing_ms["llm_translate"] += _measure_elapsed_ms(stage_started_at)
    _emit_progress(progress, 90, "llm_translate", f"直译完成，共 {len(sentences)} 句")

    stage_started_at = _measure_started_at()
    _raise_if_cancel_requested(should_cancel)
    _emit_progress(progress, 92, "align_and_build", "正在对齐并构建字幕")
    allow_qwen_word_stream_fallback = asr_provider_effective == _CLOUD_QWEN_ASR_PROVIDER
    try:
        sentences, alignment_diagnostics = align_rows_with_word_segments(
            rows=sentences,
            word_segments=word_segments,
            stage="align_and_build",
            progress_reporter=(
                lambda detail: _emit_stage_detail_progress(
                    progress,
                    stage="align_and_build",
                    stage_start=92,
                    stage_end=96,
                    fallback_message="正在对齐并构建字幕",
                    detail=detail,
                )
            ),
            return_diagnostics=True,
            allow_word_stream_fallback=allow_qwen_word_stream_fallback,
        )
    except FlowError as exc:
        raise _pipeline_error_from_flow(exc) from exc
    fallback_rows = _safe_positive_int(alignment_diagnostics.get("fallback_rows"))
    fallback_ratio = float(alignment_diagnostics.get("fallback_ratio") or 0.0)
    if allow_qwen_word_stream_fallback:
        print(
            "[DEBUG] Qwen ASR alignment diagnostics "
            f"mode={alignment_diagnostics.get('alignment_mode') or 'strict'} "
            f"fallback_rows={fallback_rows} "
            f"fallback_ratio={round(fallback_ratio, 4)}"
        )
        if fallback_ratio > _QWEN_FALLBACK_RATIO_THRESHOLD:
            print(
                "[DEBUG] Qwen ASR alignment quality gate triggered "
                f"fallback_ratio={round(fallback_ratio, 4)} "
                f"threshold={_QWEN_FALLBACK_RATIO_THRESHOLD}"
            )
            raise PipelineError(
                "align_and_build",
                "timestamp_alignment_failed",
                "词级时间戳对齐质量未达标",
                detail=json.dumps(
                    {
                        "reason": "fallback_ratio_exceeded",
                        "fallback_ratio": round(fallback_ratio, 4),
                        "threshold": _QWEN_FALLBACK_RATIO_THRESHOLD,
                        "fallback_rows": fallback_rows,
                        "total_rows": _safe_positive_int(alignment_diagnostics.get("total_rows")),
                        "provider": asr_provider_effective,
                    },
                    ensure_ascii=False,
                ),
            )
    timing_ms["align_timestamps"] += _measure_elapsed_ms(stage_started_at)
    sentences, drift_diagnostics = _apply_adaptive_drift_sync(
        sentences=sentences,
        word_segments=word_segments,
        alignment_quality_score=float(alignment_diagnostics.get("alignment_quality_score") or 0.0),
    )
    sentences = _normalize_sentence_timeline(sentences)

    _raise_if_cancel_requested(should_cancel)
    _emit_progress(progress, 97, "align_and_build", "正在生成字幕文件")
    source_srt = _build_srt(sentences, include_translation=False)
    bilingual_srt = _build_srt(sentences, include_translation=True)
    (out_dir / "src.srt").write_text(source_srt, encoding="utf-8")
    (out_dir / "src_trans.srt").write_text(bilingual_srt, encoding="utf-8")

    subtitles = []
    for index, line in enumerate(sentences):
        subtitles.append(
            {
                "id": index + 1,
                "start": round(float(line["start"]), 3),
                "end": round(float(line["end"]), 3),
                "text": str(line["text"]).strip(),
                "translation": str(line.get("translation") or "").strip(),
                "index": index,
            }
        )

    duration_sec = round(max((item["end"] for item in subtitles), default=0.0), 3)
    timing_ms["align_and_build"] += _measure_elapsed_ms(stage_started_at)
    timing_ms["total"] = _measure_elapsed_ms(pipeline_started_at)
    _raise_if_cancel_requested(should_cancel)
    _emit_progress(progress, 100, "completed", "字幕处理完成")
    sync_diagnostics = {
        "alignment_quality_score": float(alignment_diagnostics.get("alignment_quality_score") or 0.0),
        "global_offset_ms": int(drift_diagnostics.get("global_offset_ms") or 0),
        "drift_scale": float(drift_diagnostics.get("drift_scale") or 1.0),
        "correction_applied": bool(drift_diagnostics.get("correction_applied") or False),
        "correction_method": str(drift_diagnostics.get("correction_method") or "none"),
        "triggered": bool(drift_diagnostics.get("triggered") or False),
        "correction_score": float(drift_diagnostics.get("correction_score") or 0.0),
        "fallback_rows": fallback_rows,
        "fallback_ratio": round(fallback_ratio, 4),
        "alignment_mode": str(alignment_diagnostics.get("alignment_mode") or "strict"),
        "quality_gate_triggered": False,
    }
    llm_usage = _get_llm_usage_snapshot()
    return {
        "subtitles": subtitles,
        "bilingual_srt": bilingual_srt,
        "source_srt": source_srt,
        "word_segments": word_segments,
        "diagnostics": sync_diagnostics,
        "stats": {
            "duration_sec": duration_sec,
            "subtitle_count": len(subtitles),
            "word_count": len(word_segments),
            "word_segments_available": bool(word_segments),
            "whisper_runtime": whisper_runtime,
            "whisper_model_requested": whisper_model_requested,
            "whisper_model_effective": whisper_model_effective,
            "pipeline_version": "v2",
            "asr_profile": options.asr_profile,
            "asr_provider_effective": asr_provider_effective,
            "asr_provider_attempts": asr_provider_attempts,
            "asr_fallback_used": False,
            "llm_base_url": str(llm_usage.get("llm_base_url") or ""),
            "llm_provider_effective": str(llm_usage.get("llm_provider_effective") or ""),
            "llm_model_effective": str(llm_usage.get("llm_model_effective") or ""),
            "prompt_tokens": int(llm_usage.get("prompt_tokens") or 0),
            "completion_tokens": int(llm_usage.get("completion_tokens") or 0),
            "total_tokens": int(llm_usage.get("total_tokens") or 0),
            "llm_request_count": int(llm_usage.get("llm_request_count") or 0),
            "provider_request_id": str(llm_usage.get("provider_request_id") or ""),
            "translation_batch_count": translation_batch_count,
            "timing_ms": timing_ms,
            "sync_diagnostics": sync_diagnostics,
            "stages": [
                "extract_audio",
                "asr",
                "llm_translate",
                "align_and_build",
            ],
        },
    }


def run_llm_postprocess(
    sentences: list[dict],
    options_payload: dict,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
    word_segments: list[dict] | None = None,
) -> dict:
    progress = progress or (lambda percent, stage, message, detail=None: None)
    pipeline_started_at = _measure_started_at()
    timing_ms = _new_timing_ms()
    translation_batch_count = 0
    options = PipelineOptions.from_dict(options_payload or {})
    _start_llm_usage_collection(options.llm)
    word_segments = word_segments or []
    normalized: list[dict] = []
    for item in sentences or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        normalized.append(
            {
                "text": text,
                "translation": str(item.get("translation") or "").strip(),
            }
        )
    if not normalized:
        raise PipelineError("llm_translate", "split_empty", "基础分句后未得到有效句子")

    stage_started_at = _measure_started_at()
    _raise_if_cancel_requested(should_cancel)
    _emit_progress(progress, 72, "llm_translate", "正在执行 LLM 直译")
    source_texts = [str(line.get("text") or "").strip() for line in normalized]
    translations, translation_batch_count = _translate_sentences(
        texts=source_texts,
        source_language=options.source_language,
        target_language=options.target_language,
        llm_opts=options.llm,
        progress_callback=(
            lambda done, total: _emit_stage_detail_progress(
                progress,
                stage="llm_translate",
                stage_start=72,
                stage_end=90,
                fallback_message="正在执行 LLM 直译",
                detail={
                    "step_key": "llm_translate",
                    "step_label": "LLM 直译",
                    "done": done,
                    "total": total,
                    "unit": "row",
                    "percent_in_stage": int(round((done / max(1, total)) * 100)),
                },
            )
        ),
        should_cancel=should_cancel,
    )
    for index, translation in enumerate(translations):
        normalized[index]["translation"] = str(translation or "").strip()
    timing_ms["llm_translate"] += _measure_elapsed_ms(stage_started_at)
    _emit_progress(progress, 90, "llm_translate", f"直译完成，共 {len(normalized)} 句")

    stage_started_at = _measure_started_at()
    _raise_if_cancel_requested(should_cancel)
    _emit_progress(progress, 92, "align_and_build", "正在对齐并构建字幕")
    alignment_diagnostics: dict[str, Any] = {}
    drift_diagnostics: dict[str, Any] = {}
    try:
        normalized, alignment_diagnostics = align_rows_with_word_segments(
            rows=normalized,
            word_segments=word_segments,
            stage="align_and_build",
            progress_reporter=(
                lambda detail: _emit_stage_detail_progress(
                    progress,
                    stage="align_and_build",
                    stage_start=92,
                    stage_end=96,
                    fallback_message="正在对齐并构建字幕",
                    detail=detail,
                )
            ),
            return_diagnostics=True,
        )
    except FlowError as exc:
        raise _pipeline_error_from_flow(exc) from exc
    timing_ms["align_timestamps"] += _measure_elapsed_ms(stage_started_at)
    normalized, drift_diagnostics = _apply_adaptive_drift_sync(
        sentences=normalized,
        word_segments=word_segments,
        alignment_quality_score=float(alignment_diagnostics.get("alignment_quality_score") or 0.0),
    )
    normalized = _normalize_sentence_timeline(normalized)

    _raise_if_cancel_requested(should_cancel)
    _emit_progress(progress, 97, "align_and_build", "正在生成字幕文件")
    source_srt = _build_srt(normalized, include_translation=False)
    bilingual_srt = _build_srt(normalized, include_translation=True)

    subtitles = []
    for index, line in enumerate(normalized):
        subtitles.append(
            {
                "id": index + 1,
                "start": round(float(line["start"]), 3),
                "end": round(float(line["end"]), 3),
                "text": str(line["text"]).strip(),
                "translation": str(line.get("translation") or "").strip(),
                "index": index,
            }
        )

    duration_sec = round(max((item["end"] for item in subtitles), default=0.0), 3)
    timing_ms["align_and_build"] += _measure_elapsed_ms(stage_started_at)
    timing_ms["total"] = _measure_elapsed_ms(pipeline_started_at)
    _raise_if_cancel_requested(should_cancel)
    _emit_progress(progress, 100, "completed", "字幕处理完成")
    sync_diagnostics = {
        "alignment_quality_score": float(alignment_diagnostics.get("alignment_quality_score") or 0.0),
        "global_offset_ms": int(drift_diagnostics.get("global_offset_ms") or 0),
        "drift_scale": float(drift_diagnostics.get("drift_scale") or 1.0),
        "correction_applied": bool(drift_diagnostics.get("correction_applied") or False),
        "correction_method": str(drift_diagnostics.get("correction_method") or "none"),
        "triggered": bool(drift_diagnostics.get("triggered") or False),
        "correction_score": float(drift_diagnostics.get("correction_score") or 0.0),
        "fallback_rows": _safe_positive_int(alignment_diagnostics.get("fallback_rows")),
        "fallback_ratio": round(float(alignment_diagnostics.get("fallback_ratio") or 0.0), 4),
        "alignment_mode": str(alignment_diagnostics.get("alignment_mode") or "strict"),
        "quality_gate_triggered": False,
    }
    llm_usage = _get_llm_usage_snapshot()
    return {
        "subtitles": subtitles,
        "bilingual_srt": bilingual_srt,
        "source_srt": source_srt,
        "word_segments": word_segments,
        "diagnostics": sync_diagnostics,
        "stats": {
            "duration_sec": duration_sec,
            "subtitle_count": len(subtitles),
            "word_count": len(word_segments),
            "word_segments_available": bool(word_segments),
            "resume": True,
            "pipeline_version": "v2",
            "asr_profile": options.asr_profile,
            "asr_provider_effective": _infer_resume_asr_provider(word_segments),
            "asr_provider_attempts": [],
            "asr_fallback_used": False,
            "llm_base_url": str(llm_usage.get("llm_base_url") or ""),
            "llm_provider_effective": str(llm_usage.get("llm_provider_effective") or ""),
            "llm_model_effective": str(llm_usage.get("llm_model_effective") or ""),
            "prompt_tokens": int(llm_usage.get("prompt_tokens") or 0),
            "completion_tokens": int(llm_usage.get("completion_tokens") or 0),
            "total_tokens": int(llm_usage.get("total_tokens") or 0),
            "llm_request_count": int(llm_usage.get("llm_request_count") or 0),
            "provider_request_id": str(llm_usage.get("provider_request_id") or ""),
            "translation_batch_count": translation_batch_count,
            "timing_ms": timing_ms,
            "sync_diagnostics": sync_diagnostics,
            "stages": [
                "llm_translate",
                "align_and_build",
            ],
        },
    }


def _ensure_ffmpeg_available() -> None:
    global _FFMPEG_READY
    with _CACHE_LOCK:
        if _FFMPEG_READY:
            return
    for binary in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run([binary, "-version"], check=True, capture_output=True, text=True)
        except Exception as exc:
            raise PipelineError(
                "extract_audio",
                "ffmpeg_missing",
                f"缺少 {binary}，请先安装 FFmpeg 并确保在 PATH 中",
                detail=str(exc),
            ) from exc
    with _CACHE_LOCK:
        _FFMPEG_READY = True


def _extract_audio(video_path: Path, audio_path: Path) -> None:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(audio_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise PipelineError(
            "extract_audio",
            "ffmpeg_extract_failed",
            "音频提取失败",
            detail=(exc.stderr or exc.stdout or "").strip()[:1000],
        ) from exc


def _infer_resume_asr_provider(word_segments: list[dict]) -> str:
    if not word_segments:
        return ""
    cloud_count = 0
    local_count = 0
    for item in word_segments:
        source = str((item or {}).get("source") or "").strip().lower()
        if source == "cloud":
            cloud_count += 1
        elif source == "local":
            local_count += 1
    if cloud_count == 0 and local_count == 0:
        return ""
    return _CLOUD_ASR_PROVIDER if cloud_count >= local_count else "local_whisperx"


def _provider_runtime_source(provider_name: str) -> str:
    name = str(provider_name or "").strip().lower()
    return "cloud" if name.startswith("cloud_") else "local"


def _resolve_asr_provider_chain(
    whisper: WhisperOptions,
    asr_profile: str,
    fallback_enabled: bool,
    allow_cloud_fallback: bool,
    allow_local_fallback: bool,
) -> list[str]:
    runtime = (whisper.runtime or "cloud").strip().lower() or "cloud"
    profile = (asr_profile or "balanced").strip().lower() or "balanced"
    cloud_provider = _resolve_cloud_asr_provider(whisper.model)
    if profile not in {"fast", "balanced", "accurate"}:
        profile = "balanced"

    providers: list[str] = []
    if runtime == "local":
        if profile == "accurate" and fallback_enabled:
            providers = ["local_whisperx", "local_faster_whisper"]
        elif profile == "accurate":
            providers = ["local_whisperx"]
        else:
            providers = ["local_faster_whisper"]
        if fallback_enabled and allow_cloud_fallback:
            providers.append(cloud_provider)
    elif runtime == "cloud":
        providers = [cloud_provider]
        if fallback_enabled and allow_local_fallback:
            if profile == "accurate":
                providers.extend(["local_whisperx", "local_faster_whisper"])
            else:
                providers.append("local_faster_whisper")
    else:
        raise PipelineError("asr", "invalid_runtime", f"不支持的 whisper.runtime: {runtime}")

    deduped: list[str] = []
    for provider in providers:
        if provider not in deduped:
            deduped.append(provider)
    return deduped


def _dispatch_asr_v2(
    *,
    audio_path: str,
    whisper: WhisperOptions,
    asr_profile: str,
    fallback_enabled: bool,
    allow_cloud_fallback: bool,
    allow_local_fallback: bool,
    enable_diarization: bool,
    asr_progress: Callable[[int, str], None] | None = None,
) -> AsrDispatchResult:
    providers = _resolve_asr_provider_chain(
        whisper=whisper,
        asr_profile=asr_profile,
        fallback_enabled=fallback_enabled,
        allow_cloud_fallback=allow_cloud_fallback,
        allow_local_fallback=allow_local_fallback,
    )
    if not providers:
        raise PipelineError("asr", "asr_provider_chain_empty", "未生成可用的 ASR 提供者链路")

    attempt_errors: list[dict] = []
    for index, provider in enumerate(providers):
        try:
            if asr_progress:
                asr_progress(30, f"正在准备识别引擎：{provider}")
            if provider in {_CLOUD_ASR_PROVIDER, _CLOUD_QWEN_ASR_PROVIDER}:
                segments, provider_effective, model_effective = _transcribe_cloud_asr(audio_path, whisper)
                return AsrDispatchResult(
                    segments=segments,
                    provider_effective=provider_effective,
                    attempts=providers[: index + 1],
                    fallback_used=index > 0,
                    runtime_effective="cloud",
                    model_effective=model_effective,
                )
            if provider == "local_faster_whisper":
                segments, model_effective = _transcribe_local(
                    audio_path,
                    whisper,
                    asr_progress=asr_progress,
                    return_model_name=True,
                )
                return AsrDispatchResult(
                    segments=segments,
                    provider_effective=provider,
                    attempts=providers[: index + 1],
                    fallback_used=index > 0,
                    runtime_effective="local",
                    model_effective=model_effective,
                )
            if provider == "local_whisperx":
                segments, model_effective = _transcribe_local_whisperx(
                    audio_path,
                    whisper,
                    enable_diarization=enable_diarization,
                    asr_progress=asr_progress,
                )
                return AsrDispatchResult(
                    segments=segments,
                    provider_effective=provider,
                    attempts=providers[: index + 1],
                    fallback_used=index > 0,
                    runtime_effective="local",
                    model_effective=model_effective,
                )
            raise PipelineError("asr", "asr_provider_unknown", f"未知 ASR provider: {provider}")
        except PipelineError as exc:
            attempt_errors.append(
                {
                    "provider": provider,
                    "code": exc.code,
                    "message": exc.message,
                }
            )
            continue
        except Exception as exc:
            attempt_errors.append(
                {
                    "provider": provider,
                    "code": "unexpected",
                    "message": str(exc),
                }
            )
            continue

    raise PipelineError(
        "asr",
        "asr_all_providers_failed",
        "全部 ASR 提供者执行失败",
        detail=json.dumps(
            {
                "attempts": providers,
                "errors": attempt_errors,
            },
            ensure_ascii=False,
        )[:4000],
    )


def _transcribe(
    audio_path: str,
    whisper: WhisperOptions,
    asr_progress: Callable[[int, str], None] | None = None,
) -> list[dict]:
    runtime = (whisper.runtime or "cloud").strip().lower()
    if runtime == "cloud":
        segments, _, _ = _transcribe_cloud_asr(audio_path, whisper)
        return segments
    if runtime == "local":
        segments, _ = _transcribe_local(
            audio_path,
            whisper,
            asr_progress=asr_progress,
            return_model_name=True,
        )
        return segments
    raise PipelineError("asr", "invalid_runtime", f"不支持的 whisper.runtime: {runtime}")


def _ms_to_seconds(value: Any) -> float | None:
    milliseconds = _to_finite_float(value)
    if milliseconds is None:
        return None
    return round(max(0.0, milliseconds) / 1000.0, 3)


def _normalize_paraformer_word_items(words: Any) -> list[dict]:
    normalized: list[dict] = []
    if not isinstance(words, list):
        return normalized
    for item in words:
        if isinstance(item, dict):
            raw_word = item.get("text") or item.get("word") or item.get("token") or ""
            begin_time = item.get("begin_time")
            end_time = item.get("end_time")
        else:
            raw_word = getattr(item, "text", None) or getattr(item, "word", None) or getattr(item, "token", None) or ""
            begin_time = getattr(item, "begin_time", None)
            end_time = getattr(item, "end_time", None)
        word = str(raw_word or "").strip()
        start = _ms_to_seconds(begin_time)
        end = _ms_to_seconds(end_time)
        if not word or start is None or end is None or end <= start:
            continue
        normalized.append(
            {
                "word": word,
                "start": start,
                "end": end,
                "confidence": None,
            }
        )
    return normalized


def _extract_segments_from_paraformer_payload(data: dict) -> list[dict] | None:
    transcripts = data.get("transcripts")
    if not isinstance(transcripts, list):
        return None

    segments: list[dict] = []
    last_end = 0.0
    for transcript in transcripts:
        if not isinstance(transcript, dict):
            continue
        sentences = transcript.get("sentences")
        if not isinstance(sentences, list):
            continue
        for sentence in sentences:
            if not isinstance(sentence, dict):
                continue
            words = _normalize_paraformer_word_items(sentence.get("words") or [])
            text = str(sentence.get("text") or "").strip()
            if not text and words:
                text = " ".join(str(item.get("word") or "").strip() for item in words).strip()
            if not text:
                continue
            start = _ms_to_seconds(sentence.get("begin_time"))
            end = _ms_to_seconds(sentence.get("end_time"))
            if words:
                if start is None:
                    start = float(words[0]["start"])
                if end is None:
                    end = float(words[-1]["end"])
            if start is None:
                start = last_end
            if end is None or end <= start:
                end = start + 0.8
            start = round(max(0.0, float(start)), 3)
            end = round(max(start, float(end)), 3)
            last_end = end
            segments.append(
                {
                    "start": start,
                    "end": end,
                    "text": text,
                    "words": words,
                }
            )
    return segments or None


def _to_finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _normalize_word_items(words: Any) -> list[dict]:
    normalized: list[dict] = []
    if not words:
        return normalized

    for item in words:
        if isinstance(item, dict):
            raw_word = item.get("word") or item.get("text") or item.get("token") or ""
            start_raw = item.get("start")
            if start_raw is None:
                start_raw = item.get("start_time")
            end_raw = item.get("end")
            if end_raw is None:
                end_raw = item.get("end_time")
            confidence_raw = item.get("confidence")
            if confidence_raw is None:
                confidence_raw = item.get("score")
            if confidence_raw is None:
                confidence_raw = item.get("probability")
            if confidence_raw is None:
                confidence_raw = item.get("logprob")
        else:
            raw_word = (
                getattr(item, "word", None)
                or getattr(item, "text", None)
                or getattr(item, "token", None)
                or ""
            )
            start_raw = getattr(item, "start", None)
            if start_raw is None:
                start_raw = getattr(item, "start_time", None)
            end_raw = getattr(item, "end", None)
            if end_raw is None:
                end_raw = getattr(item, "end_time", None)
            confidence_raw = getattr(item, "confidence", None)
            if confidence_raw is None:
                confidence_raw = getattr(item, "score", None)
            if confidence_raw is None:
                confidence_raw = getattr(item, "probability", None)
            if confidence_raw is None:
                confidence_raw = getattr(item, "logprob", None)

        word = str(raw_word or "").strip()
        start = _to_finite_float(start_raw)
        end = _to_finite_float(end_raw)
        if not word or start is None or end is None:
            continue
        if start < 0 or end <= start:
            continue

        confidence = _to_finite_float(confidence_raw)
        normalized.append(
            {
                "word": word,
                "start": round(start, 3),
                "end": round(end, 3),
                "confidence": round(confidence, 6) if confidence is not None else None,
            }
        )
    return normalized


def _flatten_word_segments(segments: Iterable[dict], source: str) -> list[dict]:
    flattened: list[dict] = []
    for asr_segment_index, segment in enumerate(segments):
        words = _normalize_word_items(segment.get("words") or [])
        for word in words:
            flattened.append(
                {
                    "id": len(flattened) + 1,
                    "start": word["start"],
                    "end": word["end"],
                    "word": word["word"],
                    "confidence": word.get("confidence"),
                    "asr_segment_index": asr_segment_index,
                    "source": source,
                }
            )
    return flattened


_ASR_RETRY_HINT_TOKENS = (
    "unsupported",
    "not support",
    "not_supported",
    "unknown parameter",
    "unrecognized",
    "invalid parameter",
    "extra fields",
    "unknown url",
    "unknown endpoint",
    "no route",
    "route not found",
    "not found",
    "method not allowed",
    "invalid endpoint",
    "cannot post",
)
_ASR_NO_RETRY_HINT_TOKENS = (
    "invalid api key",
    "incorrect api key",
    "authentication",
    "unauthorized",
    "forbidden",
    "insufficient_quota",
    "insufficient quota",
    "billing",
)
_ASR_ENDPOINT_SUFFIXES = (
    "/audio/transcriptions",
    "/files/transcriptions",
)
_TRANSCRIPTION_START_TIME_KEYS: tuple[tuple[str, bool], ...] = (
    ("start", False),
    ("start_time", False),
    ("startTime", False),
    ("from", False),
    ("offset", False),
    ("begin", False),
    ("start_ms", True),
    ("startTimeMs", True),
    ("begin_time", True),
    ("beginTime", True),
)
_TRANSCRIPTION_END_TIME_KEYS: tuple[tuple[str, bool], ...] = (
    ("end", False),
    ("end_time", False),
    ("endTime", False),
    ("to", False),
    ("finish", False),
    ("stop", False),
    ("end_ms", True),
    ("endTimeMs", True),
    ("stop_time", True),
)


def _build_asr_endpoint_candidates(base_url: str) -> list[str]:
    normalized = _normalize_base_url(base_url)
    normalized_lower = normalized.lower()
    base_root = normalized
    for suffix in _ASR_ENDPOINT_SUFFIXES:
        if normalized_lower.endswith(suffix):
            base_root = normalized[: -len(suffix)].rstrip("/") or normalized
            break

    seen: set[str] = set()
    candidates: list[str] = []
    for suffix in _ASR_ENDPOINT_SUFFIXES:
        endpoint = f"{base_root.rstrip('/')}{suffix}"
        key = endpoint.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(endpoint)
    if not candidates:
        candidates.append(normalized.rstrip("/"))
    return candidates


def _build_asr_request_field_candidates(*, model: str, language: str) -> list[list[tuple[str, str]]]:
    shared_fields: list[tuple[str, str]] = [("model", str(model or "").strip())]
    safe_language = str(language or "").strip()
    if safe_language:
        shared_fields.append(("language", safe_language))
    raw_candidates: list[list[tuple[str, str]]] = [
        [
            *shared_fields,
            ("response_format", "verbose_json"),
            ("timestamp_granularities[]", "word"),
            ("timestamp_granularities[]", "segment"),
        ],
        [
            *shared_fields,
            ("response_format", "verbose_json"),
            ("timestamp_granularities", "word"),
            ("timestamp_granularities", "segment"),
        ],
        [*shared_fields, ("response_format", "verbose_json")],
        [*shared_fields],
    ]
    normalized: list[list[tuple[str, str]]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for candidate in raw_candidates:
        key = tuple((str(name or "").strip(), str(value or "").strip()) for name, value in candidate)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(list(key))
    return normalized


def _extract_asr_error_message(payload: Any, *, fallback_text: str = "") -> str:
    if isinstance(payload, dict):
        error_payload = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        if isinstance(error_payload, dict):
            message = str(error_payload.get("message") or error_payload.get("error") or "").strip()
            if message:
                return message
        message = str(payload.get("message") or payload.get("detail") or payload.get("error") or "").strip()
        if message:
            return message
        try:
            return json.dumps(payload, ensure_ascii=False)[:800]
        except Exception:
            return str(payload)[:800]
    if isinstance(payload, list):
        try:
            return json.dumps(payload, ensure_ascii=False)[:800]
        except Exception:
            return str(payload)[:800]
    return str(fallback_text or "").strip()


def _should_retry_asr_request(status_code: int | None, error_text: str) -> bool:
    text = str(error_text or "").lower()
    if any(token in text for token in _ASR_NO_RETRY_HINT_TOKENS):
        return False
    if status_code is None:
        return True
    if status_code in {401, 403}:
        return False
    if status_code >= 500:
        return True
    if status_code in {404, 405, 406, 408, 410, 415, 421, 422, 425, 426, 429}:
        return True
    if status_code == 400:
        return any(token in text for token in _ASR_RETRY_HINT_TOKENS)
    return any(token in text for token in _ASR_RETRY_HINT_TOKENS)


def _extract_time_seconds_from_mapping(raw: dict[str, Any], keys: tuple[tuple[str, bool], ...]) -> float | None:
    for key, as_milliseconds in keys:
        if key not in raw:
            continue
        value = _to_finite_float(raw.get(key))
        if value is None:
            continue
        seconds = value / 1000.0 if as_milliseconds else value
        return round(max(0.0, seconds), 3)
    return None


def _normalize_transcription_word_items(words: Any) -> list[dict]:
    if not isinstance(words, list):
        return []
    normalized: list[dict] = []
    for item in words:
        if isinstance(item, dict):
            safe = item
        else:
            safe = {
                "word": getattr(item, "word", None),
                "text": getattr(item, "text", None),
                "token": getattr(item, "token", None),
                "start": getattr(item, "start", None),
                "start_time": getattr(item, "start_time", None),
                "end": getattr(item, "end", None),
                "end_time": getattr(item, "end_time", None),
                "confidence": getattr(item, "confidence", None),
                "score": getattr(item, "score", None),
            }
        word = str(safe.get("word") or safe.get("text") or safe.get("token") or "").strip()
        start = _extract_time_seconds_from_mapping(safe, _TRANSCRIPTION_START_TIME_KEYS)
        end = _extract_time_seconds_from_mapping(safe, _TRANSCRIPTION_END_TIME_KEYS)
        if not word or start is None or end is None or end <= start:
            continue
        confidence = _to_finite_float(safe.get("confidence"))
        if confidence is None:
            confidence = _to_finite_float(safe.get("score"))
        if confidence is None:
            confidence = _to_finite_float(safe.get("probability"))
        normalized.append(
            {
                "word": word,
                "start": start,
                "end": end,
                "confidence": round(confidence, 6) if confidence is not None else None,
            }
        )
    return normalized


def _extract_segments_from_openai_transcription_payload(payload: dict) -> list[dict] | None:
    has_openai_shape = any(key in payload for key in ("text", "segments", "words"))
    text = str(payload.get("text") or "").strip()
    global_words = _normalize_transcription_word_items(payload.get("words"))
    segments_raw = payload.get("segments")

    if isinstance(segments_raw, list):
        segments: list[dict] = []
        last_end = 0.0
        for raw_segment in segments_raw:
            if not isinstance(raw_segment, dict):
                continue
            words = _normalize_transcription_word_items(raw_segment.get("words"))
            start = _extract_time_seconds_from_mapping(raw_segment, _TRANSCRIPTION_START_TIME_KEYS)
            end = _extract_time_seconds_from_mapping(raw_segment, _TRANSCRIPTION_END_TIME_KEYS)
            if not words and global_words and start is not None and end is not None and end > start:
                words = [
                    item
                    for item in global_words
                    if float(item.get("start") or 0.0) >= start - 0.05 and float(item.get("end") or 0.0) <= end + 0.05
                ]
            segment_text = str(raw_segment.get("text") or "").strip()
            if not segment_text and words:
                segment_text = " ".join(str(item.get("word") or "").strip() for item in words).strip()
            if not segment_text:
                continue
            if start is None and words:
                start = float(words[0]["start"])
            if end is None and words:
                end = float(words[-1]["end"])
            if start is None:
                start = last_end
            if end is None or end <= start:
                end = start + 0.8
            start = round(max(0.0, float(start)), 3)
            end = round(max(start, float(end)), 3)
            last_end = end
            segments.append(
                {
                    "start": start,
                    "end": end,
                    "text": segment_text,
                    "words": words,
                }
            )
        if segments:
            return segments

    if global_words:
        merged_text = text or " ".join(str(item.get("word") or "").strip() for item in global_words).strip()
        if merged_text:
            start = float(global_words[0]["start"])
            end = float(global_words[-1]["end"])
            if end <= start:
                end = start + 0.8
            return [
                {
                    "start": round(max(0.0, start), 3),
                    "end": round(max(start, end), 3),
                    "text": merged_text,
                    "words": global_words,
                }
            ]

    if text:
        return [{"start": 0.0, "end": 0.8, "text": text, "words": []}]
    if has_openai_shape:
        return []
    return None


def _extract_segments_from_cloud_transcription_payload(payload: dict) -> list[dict] | None:
    candidate_payloads: list[dict] = []

    def _append_candidate(value: Any) -> None:
        if isinstance(value, dict):
            candidate_payloads.append(value)

    _append_candidate(payload)
    for key in ("data", "result", "output", "transcription", "response"):
        value = payload.get(key)
        if isinstance(value, dict):
            _append_candidate(value)
            continue
        if isinstance(value, list):
            for item in value:
                _append_candidate(item)

    for candidate in candidate_payloads:
        segments = _extract_segments_from_paraformer_payload(candidate)
        if segments is not None:
            return segments
        segments = _extract_segments_from_openai_transcription_payload(candidate)
        if segments is not None:
            return segments
    return None


def _transcribe_cloud_openai_compatible(
    audio_path: str,
    whisper: WhisperOptions,
    *,
    model: str,
    model_label: str,
) -> list[dict]:
    api_key = (whisper.api_key or "").strip()
    if not api_key:
        raise PipelineError("asr", "missing_whisper_api_key", "whisper.runtime=cloud 时必须提供 whisper.api_key")

    language = str(whisper.language or "").strip()
    base_url = _normalize_base_url(whisper.base_url)
    endpoints = _build_asr_endpoint_candidates(base_url)
    field_candidates = _build_asr_request_field_candidates(model=model, language=language)
    failure_details: list[str] = []
    audio_name = Path(audio_path).name or "audio.wav"

    print(
        f"[DEBUG] {model_label} cloud request model={model} "
        f"language={language or '-'} base_url={base_url} endpoints={len(endpoints)}"
    )

    for endpoint in endpoints:
        for index, fields in enumerate(field_candidates):
            if index > 0:
                print(
                    f"[DEBUG] {model_label} cloud request retry "
                    f"endpoint={endpoint} payload_variant={index + 1}"
                )
            try:
                with open(audio_path, "rb") as audio_stream:
                    response = requests.post(
                        endpoint,
                        headers={"Authorization": f"Bearer {api_key}"},
                        data=fields,
                        files={"file": (audio_name, audio_stream, "audio/wav")},
                        timeout=180,
                    )
            except Exception as exc:
                error_text = f"request_error={str(exc)[:420]}"
                failure_details.append(f"endpoint={endpoint}; status=request_error; detail={error_text}")
                if _should_retry_asr_request(None, error_text):
                    continue
                raise PipelineError(
                    "asr",
                    "cloud_asr_failed",
                    f"{model_label} 云端识别请求失败",
                    detail="\n".join(failure_details)[:1200],
                ) from exc

            payload: Any = None
            try:
                payload = response.json()
            except Exception:
                payload = None

            if int(response.status_code) >= 400:
                error_text = _extract_asr_error_message(payload, fallback_text=str(response.text or "")[:600])
                failure_detail = (
                    f"endpoint={endpoint}; status={int(response.status_code)}; detail={error_text[:420]}"
                )
                failure_details.append(failure_detail)
                print(f"[DEBUG] {model_label} cloud request failed {failure_detail}")
                if _should_retry_asr_request(int(response.status_code), error_text):
                    continue
                raise PipelineError(
                    "asr",
                    "cloud_asr_failed",
                    f"{model_label} 云端识别失败（HTTP {int(response.status_code)}）",
                    detail="\n".join(failure_details)[:1200],
                )

            if not isinstance(payload, dict):
                body_preview = str(response.text or "")[:420]
                failure_detail = (
                    f"endpoint={endpoint}; status=200; detail=non_json_body:{body_preview}"
                )
                failure_details.append(failure_detail)
                print(f"[DEBUG] {model_label} cloud request failed {failure_detail}")
                continue

            segments = _extract_segments_from_cloud_transcription_payload(payload)
            if segments is not None:
                print(f"[DEBUG] {model_label} cloud request success endpoint={endpoint} segments={len(segments)}")
                return segments

            payload_keys = list(payload.keys())[:16]
            failure_detail = (
                f"endpoint={endpoint}; status=200; detail=unrecognized_payload_keys:{payload_keys}"
            )
            failure_details.append(failure_detail)
            print(f"[DEBUG] {model_label} cloud request failed {failure_detail}")

    raise PipelineError(
        "asr",
        "cloud_asr_failed",
        f"{model_label} 云端识别结果缺少可解析分句",
        detail="\n".join(failure_details)[:1200],
    )


def _transcribe_paraformer_v2(audio_path: str, whisper: WhisperOptions) -> list[dict]:
    requested_model = (whisper.model or "").strip()
    model = _CLOUD_ASR_MODEL
    print(
        f"[DEBUG] Paraformer cloud request model={model} "
        f"requested_model={requested_model or '-'}"
    )
    return _transcribe_cloud_openai_compatible(
        audio_path,
        whisper,
        model=model,
        model_label="Paraformer",
    )


def _transcribe_qwen3_asr_flash_filetrans(audio_path: str, whisper: WhisperOptions) -> list[dict]:
    requested_model = (whisper.model or "").strip()
    model = _CLOUD_QWEN_ASR_MODEL
    print(
        f"[DEBUG] Qwen ASR cloud request model={model} "
        f"requested_model={requested_model or '-'}"
    )
    return _transcribe_cloud_openai_compatible(
        audio_path,
        whisper,
        model=model,
        model_label="Qwen ASR",
    )


def _transcribe_local(
    audio_path: str,
    whisper: WhisperOptions,
    asr_progress: Callable[[int, str], None] | None = None,
    return_model_name: bool = False,
) -> list[dict] | tuple[list[dict], str]:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception as exc:
        raise PipelineError(
            "asr",
            "local_runtime_missing",
            "本地 ASR 依赖缺失，请安装 faster-whisper 或切换到 cloud 运行模式",
            detail=str(exc),
        ) from exc

    _, _, model_name = resolve_whisper_runtime_models(whisper)
    language = (whisper.language or "").strip() or None
    device = "cpu"
    compute_type = "int8"
    try:
        if asr_progress:
            asr_progress(30, f"正在加载模型：{model_name}")
        cpu_threads = max(1, int(os.cpu_count() or 4))
        cache_key = (model_name, device, compute_type, cpu_threads)
        model = _cache_get(_FASTER_WHISPER_MODEL_CACHE, cache_key)
        if model is None:
            model = WhisperModel(
                model_name,
                device=device,
                compute_type=compute_type,
                cpu_threads=cpu_threads,
            )
            model = _cache_set(
                _FASTER_WHISPER_MODEL_CACHE,
                cache_key,
                model,
                _FASTER_WHISPER_MODEL_CACHE_MAX,
            )
        transcribe_kwargs = {
            "language": language,
            "word_timestamps": True,
            "beam_size": 1,
            "best_of": 1,
            "condition_on_previous_text": False,
            "vad_filter": True,
        }
        if asr_progress:
            asr_progress(30, "模型已就绪，正在开始识别")
        segments_iter, _ = model.transcribe(audio_path, **transcribe_kwargs)
    except Exception as exc:
        raise PipelineError("asr", "local_asr_failed", "本地 ASR 执行失败", detail=str(exc)) from exc

    try:
        import wave

        with wave.open(audio_path, "rb") as wav_reader:
            frame_rate = max(1, int(wav_reader.getframerate() or 16000))
            frame_count = max(0, int(wav_reader.getnframes() or 0))
            audio_duration_sec = frame_count / frame_rate
    except Exception:
        audio_duration_sec = 0.0

    segments = []
    last_progress_percent = 30
    last_emit_at = 0.0
    decode_started_at = time.monotonic()
    for seg in segments_iter:
        text = str(getattr(seg, "text", "") or "").strip()
        if not text:
            continue
        start = float(getattr(seg, "start", 0.0) or 0.0)
        end = float(getattr(seg, "end", 0.0) or 0.0)
        if end <= start:
            end = start + 0.8
        words = _normalize_word_items(getattr(seg, "words", None) or [])
        segments.append({"start": start, "end": end, "text": text, "words": words})

        # 在 ASR 长阶段内提供细粒度反馈，避免前端只看到 30% 长时间不动。
        if audio_duration_sec > 0:
            ratio = max(0.0, min(1.0, end / audio_duration_sec))
            percent = 30 + int(ratio * 11)
            percent = max(30, min(41, percent))
        else:
            percent = min(41, 30 + int(math.log2(len(segments) + 1)))

        now = time.monotonic()
        should_emit = percent > last_progress_percent or (now - last_emit_at >= 1.0)
        if should_emit:
            last_progress_percent = percent
            last_emit_at = now
            if audio_duration_sec > 0:
                eta_label = ""
                if end > 0.5:
                    decode_elapsed = max(0.0, now - decode_started_at)
                    ratio = max(0.01, min(1.0, end / audio_duration_sec))
                    estimated_total = decode_elapsed / ratio
                    remaining_seconds = max(0.0, estimated_total - decode_elapsed)
                    eta_label = f"，预计剩余 {_format_seconds_label(remaining_seconds)}"
                progress_msg = (
                    f"识别中：已识别 {len(segments)} 段"
                    f"（{_format_seconds_label(end)} / {_format_seconds_label(audio_duration_sec)}{eta_label}）"
                )
            else:
                progress_msg = f"识别中：已识别 {len(segments)} 段"
            if asr_progress:
                asr_progress(percent, progress_msg)
    if return_model_name:
        return segments, model_name
    return segments


def _transcribe_local_whisperx(
    audio_path: str,
    whisper: WhisperOptions,
    *,
    enable_diarization: bool,
    asr_progress: Callable[[int, str], None] | None = None,
) -> tuple[list[dict], str]:
    try:
        import whisperx  # type: ignore
    except Exception as exc:
        raise PipelineError(
            "asr",
            "local_whisperx_missing",
            "未安装 whisperx，无法执行高精度本地对齐",
            detail=str(exc),
        ) from exc

    _, _, model_name = resolve_whisper_runtime_models(
        WhisperOptions(
            runtime="local",
            model=whisper.model,
            language=whisper.language,
            base_url=whisper.base_url,
            api_key=whisper.api_key,
        )
    )
    language = (whisper.language or "").strip().lower()
    language = "" if language == "auto" else language
    device = "cpu"
    compute_type = "int8"
    asr_model_cache_key = (model_name, device, compute_type, language or "auto")
    try:
        if asr_progress:
            asr_progress(30, f"WhisperX 正在加载模型：{model_name}")
        audio = whisperx.load_audio(audio_path)
        asr_model = _cache_get(_WHISPERX_ASR_MODEL_CACHE, asr_model_cache_key)
        if asr_model is None:
            asr_model = whisperx.load_model(
                model_name,
                device=device,
                compute_type=compute_type,
                language=language or None,
            )
            asr_model = _cache_set(
                _WHISPERX_ASR_MODEL_CACHE,
                asr_model_cache_key,
                asr_model,
                _WHISPERX_ASR_MODEL_CACHE_MAX,
            )
        if asr_progress:
            asr_progress(31, "WhisperX 模型已就绪，开始识别")
        result = asr_model.transcribe(audio, batch_size=8)
        result_language = str(result.get("language") or language or "en").strip().lower() or "en"
        align_model_cache_key = (result_language, device)
        align_cache_value = _cache_get(_WHISPERX_ALIGN_MODEL_CACHE, align_model_cache_key)
        if align_cache_value is None:
            align_model, metadata = whisperx.load_align_model(language_code=result_language, device=device)
            align_cache_value = _cache_set(
                _WHISPERX_ALIGN_MODEL_CACHE,
                align_model_cache_key,
                (align_model, metadata),
                _WHISPERX_ALIGN_MODEL_CACHE_MAX,
            )
        else:
            align_model, metadata = align_cache_value
        if asr_progress:
            asr_progress(33, "WhisperX 正在对齐时间轴")
        aligned = whisperx.align(
            result.get("segments", []),
            align_model,
            metadata,
            audio,
            device,
            return_char_alignments=False,
        )
        aligned_segments = aligned.get("segments", []) if isinstance(aligned, dict) else []
        if enable_diarization:
            hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
            if hf_token:
                try:
                    if asr_progress:
                        asr_progress(35, "WhisperX 正在执行说话人分离")
                    diarize_pipeline = whisperx.DiarizationPipeline(
                        use_auth_token=hf_token,
                        device=device,
                    )
                    diarized = diarize_pipeline(audio)
                    assign_payload = whisperx.assign_word_speakers(diarized, {"segments": aligned_segments})
                    aligned_segments = assign_payload.get("segments", aligned_segments)
                except Exception:
                    pass
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError("asr", "local_whisperx_failed", "本地 whisperX 执行失败", detail=str(exc)) from exc

    normalized: list[dict] = []
    for segment in aligned_segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        start = _to_finite_float(segment.get("start"))
        end = _to_finite_float(segment.get("end"))
        if not text or start is None:
            continue
        if end is None or end <= start:
            end = start + 0.8
        words = _normalize_word_items(segment.get("words") or [])
        normalized.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
                "words": words,
            }
        )

    if not normalized:
        raise PipelineError("asr", "local_whisperx_empty_segments", "本地 whisperX 未返回有效片段")
    return normalized, model_name


def _format_seconds_label(seconds: float) -> str:
    safe_sec = max(0, int(round(seconds or 0)))
    minutes = safe_sec // 60
    remain = safe_sec % 60
    return f"{minutes:02d}:{remain:02d}"


def _split_text_nlp(text: str) -> list[str]:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return []
    parts = re.split(r"(?<=[。！？!?;；\.])\s+", clean)
    normalized = [item.strip() for item in parts if item and item.strip()]
    return normalized or [clean]


def _split_segments_to_sentences(segments: Iterable[dict]) -> list[dict]:
    sentences: list[dict] = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        parts = _split_text_nlp(text)
        for part in parts:
            if part:
                sentences.append({"text": part})
    return sentences


def _remove_punctuation_for_match(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or ""))
    value = re.sub(r"[^\w\s]", "", value)
    return value.strip()


def _build_word_alignment_index(word_segments: list[dict]) -> tuple[str, dict[int, int], list[dict]]:
    full_words_str = ""
    position_to_word_idx: dict[int, int] = {}
    normalized_words: list[dict] = []
    for word in word_segments or []:
        if not isinstance(word, dict):
            continue
        token = _remove_punctuation_for_match(str(word.get("word") or "").lower()).replace(" ", "")
        if not token:
            continue
        start = _to_finite_float(word.get("start"))
        end = _to_finite_float(word.get("end"))
        if start is None or end is None or end <= start:
            continue
        normalized_words.append(
            {
                "word": token,
                "start": float(start),
                "end": float(end),
            }
        )
        word_idx = len(normalized_words) - 1
        start_pos = len(full_words_str)
        full_words_str += token
        for pos in range(start_pos, len(full_words_str)):
            position_to_word_idx[pos] = word_idx
    return full_words_str, position_to_word_idx, normalized_words


def _align_sentences_with_word_timestamps(
    *,
    sentences: list[dict],
    word_segments: list[dict],
    stage: str,
) -> list[dict]:
    full_words_str, position_to_word_idx, normalized_words = _build_word_alignment_index(word_segments or [])
    if not normalized_words or not full_words_str:
        raise PipelineError(
            stage,
            "timestamp_alignment_failed",
            "词级时间戳缺失，无法对齐字幕",
            detail=json.dumps({"reason": "word_segments_empty"}, ensure_ascii=False),
        )

    aligned_rows: list[dict] = []
    current_pos = 0
    for sentence_index, row in enumerate(sentences or []):
        text = str((row or {}).get("text") or "").strip()
        if not text:
            continue
        clean_sentence = _remove_punctuation_for_match(text.lower()).replace(" ", "")
        if not clean_sentence:
            continue

        sentence_len = len(clean_sentence)
        match_found = False
        while current_pos <= len(full_words_str) - sentence_len:
            if full_words_str[current_pos : current_pos + sentence_len] == clean_sentence:
                start_word_idx = position_to_word_idx.get(current_pos)
                end_word_idx = position_to_word_idx.get(current_pos + sentence_len - 1)
                if start_word_idx is None or end_word_idx is None:
                    break
                start = float(normalized_words[start_word_idx]["start"])
                end = float(normalized_words[end_word_idx]["end"])
                if end < start:
                    end = start
                aligned_row = {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "text": text,
                }
                translation = str((row or {}).get("translation") or "").strip()
                if translation:
                    aligned_row["translation"] = translation
                aligned_rows.append(aligned_row)
                current_pos += sentence_len
                match_found = True
                break
            current_pos += 1

        if not match_found:
            context_start = max(0, current_pos - 24)
            context_end = min(len(full_words_str), current_pos + sentence_len + 24)
            context = full_words_str[context_start:context_end]
            raise PipelineError(
                stage,
                "timestamp_alignment_failed",
                "词级时间戳对齐失败",
                detail=json.dumps(
                    {
                        "sentence_index": sentence_index,
                        "sentence": text,
                        "normalized_sentence": clean_sentence,
                        "search_position": current_pos,
                        "context": context,
                    },
                    ensure_ascii=False,
                ),
            )

    for idx in range(len(aligned_rows) - 1):
        current_row = aligned_rows[idx]
        next_row = aligned_rows[idx + 1]
        delta_time = float(next_row["start"]) - float(current_row["end"])
        if 0 < delta_time < 1:
            current_row["end"] = round(float(next_row["start"]), 3)
        if float(current_row["end"]) < float(current_row["start"]):
            current_row["end"] = round(float(current_row["start"]), 3)

    return aligned_rows


RESPONSES_PREFERRED_MODEL_PREFIXES = (
    "gpt-5",
    "o1",
    "o3",
    "o4",
)
FALLBACK_HINT_TOKENS = (
    "unsupported",
    "not support",
    "not_supported",
    "unknown parameter",
    "unrecognized",
    "unknown url",
    "unknown endpoint",
    "no route",
    "route not found",
    "not found",
    "method not allowed",
    "invalid endpoint",
    "cannot post",
)
NO_FALLBACK_HINT_TOKENS = (
    "invalid api key",
    "incorrect api key",
    "authentication",
    "unauthorized",
    "forbidden",
    "insufficient_quota",
    "insufficient quota",
    "billing",
)


def _normalize_base_url(base_url: str) -> str:
    value = (base_url or "").strip()
    if not value:
        value = "https://api.siliconflow.cn/v1"
    if "://" not in value:
        value = f"https://{value}"
    normalized_lower = value.lower().rstrip("/")
    for suffix in ("/responses", "/chat/completions", "/completions"):
        if normalized_lower.endswith(suffix):
            value = value[: -len(suffix)]
            break
    return value.rstrip("/")


def _infer_llm_protocol_candidates(base_url: str, model: str = "") -> list[str]:
    raw = (base_url or "").strip()
    model_lower = (model or "").strip().lower()
    if not raw and any(model_lower.startswith(prefix) for prefix in RESPONSES_PREFERRED_MODEL_PREFIXES):
        return ["responses", "chat"]

    normalized = raw.lower().rstrip("/")
    first = "chat"
    explicit_protocol: str | None = None
    if normalized.endswith("/responses"):
        first = "responses"
        explicit_protocol = "responses"
    elif normalized.endswith("/chat/completions") or normalized.endswith("/completions"):
        first = "chat"
        explicit_protocol = "chat"

    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    except Exception:
        parsed = None

    if parsed:
        path = (parsed.path or "").lower()
        if path.endswith("/responses"):
            first = "responses"
            explicit_protocol = "responses"
        elif path.endswith("/chat/completions") or path.endswith("/completions"):
            first = "chat"
            explicit_protocol = "chat"

    if explicit_protocol is None and first != "responses" and any(
        model_lower.startswith(prefix) for prefix in RESPONSES_PREFERRED_MODEL_PREFIXES
    ):
        first = "responses"

    second = "chat" if first == "responses" else "responses"
    return [first, second]


def _should_use_responses_api(base_url: str, model: str = "") -> bool:
    candidates = _infer_llm_protocol_candidates(base_url, model=model)
    return len(candidates) > 0 and candidates[0] == "responses"


def _should_fallback_protocol(status_code: int | None, error_text: str) -> bool:
    text = str(error_text or "").lower()
    if any(token in text for token in NO_FALLBACK_HINT_TOKENS):
        return False
    if status_code is None:
        return True
    if status_code in {401, 403}:
        return False
    if status_code >= 500:
        return True
    if status_code in {404, 405, 406, 408, 410, 415, 421, 422, 425, 426, 429}:
        return True
    if status_code == 400:
        return any(token in text for token in FALLBACK_HINT_TOKENS)
    return any(token in text for token in FALLBACK_HINT_TOKENS)


def _get_llm_client(opts: LlmOptions) -> OpenAI:
    api_key = (opts.api_key or "").strip()
    if not api_key:
        raise PipelineError("llm_precheck", "missing_llm_api_key", "缺少 LLM API Key")
    return OpenAI(api_key=api_key, base_url=_normalize_base_url(opts.base_url))


def _get_llm_probe_cache_key(opts: LlmOptions) -> str:
    protocol = ">".join(_infer_llm_protocol_candidates(opts.base_url, opts.model))
    raw = "|".join(
        [
            _normalize_base_url(opts.base_url),
            str(opts.model or "").strip(),
            str(opts.api_key or "").strip(),
            protocol,
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _probe_llm_access(opts: LlmOptions) -> bool:
    api_key = (opts.api_key or "").strip()
    if not api_key:
        raise PipelineError("llm_precheck", "missing_llm_api_key", "缺少 LLM API Key")

    now = time.time()
    cache_key = _get_llm_probe_cache_key(opts)
    with _CACHE_LOCK:
        stale_keys = [key for key, expires_at in _LLM_PROBE_CACHE.items() if float(expires_at) <= now]
        for stale_key in stale_keys:
            _LLM_PROBE_CACHE.pop(stale_key, None)
        cached_expires_at = _LLM_PROBE_CACHE.get(cache_key)
        if cached_expires_at and float(cached_expires_at) > now:
            _LLM_PROBE_CACHE.move_to_end(cache_key)
            return True

    base_url = _normalize_base_url(opts.base_url)
    protocol_candidates = _infer_llm_protocol_candidates(opts.base_url, opts.model)
    print(
        f"[DEBUG] LLM precheck protocol candidates={protocol_candidates} "
        f"base_url={base_url} model={opts.model}"
    )
    failure_details: list[str] = []

    for protocol in protocol_candidates:
        if protocol == "responses":
            endpoint = f"{base_url.rstrip('/')}/responses"
            base_payload: dict[str, Any] = {
                "model": opts.model,
                "input": [
                    {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": "You are a connectivity probe. Reply briefly."}],
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "ping"}],
                    },
                ],
            }
            payload_candidates: list[dict[str, Any]] = []
            if opts.llm_support_json:
                json_payload = dict(base_payload)
                json_payload["text"] = {"format": {"type": "json_object"}}
                payload_candidates.append(json_payload)
            payload_candidates.append(base_payload)

            print(f"[DEBUG] LLM precheck using protocol=responses endpoint={endpoint}")
            last_status: int | None = None
            last_error = ""
            for index, payload in enumerate(payload_candidates):
                if index > 0:
                    print("[DEBUG] LLM precheck retrying Responses API with minimal payload")
                try:
                    response = requests.post(
                        endpoint,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {api_key}",
                        },
                        json=payload,
                        timeout=30,
                    )
                except Exception as exc:
                    last_status = None
                    last_error = f"request_error={str(exc)[:420]}"
                    continue
                if int(response.status_code) < 400:
                    expires_at = now + _LLM_PROBE_TTL_SECONDS
                    _cache_set(_LLM_PROBE_CACHE, cache_key, expires_at, _LLM_PROBE_CACHE_MAX)
                    print("[DEBUG] LLM precheck success protocol=responses")
                    return False
                last_status = int(response.status_code)
                last_error = f"body={str(response.text or '')[:420]}"

            status_tag = str(last_status) if last_status is not None else "request_error"
            failure_detail = f"protocol=responses; status={status_tag}; detail={last_error[:420]}"
            failure_details.append(failure_detail)
            print(f"[DEBUG] LLM precheck failed {failure_detail}")
            if _should_fallback_protocol(last_status, last_error):
                print("[DEBUG] LLM precheck falling back from responses to next protocol")
                continue
            raise PipelineError(
                "llm_precheck",
                "llm_access_denied",
                "LLM API 预检失败",
                detail="\n".join(failure_details)[:1200],
            )

        print(f"[DEBUG] LLM precheck using protocol=chat.completions base_url={base_url}")
        client = _get_llm_client(opts)
        try:
            client.chat.completions.create(
                model=opts.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                timeout=30,
            )
            expires_at = now + _LLM_PROBE_TTL_SECONDS
            _cache_set(_LLM_PROBE_CACHE, cache_key, expires_at, _LLM_PROBE_CACHE_MAX)
            print("[DEBUG] LLM precheck success protocol=chat.completions")
            return False
        except Exception as exc:
            error_text = f"request_error={str(exc)[:420]}"
            failure_detail = f"protocol=chat.completions; status=request_error; detail={error_text}"
            failure_details.append(failure_detail)
            print(f"[DEBUG] LLM precheck failed {failure_detail}")
            if _should_fallback_protocol(None, error_text):
                print("[DEBUG] LLM precheck falling back from chat.completions to next protocol")
                continue
            raise PipelineError(
                "llm_precheck",
                "llm_access_denied",
                "LLM API 预检失败",
                detail="\n".join(failure_details)[:1200],
            ) from exc

    raise PipelineError(
        "llm_precheck",
        "llm_access_denied",
        "LLM API 预检失败",
        detail="\n".join(failure_details)[:1200],
    )


def _extract_json_from_text(content: str) -> dict:
    raw = (content or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        matched = re.search(r"\{[\s\S]*\}", raw)
        if not matched:
            raise
        return json.loads(matched.group(0))


def _extract_responses_output_text(payload: dict) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    if isinstance(output_text, list):
        merged = "\n".join(str(item).strip() for item in output_text if str(item).strip())
        if merged:
            return merged

    output_items = payload.get("output")
    if not isinstance(output_items, list):
        return ""

    chunks: list[str] = []
    for item in output_items:
        if not isinstance(item, dict):
            continue
        content_items = item.get("content")
        if not isinstance(content_items, list):
            continue
        for content in content_items:
            if not isinstance(content, dict):
                continue
            text_value = content.get("text")
            if isinstance(text_value, str) and text_value.strip():
                chunks.append(text_value.strip())
                continue
            if isinstance(text_value, dict):
                nested = text_value.get("value")
                if isinstance(nested, str) and nested.strip():
                    chunks.append(nested.strip())
    return "\n".join(chunks).strip()


def _chat_json(opts: LlmOptions, prompt: str) -> dict:
    base_url = _normalize_base_url(opts.base_url)
    protocol_candidates = _infer_llm_protocol_candidates(opts.base_url, opts.model)
    print(
        f"[DEBUG] LLM JSON protocol candidates={protocol_candidates} "
        f"base_url={base_url} model={opts.model}"
    )
    failure_details: list[str] = []

    for protocol in protocol_candidates:
        if protocol == "responses":
            endpoint = f"{base_url.rstrip('/')}/responses"
            base_payload: dict[str, Any] = {
                "model": opts.model,
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": prompt}],
                    }
                ],
            }

            print(f"[DEBUG] LLM JSON request using protocol=responses endpoint={endpoint}")
            payload_candidates: list[dict[str, Any]] = []
            if opts.llm_support_json:
                json_payload = dict(base_payload)
                json_payload["text"] = {"format": {"type": "json_object"}}
                payload_candidates.append(json_payload)
            payload_candidates.append(base_payload)

            last_status: int | None = None
            last_error = ""
            for index, payload in enumerate(payload_candidates):
                if index > 0:
                    print("[DEBUG] LLM JSON retrying Responses API with minimal payload")
                try:
                    response = requests.post(
                        endpoint,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {(opts.api_key or '').strip()}",
                        },
                        json=payload,
                        timeout=180,
                    )
                except Exception as exc:
                    last_status = None
                    last_error = f"request_error={str(exc)[:420]}"
                    continue

                if int(response.status_code) >= 400:
                    last_status = int(response.status_code)
                    last_error = f"body={str(response.text or '')[:600]}"
                    continue

                try:
                    response_payload = response.json()
                except Exception as exc:
                    raise PipelineError(
                        "llm",
                        "llm_invalid_json",
                        "LLM Responses 返回非 JSON",
                        detail=str(response.text or "")[:600],
                    ) from exc
                if not isinstance(response_payload, dict):
                    raise PipelineError("llm", "llm_invalid_json", "LLM Responses 返回结构异常", detail=str(response_payload)[:600])

                content = _extract_responses_output_text(response_payload)
                if not content:
                    raise PipelineError("llm", "llm_invalid_json", "LLM Responses 未返回文本", detail=str(response_payload)[:600])
                try:
                    prompt_tokens, completion_tokens, total_tokens, provider_request_id = _extract_usage_from_response_payload(
                        response_payload
                    )
                    if not provider_request_id:
                        try:
                            provider_request_id = str((response.headers or {}).get("x-request-id") or "").strip()
                        except Exception:
                            provider_request_id = ""
                    _append_llm_usage_sample(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        provider_request_id=provider_request_id,
                    )
                    print("[DEBUG] LLM JSON success protocol=responses")
                    return _extract_json_from_text(content)
                except Exception as exc:
                    raise PipelineError("llm", "llm_invalid_json", "LLM 返回非预期 JSON", detail=content[:600]) from exc

            status_tag = str(last_status) if last_status is not None else "request_error"
            failure_detail = f"protocol=responses; status={status_tag}; detail={last_error[:600]}"
            failure_details.append(failure_detail)
            print(f"[DEBUG] LLM JSON failed {failure_detail}")
            if _should_fallback_protocol(last_status, last_error):
                print("[DEBUG] LLM JSON falling back from responses to next protocol")
                continue
            raise PipelineError(
                "llm",
                "llm_request_failed",
                f"LLM 请求失败（protocol=responses; status={status_tag}）",
                detail="\n".join(failure_details)[:1200],
            )

        client = _get_llm_client(opts)
        params = {
            "model": opts.model,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": 180,
        }
        if opts.llm_support_json:
            params["response_format"] = {"type": "json_object"}

        print(f"[DEBUG] LLM JSON request using protocol=chat.completions base_url={base_url}")
        try:
            resp = client.chat.completions.create(**params)
        except Exception as exc:
            error_text = f"request_error={str(exc)[:420]}"
            failure_detail = f"protocol=chat.completions; status=request_error; detail={error_text}"
            failure_details.append(failure_detail)
            print(f"[DEBUG] LLM JSON failed {failure_detail}")
            if _should_fallback_protocol(None, error_text):
                print("[DEBUG] LLM JSON falling back from chat.completions to next protocol")
                continue
            raise PipelineError("llm", "llm_request_failed", "LLM 请求失败", detail="\n".join(failure_details)[:1200]) from exc
        content = str(resp.choices[0].message.content or "")
        try:
            prompt_tokens, completion_tokens, total_tokens, provider_request_id = _extract_usage_from_chat_response(resp)
            _append_llm_usage_sample(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                provider_request_id=provider_request_id,
            )
            print("[DEBUG] LLM JSON success protocol=chat.completions")
            return _extract_json_from_text(content)
        except Exception as exc:
            raise PipelineError("llm", "llm_invalid_json", "LLM 返回非预期 JSON", detail=content[:600]) from exc

    raise PipelineError("llm", "llm_request_failed", "LLM 请求失败", detail="\n".join(failure_details)[:1200])


def _meaning_split_sentences(
    sentences: list[dict],
    llm_opts: LlmOptions,
    should_cancel: CancelCheck | None = None,
) -> list[dict]:
    out: list[dict] = []
    for item in sentences:
        _raise_if_cancel_requested(should_cancel)
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        translation = str(item.get("translation") or "").strip()
        words = re.findall(r"\S+", text)
        if len(words) <= 20:
            row = {"text": text}
            if translation:
                row["translation"] = translation
            out.append(row)
            continue
        if len(words) <= 28:
            rule_parts = _rule_split_sentence_parts(text)
            if len(rule_parts) < 2:
                row = {"text": text}
                if translation:
                    row["translation"] = translation
                out.append(row)
                continue
            for idx, part in enumerate(rule_parts):
                row = {"text": str(part).strip()}
                if translation and idx == 0:
                    row["translation"] = translation
                out.append(row)
            continue

        prompt = (
            "请把下面一句字幕按语义切成 2~4 段，保持原词序，不要改写。"
            "返回 JSON：{\"parts\": [\"段1\", \"段2\"]}\n"
            f"字幕：{text}"
        )
        payload = _chat_json(llm_opts, prompt)
        parts = payload.get("parts")
        if not isinstance(parts, list) or len(parts) < 2:
            raise PipelineError("meaning_split", "meaning_split_invalid", "语义分句返回格式错误", detail=str(payload))
        normalized_parts = [str(x).strip() for x in parts if str(x).strip()]
        if len(normalized_parts) < 2:
            row = {"text": text}
            if translation:
                row["translation"] = translation
            out.append(row)
            continue

        for idx, part in enumerate(normalized_parts):
            row = {"text": str(part).strip()}
            if translation and idx == 0:
                row["translation"] = translation
            out.append(row)
        _raise_if_cancel_requested(should_cancel)
    return [item for item in out if str(item.get("text") or "").strip()]


def _rule_split_sentence_parts(text: str) -> list[str]:
    clean = re.sub(r"\s+", " ", str(text or "").strip())
    if not clean:
        return []
    if " " not in clean and not re.search(r"[，,。！？!?；;：:]", clean):
        return [clean]

    midpoint = len(clean) // 2
    punct_positions = [match.end() for match in re.finditer(r"[，,。！？!?；;：:]", clean)]
    split_index = 0
    if punct_positions:
        split_index = min(punct_positions, key=lambda pos: abs(pos - midpoint))
    else:
        for match in re.finditer(r"\s+", clean):
            position = match.start()
            if split_index == 0 or abs(position - midpoint) < abs(split_index - midpoint):
                split_index = position

    if split_index <= 0 or split_index >= len(clean):
        return [clean]

    left = clean[:split_index].strip()
    right = clean[split_index:].strip()
    if not left or not right:
        return [clean]
    return [left, right]


def _calc_weighted_text_length(text: str) -> float:
    value = str(text or "")

    def _char_weight(char: str) -> float:
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

    return sum(_char_weight(char) for char in value)


def _needs_subtitle_secondary_split(text: str, translation: str) -> bool:
    source_len = len(str(text or ""))
    if source_len > _SUBTITLE_MAX_LENGTH:
        return True
    target_len = _calc_weighted_text_length(str(translation or ""))
    return target_len * _SUBTITLE_TARGET_MULTIPLIER > _SUBTITLE_MAX_LENGTH


def _align_translation_parts_with_llm(
    *,
    source_text: str,
    translation: str,
    source_parts: list[str],
    llm_opts: LlmOptions,
    should_cancel: CancelCheck | None = None,
) -> list[str]:
    if not source_parts:
        return []
    if len(source_parts) == 1:
        return [str(translation or "").strip()]
    if not str(translation or "").strip():
        return [""] * len(source_parts)

    _raise_if_cancel_requested(should_cancel)
    prompt = (
        "你是字幕对齐助手。请将整句译文按原文拆分片段进行对齐切分。"
        "要求：不改写原意；返回段数必须与 source_parts 完全一致；仅返回 JSON。"
        "格式：{\"parts\":[\"译文片段1\",\"译文片段2\"]}\n"
        f"source_text: {source_text}\n"
        f"source_parts: {json.dumps(source_parts, ensure_ascii=False)}\n"
        f"translation: {translation}"
    )
    payload = _chat_json(llm_opts, prompt)
    parts = payload.get("parts")
    if not isinstance(parts, list):
        raise PipelineError(
            "translation",
            "subtitle_split_align_invalid",
            "长字幕二次拆分失败：译文分段格式不正确",
            detail=str(payload)[:600],
        )
    normalized = [str(item).strip() for item in parts]
    if len(normalized) != len(source_parts):
        raise PipelineError(
            "translation",
            "subtitle_split_align_invalid",
            "长字幕二次拆分失败：译文分段数量与原文不一致",
            detail=json.dumps(
                {
                    "expected_parts": len(source_parts),
                    "actual_parts": len(normalized),
                },
                ensure_ascii=False,
            ),
        )
    if any(part == "" for part in normalized):
        raise PipelineError(
            "translation",
            "subtitle_split_align_invalid",
            "长字幕二次拆分失败：译文分段包含空文本",
            detail=str(payload)[:600],
        )
    return normalized


def _split_long_subtitle_rows(
    rows: list[dict],
    llm_opts: LlmOptions,
    should_cancel: CancelCheck | None = None,
) -> list[dict]:
    current: list[dict] = []
    for row in rows or []:
        text = str((row or {}).get("text") or "").strip()
        if not text:
            continue
        current.append(
            {
                "text": text,
                "translation": str((row or {}).get("translation") or "").strip(),
            }
        )

    for _ in range(3):
        changed = False
        next_rows: list[dict] = []
        for row in current:
            _raise_if_cancel_requested(should_cancel)
            text = str(row.get("text") or "").strip()
            translation = str(row.get("translation") or "").strip()
            if not _needs_subtitle_secondary_split(text, translation):
                next_rows.append({"text": text, "translation": translation})
                continue
            source_parts = _rule_split_sentence_parts(text)
            if len(source_parts) < 2:
                next_rows.append({"text": text, "translation": translation})
                continue
            translation_parts = _align_translation_parts_with_llm(
                source_text=text,
                translation=translation,
                source_parts=source_parts,
                llm_opts=llm_opts,
                should_cancel=should_cancel,
            )
            changed = True
            for idx, part in enumerate(source_parts):
                next_rows.append(
                    {
                        "text": str(part).strip(),
                        "translation": str(translation_parts[idx] if idx < len(translation_parts) else "").strip(),
                    }
                )
        current = [row for row in next_rows if str(row.get("text") or "").strip()]
        if not changed:
            break
    return current


def _build_translation_batches(
    texts: list[str],
    *,
    max_items: int,
    max_chars: int,
    min_items: int,
) -> list[tuple[int, int]]:
    if not texts:
        return []
    total = len(texts)
    batches: list[tuple[int, int]] = []
    cursor = 0
    normalized_max_items = max(1, int(max_items))
    normalized_max_chars = max(1, int(max_chars))
    normalized_min_items = max(1, min(int(min_items), normalized_max_items))
    while cursor < total:
        start = cursor
        chars_in_batch = 0
        while cursor < total:
            item_chars = len(str(texts[cursor] or ""))
            current_count = cursor - start
            next_count = current_count + 1
            exceeds_item_limit = next_count > normalized_max_items
            exceeds_char_limit = (chars_in_batch + item_chars) > normalized_max_chars
            if exceeds_item_limit:
                break
            if exceeds_char_limit and current_count >= normalized_min_items:
                break
            chars_in_batch += item_chars
            cursor += 1
        if cursor == start:
            cursor += 1
        batches.append((start, cursor))
    return batches


def _translate_sentences(
    texts: list[str],
    source_language: str,
    target_language: str,
    llm_opts: LlmOptions,
    progress_callback: Callable[[int, int], None] | None = None,
    should_cancel: CancelCheck | None = None,
) -> tuple[list[str], int]:
    if not texts:
        return [], 0

    translations = [""] * len(texts)
    batches = _build_translation_batches(
        texts,
        max_items=28,
        max_chars=2600,
        min_items=8,
    )
    for start, end in batches:
        _raise_if_cancel_requested(should_cancel)
        batch = texts[start:end]
        payload = {f"id_{idx}": text for idx, text in enumerate(batch)}
        prompt = (
            f"你是字幕翻译助手。把以下 {source_language} 字幕翻译成 {target_language}。"
            "只返回 JSON，键必须与输入完全一致，值为翻译文本。\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )
        data = _chat_json(llm_opts, prompt)
        for idx in range(len(batch)):
            value = data.get(f"id_{idx}", "")
            translations[start + idx] = str(value or "").strip()
        if progress_callback:
            progress_callback(end, len(texts))
        _raise_if_cancel_requested(should_cancel)
    return translations, len(batches)


def _refine_translation_pass(
    *,
    texts: list[str],
    translations: list[str],
    source_language: str,
    target_language: str,
    llm_opts: LlmOptions,
    should_cancel: CancelCheck | None = None,
) -> tuple[list[str], bool]:
    if not texts or not translations:
        return translations, False
    if len(translations) != len(texts):
        return translations, False

    improved = list(translations)
    touched = False
    batch_size = 12
    for start in range(0, len(texts), batch_size):
        _raise_if_cancel_requested(should_cancel)
        end = min(len(texts), start + batch_size)
        batch_rows = []
        for idx in range(start, end):
            batch_rows.append(
                {
                    "id": f"id_{idx - start}",
                    "source": texts[idx],
                    "translation": translations[idx],
                }
            )
        prompt = (
            f"你是字幕润色助手。下面是 {source_language} 到 {target_language} 的字幕翻译结果。"
            "请先检查是否忠实原文，再在不增删事实的前提下做更自然的口语化改写。"
            "输出必须是 JSON 对象，键必须与输入 id 完全一致，值为润色后的翻译。"
            "如果原翻译已经很好，也要返回原文本。\n"
            f"{json.dumps(batch_rows, ensure_ascii=False)}"
        )
        try:
            data = _chat_json(llm_opts, prompt)
            batch_success = True
            for local_idx in range(0, end - start):
                key = f"id_{local_idx}"
                value = str(data.get(key) or "").strip()
                if not value:
                    batch_success = False
                    break
                improved[start + local_idx] = value
            if batch_success:
                touched = True
        except PipelineError:
            continue
        except Exception:
            continue

        _raise_if_cancel_requested(should_cancel)
    return improved, touched


def _split_long_text_for_single_line(text: str, max_chars: int) -> list[str]:
    safe_text = re.sub(r"\s+", " ", str(text or "").strip())
    if not safe_text:
        return []
    if len(safe_text) <= max_chars:
        return [safe_text]

    protected_map: dict[str, str] = {}

    def protect(pattern: str, source: str, token_prefix: str) -> str:
        counter = len(protected_map)

        def _repl(match: re.Match[str]) -> str:
            nonlocal counter
            token = f"__{token_prefix}_{counter}__"
            counter += 1
            protected_map[token] = match.group(0)
            return token

        return re.sub(pattern, _repl, source)

    protected = safe_text
    protected = protect(r"https?://\S+", protected, "URL")
    protected = protect(r"(?:[A-Za-z]\.){2,}", protected, "ABBR")
    protected = protect(r"\b\d+(?:[.,:/-]\d+)*\b", protected, "NUM")

    punctuation_parts = [item.strip() for item in re.split(r"(?<=[。！？!?;；，,])\s*", protected) if item.strip()]
    if not punctuation_parts:
        punctuation_parts = [protected]

    output: list[str] = []
    for part in punctuation_parts:
        if len(part) <= max_chars:
            output.append(part)
            continue
        words = part.split(" ")
        if len(words) <= 1:
            output.append(part)
            continue
        bucket = ""
        for word in words:
            candidate = f"{bucket} {word}".strip()
            if bucket and len(candidate) > max_chars:
                output.append(bucket)
                bucket = word
            else:
                bucket = candidate
        if bucket:
            output.append(bucket)

    restored: list[str] = []
    for item in output:
        result = item
        for token, raw_value in protected_map.items():
            result = result.replace(token, raw_value)
        restored_value = result.strip()
        if restored_value:
            restored.append(restored_value)
    return restored or [safe_text]


def _split_text_timeline(
    *,
    start: float,
    end: float,
    parts: list[str],
    translation: str,
) -> list[dict]:
    if not parts:
        return []
    if len(parts) == 1:
        return [
            {
                "start": start,
                "end": max(end, start + 0.3),
                "text": parts[0],
                "translation": translation,
            }
        ]
    total_chars = max(1, sum(len(item) for item in parts))
    cursor = start
    duration = max(0.5, end - start)
    rows: list[dict] = []
    for idx, part in enumerate(parts):
        if idx == len(parts) - 1:
            next_cursor = end
        else:
            next_cursor = cursor + duration * (len(part) / total_chars)
        if next_cursor <= cursor:
            next_cursor = cursor + 0.3
        rows.append(
            {
                "start": round(cursor, 3),
                "end": round(next_cursor, 3),
                "text": part,
                "translation": translation if idx == 0 else "",
            }
        )
        cursor = next_cursor
    return rows


def _apply_single_line_strategy(sentences: list[dict]) -> tuple[list[dict], bool]:
    if not sentences:
        return sentences, False
    changed = False
    optimized: list[dict] = []
    for row in sentences:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        start = float(row.get("start") or 0.0)
        end = float(row.get("end") or (start + 0.8))
        if end <= start:
            end = start + 0.5
        translation = str(row.get("translation") or "").strip()

        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", text))
        max_chars = 24 if has_cjk else 56
        parts = _split_long_text_for_single_line(text, max_chars=max_chars)
        if len(parts) > 1:
            changed = True
        optimized.extend(
            _split_text_timeline(
                start=start,
                end=end,
                parts=parts,
                translation=translation,
            )
        )
    normalized = _normalize_sentence_timeline(optimized)
    return normalized, changed


def _normalize_sentence_timeline(sentences: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for row in sentences:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        start = _to_finite_float(row.get("start"))
        end = _to_finite_float(row.get("end"))
        if start is None:
            start = 0.0
        if end is None:
            end = start
        start = max(0.0, float(start))
        end = max(start, float(end))
        item: dict[str, Any] = {
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
        }
        translation = str(row.get("translation") or "").strip()
        if translation:
            item["translation"] = translation
        normalized.append(item)
    return normalized


def _format_srt_time(seconds: float) -> str:
    millis = int(round(max(0.0, seconds) * 1000))
    hours = millis // 3600000
    minutes = (millis % 3600000) // 60000
    secs = (millis % 60000) // 1000
    ms = millis % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _build_srt(sentences: list[dict], include_translation: bool) -> str:
    blocks: list[str] = []
    for index, row in enumerate(sentences, start=1):
        text = str(row.get("text") or "").strip()
        trans = str(row.get("translation") or "").strip()
        if include_translation and trans:
            body = f"{text}\n{trans}".strip()
        else:
            body = text
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{_format_srt_time(float(row['start']))} --> {_format_srt_time(float(row['end']))}",
                    body,
                ]
            )
        )
    return "\n\n".join(blocks).strip()


def _save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_rmtree(path: str | Path) -> None:
    target = Path(path)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
