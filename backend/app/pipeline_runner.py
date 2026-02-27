from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from vendor.videolingo_subtitle_core import PipelineError, run_llm_postprocess, run_pipeline
from app.translation_model_bridge import QWEN_MT_FLASH_MODEL, qwen_mt_bridge_session


ProgressCallback = Callable[[int, str, str, dict[str, Any] | None], None]
CancelCheck = Callable[[], bool]


def _resolve_requested_llm_model(options: dict[str, Any] | None) -> str:
    safe_options = options if isinstance(options, dict) else {}
    llm = safe_options.get("llm") if isinstance(safe_options.get("llm"), dict) else {}
    return str(llm.get("model") or "").strip()


def _resolve_requested_llm_base_url(options: dict[str, Any] | None) -> str:
    safe_options = options if isinstance(options, dict) else {}
    llm = safe_options.get("llm") if isinstance(safe_options.get("llm"), dict) else {}
    return str(llm.get("base_url") or "").strip()


def _infer_llm_provider(base_url: str) -> str:
    safe_base_url = str(base_url or "").strip()
    if not safe_base_url:
        return ""
    try:
        parsed = urlparse(safe_base_url if "://" in safe_base_url else f"https://{safe_base_url}")
        host = str(parsed.netloc or "").lower().strip()
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


def _attach_translation_stats(
    *,
    result: dict[str, Any] | None,
    options: dict[str, Any] | None,
    bridge_enabled: bool,
    bridge_stats: dict[str, Any],
) -> dict[str, Any]:
    safe_result = result if isinstance(result, dict) else {}
    stats = safe_result.get("stats")
    if not isinstance(stats, dict):
        stats = {}
        safe_result["stats"] = stats

    requested_model = _resolve_requested_llm_model(options)
    requested_base_url = _resolve_requested_llm_base_url(options)
    if bridge_enabled:
        stats["translation_mode_effective"] = "translation_model"
        stats["translation_provider_effective"] = str(bridge_stats.get("translation_provider_effective") or "")
        stats["translation_model_effective"] = str(
            bridge_stats.get("translation_model_effective") or requested_model or QWEN_MT_FLASH_MODEL
        )
        stats["translation_prompt_tokens"] = int(bridge_stats.get("translation_prompt_tokens") or 0)
        stats["translation_completion_tokens"] = int(bridge_stats.get("translation_completion_tokens") or 0)
        stats["translation_total_tokens"] = int(bridge_stats.get("translation_total_tokens") or 0)
        stats["translation_request_count"] = int(bridge_stats.get("translation_request_count") or 0)
    else:
        stats.setdefault("translation_mode_effective", "llm_model")
        resolved_provider = str(stats.get("llm_provider_effective") or _infer_llm_provider(requested_base_url))
        resolved_model = str(stats.get("llm_model_effective") or requested_model)
        stats.setdefault("translation_provider_effective", resolved_provider)
        stats.setdefault("translation_model_effective", resolved_model)
        stats.setdefault("translation_prompt_tokens", 0)
        stats.setdefault("translation_completion_tokens", 0)
        stats.setdefault("translation_total_tokens", 0)
        stats.setdefault("translation_request_count", 0)
    return safe_result


def run_subtitle_pipeline(
    video_path: str,
    work_dir: str,
    options: dict,
    progress: ProgressCallback,
    should_cancel: CancelCheck | None = None,
) -> dict:
    try:
        with qwen_mt_bridge_session(options) as bridge:
            result = run_pipeline(
                video_path=video_path,
                work_dir=work_dir,
                options_payload=options,
                progress=progress,
                should_cancel=should_cancel,
            )
            return _attach_translation_stats(
                result=result,
                options=options,
                bridge_enabled=bridge.enabled,
                bridge_stats=bridge.get_usage_stats(),
            )
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(
            stage="pipeline",
            code="pipeline_unexpected_error",
            message="字幕流水线执行失败",
            detail=f"{exc}\n{traceback.format_exc(limit=5)}",
        ) from exc


def run_llm_resume_pipeline(
    sentences: list[dict],
    options: dict,
    word_segments: list[dict],
    progress: ProgressCallback,
    should_cancel: CancelCheck | None = None,
) -> dict:
    try:
        with qwen_mt_bridge_session(options) as bridge:
            result = run_llm_postprocess(
                sentences=sentences,
                options_payload=options,
                progress=progress,
                should_cancel=should_cancel,
                word_segments=word_segments,
            )
            return _attach_translation_stats(
                result=result,
                options=options,
                bridge_enabled=bridge.enabled,
                bridge_stats=bridge.get_usage_stats(),
            )
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(
            stage="llm",
            code="pipeline_unexpected_error",
            message="字幕续跑失败",
            detail=f"{exc}\n{traceback.format_exc(limit=5)}",
        ) from exc


def validate_video_file(path: str) -> None:
    target = Path(path)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"Video file not found: {path}")
    if target.stat().st_size <= 0:
        raise ValueError("Video file is empty")
