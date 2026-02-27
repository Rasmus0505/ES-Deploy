from __future__ import annotations

import importlib
import hashlib
import json
import mimetypes
import os
import re
import shutil
import traceback
import uuid
import wave
from io import BytesIO
from pathlib import Path
from time import time
from urllib.parse import urlparse

import requests
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openai import OpenAI

from app.auth_service import AuthError, AuthPrincipal, AuthService
from app.history_store import SqliteHistoryStore
from app.job_manager import SubtitleJobManager
from app.llm_cost_ledger import append_llm_cost_record
from app.provider_url_rules import (
    DEFAULT_LLM_BASE_URL,
    extract_responses_output_text as _extract_responses_output_text,
    infer_llm_protocol_candidates as _infer_llm_protocol_candidates,
    normalize_llm_base_url as _normalize_llm_base_url,
    normalize_whisper_base_url as _normalize_whisper_base_url,
    should_fallback_protocol as _should_fallback_protocol,
)
from app.reading_pipeline import PIPELINE_VERSION as READING_PIPELINE_VERSION
from app.reading_pipeline import QUALITY_RETRY_BUDGET as READING_QUALITY_RETRY_BUDGET
from app.reading_pipeline import QUALITY_STRATEGY_TAG as READING_QUALITY_STRATEGY_TAG
from app.reading_pipeline import ReadingPipelineError, generate_reading_material, grade_short_answer
from app.reading_store import SqliteReadingStore
from app.security_crypto import mask_secret
from app.url_ingest import evaluate_source_url_policy, normalize_source_url
from app.schemas import (
    AuthLoginRequest,
    AuthLogoutResponse,
    AuthRegisterRequest,
    AuthTokenResponse,
    AuthUserResponse,
    BrowserErrorReadResponse,
    BrowserErrorReportRequest,
    BrowserErrorReportResponse,
    DeleteOperationResponse,
    HealthResponse,
    HistoryRecordsResponse,
    HistoryRecordsSyncResponse,
    HistoryRecordsUpsertRequest,
    JobCreateResponse,
    JobStatusResponse,
    LlmOptions,
    ProfileSettings,
    ProfileKeysUpdateRequest,
    ProfileKeysUpdateResponse,
    ProfileSettingsUpdateRequest,
    ReadingHistoryItem,
    ReadingHistoryResponse,
    ReadingMaterialGenerateRequest,
    ReadingMaterialResponse,
    ReadingShortAnswerHistoryDeleteRequest,
    ReadingShortAnswerHistoryResponse,
    ReadingShortAnswerSubmitRequest,
    ReadingShortAnswerSubmitResponse,
    ReadingSourcesResponse,
    ReadingVersionResponse,
    SubtitleConfigProbeResult,
    SubtitleConfigTestResponse,
    SubtitleJobFromUrlRequest,
    SubtitleJobOptions,
    WalletPackItem,
    WalletPacksResponse,
    WalletQuotaResponse,
    WalletRedeemRequest,
    WalletRedeemResponse,
    WhisperLocalModelsResponse,
    WhisperLocalModelStatus,
)
from vendor.videolingo_subtitle_core.engine import (
    PipelineError,
    WhisperOptions as EngineWhisperOptions,
    _transcribe_paraformer_v2 as _engine_transcribe_paraformer_v2,
    _transcribe_qwen3_asr_flash_filetrans as _engine_transcribe_qwen3_asr_flash_filetrans,
)


APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_ROOT.parent
RUNTIME_JOBS_ROOT = APP_ROOT / "runtime" / "jobs"
RUNTIME_JOBS_ROOT.mkdir(parents=True, exist_ok=True)
BROWSER_ERROR_ROOT = APP_ROOT / "runtime" / "browser-errors"
BROWSER_ERROR_ROOT.mkdir(parents=True, exist_ok=True)
LOCAL_DATA_ROOT = PROJECT_ROOT / "data"
LOCAL_DATA_ROOT.mkdir(parents=True, exist_ok=True)
LOCAL_SQLITE_DB = LOCAL_DATA_ROOT / "app.db"
DEFAULT_BACKEND_ERROR_FILE = os.getenv("BACKEND_ERROR_FILE", "调试信息/浏览器报错.md")
DEFAULT_PROFILE_LLM_MODEL = "gpt-5.2"
READING_CACHE_LLM_SIGNATURE_KEY = "_llm_signature"
READING_CACHE_QUALITY_STRATEGY_KEY = "quality_strategy"
READING_CACHE_QUALITY_RETRY_KEY = "quality_retry_budget"
BROWSER_ERRORS_DEPRECATION_HEADER = "browser-errors endpoints will be removed after 14-day grace period"
BROWSER_ERRORS_DEPRECATION_END_DATE = "2026-03-12"
DEFAULT_CORS_ALLOW_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8510",
    "http://127.0.0.1:8510",
)
DEFAULT_GLOBAL_JOB_LIMIT = 3
DEFAULT_USER_JOB_LIMIT = 1
ONEAPI_BASE_URL = str(os.getenv("ONEAPI_BASE_URL", "http://127.0.0.1:3000")).strip().rstrip("/")
ONEAPI_API_PREFIX = str(os.getenv("ONEAPI_API_PREFIX", "/api")).strip() or "/api"
if not ONEAPI_API_PREFIX.startswith("/"):
    ONEAPI_API_PREFIX = f"/{ONEAPI_API_PREFIX}"
ONEAPI_V1_BASE_URL = str(os.getenv("ONEAPI_V1_BASE_URL", "")).strip() or f"{ONEAPI_BASE_URL}/v1"
WALLET_COST_MULTIPLIER = max(0.01, float(os.getenv("WALLET_COST_MULTIPLIER", "3") or 3))
DEFAULT_WALLET_PACKS = [
    {"id": "trial", "label": "试用包", "price": 0.9, "quota": 90000, "description": "低门槛快速体验"},
    {"id": "standard", "label": "标准包", "price": 9.9, "quota": 990000, "description": "日常高频学习"},
    {"id": "pro", "label": "进阶包", "price": 99.0, "quota": 9900000, "description": "长期稳定使用"},
]


def _resolve_cors_allow_origins() -> list[str]:
    raw = str(os.getenv("CORS_ALLOW_ORIGINS", "")).strip()
    if not raw:
        return list(DEFAULT_CORS_ALLOW_ORIGINS)
    values = [str(item or "").strip() for item in raw.split(",")]
    deduped: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped or list(DEFAULT_CORS_ALLOW_ORIGINS)


def _now_ms() -> int:
    return int(round(time() * 1000))


def _resolve_wallet_packs() -> list[dict]:
    raw = str(os.getenv("WALLET_PACKS_JSON", "")).strip()
    if not raw:
        return list(DEFAULT_WALLET_PACKS)
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        print(f"[DEBUG] Invalid WALLET_PACKS_JSON, fallback to defaults: {exc}")
        return list(DEFAULT_WALLET_PACKS)
    if not isinstance(parsed, list):
        return list(DEFAULT_WALLET_PACKS)
    normalized: list[dict] = []
    for idx, item in enumerate(parsed):
        if not isinstance(item, dict):
            continue
        try:
            price = float(item.get("price") or 0)
            quota = int(item.get("quota") or 0)
        except Exception:
            continue
        if price <= 0 or quota <= 0:
            continue
        normalized.append(
            {
                "id": str(item.get("id") or f"pack_{idx + 1}").strip() or f"pack_{idx + 1}",
                "label": str(item.get("label") or f"套餐{idx + 1}").strip() or f"套餐{idx + 1}",
                "price": round(price, 2),
                "quota": quota,
                "description": str(item.get("description") or "").strip(),
            }
        )
    return normalized or list(DEFAULT_WALLET_PACKS)


WALLET_PACKS = _resolve_wallet_packs()


def _build_oneapi_user_llm_payload(*, access_token: str, raw_llm: dict | None = None) -> dict:
    safe = _sanitize_llm_options_payload(raw_llm if isinstance(raw_llm, dict) else None)
    return _sanitize_llm_options_payload(
        {
            "base_url": ONEAPI_V1_BASE_URL,
            "api_key": str(access_token or "").strip(),
            "model": str(safe.get("model") or DEFAULT_PROFILE_LLM_MODEL).strip() or DEFAULT_PROFILE_LLM_MODEL,
            "llm_support_json": bool(safe.get("llm_support_json", False)),
        }
    )


def _inject_job_llm_options_from_principal(*, options: dict, principal: AuthPrincipal) -> dict:
    safe_options = dict(options if isinstance(options, dict) else {})
    llm_raw = safe_options.get("llm") if isinstance(safe_options.get("llm"), dict) else {}
    safe_options["llm"] = _build_oneapi_user_llm_payload(
        access_token=principal.access_token,
        raw_llm=llm_raw if isinstance(llm_raw, dict) else None,
    )
    return safe_options


app = FastAPI(title="Listening Subtitle Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_resolve_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

history_store = SqliteHistoryStore(db_path=str(LOCAL_SQLITE_DB))
reading_store = SqliteReadingStore(db_path=str(LOCAL_SQLITE_DB))
profile_store = reading_store
auth_service = AuthService()
job_manager = SubtitleJobManager(
    runtime_root=str(RUNTIME_JOBS_ROOT),
    db_path=str(LOCAL_SQLITE_DB),
    global_concurrency_limit=max(1, int(os.getenv("SUBTITLE_GLOBAL_CONCURRENCY", str(DEFAULT_GLOBAL_JOB_LIMIT)))),
    per_user_concurrency_limit=max(1, int(os.getenv("SUBTITLE_PER_USER_CONCURRENCY", str(DEFAULT_USER_JOB_LIMIT)))),
)
LOCAL_WHISPER_MODEL_CANDIDATES = ("tiny", "base", "small", "medium", "large-v3")


def _safe_filename(filename: str) -> str:
    value = (filename or "video.mp4").strip()
    value = re.sub(r"[^\w\.\-\u4e00-\u9fff]", "_", value)
    if not value:
        value = "video.mp4"
    return value


def _validate_subtitle_job_options(options: SubtitleJobOptions) -> dict:
    options_dict = options.model_dump()
    whisper_runtime = (options.whisper.runtime or "cloud").strip().lower()
    whisper_model = (options.whisper.model or "paraformer-v2").strip() or "paraformer-v2"
    whisper_model_key = whisper_model.lower()
    cloud_only_models = {
        "paraformer-v2",
        "qwen3-asr-flash-filetrans",
        "distil-large-v2",
        "large-v3-turbo",
        "whisper-large-v3-turbo",
        "whisper-large-v3",
        "whisper-1",
        "whisperx",
    }
    if whisper_runtime == "local" and whisper_model_key in cloud_only_models:
        detail = {
            "code": "invalid_whisper_model",
            "runtime": whisper_runtime,
            "model": whisper_model,
            "message": "local 模式不支持该 Whisper 模型，请切换为 cloud 或选择本地可用模型",
        }
        raise HTTPException(status_code=400, detail=detail)
    return options_dict


def _build_active_job_exists_detail(active_job) -> dict:
    cancel_requested = bool(getattr(active_job, "cancel_requested", False))
    return {
        "code": "active_job_exists",
        "active_job_id": active_job.job_id,
        "active_job_status": active_job.status,
        "cancel_requested": cancel_requested,
        "message": "已有字幕任务在执行，请等待当前任务结束后重试"
        if not cancel_requested
        else "已有字幕任务正在取消中，请等待取消完成后重试",
    }


def _deprecated_simplified_only_detail() -> dict:
    return {
        "code": "deprecated_simplified_only",
        "message": "旧恢复接口已下线，请使用创建任务或“重新生成听力”发起新的简化流程任务",
    }


def _resolve_error_file(file_name: str) -> Path:
    value = (file_name or "browser-error.log").strip().replace("\\", "/")
    value = re.sub(r"[^0-9A-Za-z_\-./\u4e00-\u9fff]", "_", value)
    value = value.lstrip("/")
    if not value:
        value = "browser-error.log"
    safe_path = (BROWSER_ERROR_ROOT / value).resolve()
    if not str(safe_path).startswith(str(BROWSER_ERROR_ROOT.resolve())):
        raise HTTPException(status_code=400, detail="Invalid file_name path")
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    return safe_path


def _append_runtime_error_report(content: str, file_name: str = "browser-error.log") -> None:
    target_path = _resolve_error_file(file_name)
    with target_path.open("a", encoding="utf-8") as stream:
        stream.write(content.rstrip() + "\n")
    print(f"[DEBUG] Appended runtime error report: {target_path}")


def _mark_browser_errors_deprecated(response: Response) -> None:
    response.headers["X-Deprecated"] = BROWSER_ERRORS_DEPRECATION_HEADER


def _safe_positive_int(value) -> int:
    try:
        parsed = int(value)
    except Exception:
        return 0
    return parsed if parsed > 0 else 0


def _auth_http_exception(exc: AuthError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.to_detail())


def _require_principal(request: Request) -> AuthPrincipal:
    try:
        return auth_service.authenticate_request(request)
    except AuthError as exc:
        raise _auth_http_exception(exc) from exc


def _to_wallet_quota_response(payload: dict) -> WalletQuotaResponse:
    safe = payload if isinstance(payload, dict) else {}
    return WalletQuotaResponse.model_validate(
        {
            "user_id": str(safe.get("user_id") or ""),
            "username": str(safe.get("username") or ""),
            "quota": int(safe.get("quota") or 0),
            "used_quota": int(safe.get("used_quota") or 0),
            "remaining_quota": int(safe.get("remaining_quota") or 0),
            "request_count": int(safe.get("request_count") or 0),
        }
    )


def _to_wallet_packs_response() -> WalletPacksResponse:
    packs = [WalletPackItem.model_validate(item).model_dump() for item in WALLET_PACKS]
    return WalletPacksResponse.model_validate(
        {
            "packs": packs,
            "cost_multiplier": WALLET_COST_MULTIPLIER,
        }
    )


def _infer_llm_provider_effective(base_url: str) -> str:
    normalized = _normalize_llm_base_url(base_url)
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


def _extract_llm_usage_from_payload(payload: dict | None) -> dict:
    safe_payload = payload if isinstance(payload, dict) else {}
    usage = safe_payload.get("usage") if isinstance(safe_payload.get("usage"), dict) else {}
    prompt_tokens = _safe_positive_int(usage.get("input_tokens") or usage.get("prompt_tokens"))
    completion_tokens = _safe_positive_int(usage.get("output_tokens") or usage.get("completion_tokens"))
    total_tokens = _safe_positive_int(usage.get("total_tokens"))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "llm_request_count": 1 if (prompt_tokens > 0 or completion_tokens > 0 or total_tokens > 0) else 0,
        "provider_request_id": str(safe_payload.get("id") or "").strip(),
    }


def _extract_llm_usage_from_chat_response(response) -> dict:
    usage_obj = getattr(response, "usage", None)
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
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "llm_request_count": 1 if (prompt_tokens > 0 or completion_tokens > 0 or total_tokens > 0) else 0,
        "provider_request_id": str(getattr(response, "id", "") or "").strip(),
    }


def _build_llm_cost_stats(*, base_url: str, model: str, usage: dict | None) -> dict:
    usage_payload = usage if isinstance(usage, dict) else {}
    prompt_tokens = _safe_positive_int(usage_payload.get("prompt_tokens"))
    completion_tokens = _safe_positive_int(usage_payload.get("completion_tokens"))
    total_tokens = _safe_positive_int(usage_payload.get("total_tokens"))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    request_count = _safe_positive_int(usage_payload.get("llm_request_count"))
    if request_count <= 0 and total_tokens > 0:
        request_count = 1
    normalized_base_url = _normalize_llm_base_url(base_url)
    safe_model = str(model or "").strip()
    return {
        "llm_base_url": normalized_base_url,
        "llm_provider_effective": _infer_llm_provider_effective(normalized_base_url),
        "llm_model_effective": safe_model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "llm_request_count": request_count,
        "provider_request_id": str(usage_payload.get("provider_request_id") or "").strip(),
    }


def _append_llm_cost_for_scene(*, scene: str, owner_id: str, base_url: str, model: str, usage: dict | None) -> None:
    stats = _build_llm_cost_stats(base_url=base_url, model=model, usage=usage)
    if int(stats.get("total_tokens") or 0) <= 0:
        return
    append_llm_cost_record(
        scene=scene,
        owner_id=str(owner_id or "").strip(),
        stats=stats,
        llm_base_url=str(stats.get("llm_base_url") or ""),
        llm_provider_effective=str(stats.get("llm_provider_effective") or ""),
        llm_model_effective=str(stats.get("llm_model_effective") or ""),
        provider_request_id=str(stats.get("provider_request_id") or ""),
    )


def _test_llm_config(options: SubtitleJobOptions) -> tuple[SubtitleConfigProbeResult, dict]:
    llm_key = (options.llm.api_key or "").strip()
    if not llm_key:
        return SubtitleConfigProbeResult(ok=False, message="LLM API Key 为空"), {}

    raw_base_url = options.llm.base_url
    base_url = _normalize_llm_base_url(raw_base_url)
    protocol_candidates = _infer_llm_protocol_candidates(raw_base_url, options.llm.model)
    print(
        f"[DEBUG] LLM probe protocol candidates={protocol_candidates} "
        f"base_url={base_url} model={options.llm.model}"
    )
    probe_failures: list[str] = []

    for protocol in protocol_candidates:
        if protocol == "responses":
            endpoint = f"{base_url.rstrip('/')}/responses"
            base_payload = {
                "model": options.llm.model,
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
            payload_candidates: list[dict] = []
            if options.llm.llm_support_json:
                json_payload = dict(base_payload)
                json_payload["text"] = {"format": {"type": "json_object"}}
                payload_candidates.append(json_payload)
            payload_candidates.append(base_payload)

            print(f"[DEBUG] LLM probe using protocol=responses endpoint={endpoint}")
            last_status: int | None = None
            last_error = ""
            for index, request_payload in enumerate(payload_candidates):
                if index > 0:
                    print("[DEBUG] LLM probe retrying Responses API with minimal payload")
                try:
                    response = requests.post(
                        endpoint,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {llm_key}",
                        },
                        json=request_payload,
                        timeout=30,
                    )
                except Exception as exc:
                    last_status = None
                    last_error = f"request_error={str(exc)[:420]}"
                    continue

                if int(response.status_code) < 400:
                    try:
                        payload = response.json()
                    except Exception:
                        payload = {}
                    output_preview = _extract_responses_output_text(payload if isinstance(payload, dict) else {})
                    print(
                        f"[DEBUG] LLM probe success protocol=responses status={response.status_code} "
                        f"output_len={len(output_preview)}"
                    )
                    usage = _extract_llm_usage_from_payload(payload if isinstance(payload, dict) else {})
                    if not str(usage.get("provider_request_id") or "").strip():
                        headers = getattr(response, "headers", {})
                        try:
                            usage["provider_request_id"] = str(headers.get("x-request-id") or "").strip()
                        except Exception:
                            usage["provider_request_id"] = ""
                    return SubtitleConfigProbeResult(ok=True, message="LLM Responses 连通性测试通过"), usage

                last_status = int(response.status_code)
                last_error = f"body={str(response.text or '')[:420]}"

            status_tag = str(last_status) if last_status is not None else "request_error"
            failure_detail = f"protocol=responses; status={status_tag}; detail={last_error[:420]}"
            probe_failures.append(failure_detail)
            print(f"[DEBUG] LLM probe failed {failure_detail}")
            if _should_fallback_protocol(last_status, last_error):
                print("[DEBUG] LLM probe falling back from responses to next protocol")
                continue
            return SubtitleConfigProbeResult(
                ok=False,
                message=f"LLM Responses 连通性测试失败（HTTP {status_tag}）",
                detail="\n".join(probe_failures)[:1200],
            ), {}

        print(f"[DEBUG] LLM probe using protocol=chat.completions base_url={base_url}")
        try:
            client = OpenAI(api_key=llm_key, base_url=base_url)
            chat_response = client.chat.completions.create(
                model=options.llm.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                timeout=30,
            )
            print("[DEBUG] LLM probe success protocol=chat.completions")
            usage = _extract_llm_usage_from_chat_response(chat_response)
            return SubtitleConfigProbeResult(ok=True, message="LLM 连通性测试通过"), usage
        except Exception as exc:
            error_detail = f"error={str(exc)[:420]}"
            failure_detail = f"protocol=chat.completions; status=request_error; detail={error_detail}"
            probe_failures.append(failure_detail)
            print(f"[DEBUG] LLM probe failed {failure_detail}")
            if _should_fallback_protocol(None, error_detail):
                print("[DEBUG] LLM probe falling back from chat.completions to next protocol")
                continue
            return SubtitleConfigProbeResult(
                ok=False,
                message="LLM 连通性测试失败",
                detail="\n".join(probe_failures)[:1200],
            ), {}

    attempts = " -> ".join("chat.completions" if item == "chat" else "responses" for item in protocol_candidates)
    return SubtitleConfigProbeResult(
        ok=False,
        message=f"LLM 连通性测试失败（已尝试 {attempts}）",
        detail="\n".join(probe_failures)[:1200],
    ), {}


def _normalize_llm_probe_output(raw_result) -> tuple[SubtitleConfigProbeResult, dict]:
    if isinstance(raw_result, tuple) and len(raw_result) == 2:
        probe_result, usage = raw_result
        safe_probe = probe_result if isinstance(probe_result, SubtitleConfigProbeResult) else SubtitleConfigProbeResult(ok=False, message="LLM 连通性测试失败")
        safe_usage = usage if isinstance(usage, dict) else {}
        return safe_probe, safe_usage
    if isinstance(raw_result, SubtitleConfigProbeResult):
        return raw_result, {}
    return SubtitleConfigProbeResult(ok=False, message="LLM 连通性测试失败"), {}


def _build_silent_wav_bytes(duration_seconds: float = 0.3, sample_rate: int = 16000) -> bytes:
    frame_count = max(1, int(duration_seconds * sample_rate))
    audio_buffer = BytesIO()
    with wave.open(audio_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * frame_count)
    return audio_buffer.getvalue()


def _test_whisper_cloud(options: SubtitleJobOptions) -> SubtitleConfigProbeResult:
    whisper_key = (options.whisper.api_key or "").strip()
    if not whisper_key:
        return SubtitleConfigProbeResult(ok=False, message="Whisper API Key 为空（cloud 模式必填）")

    requested_model = str(options.whisper.model or "").strip().lower()
    if requested_model == "qwen3-asr-flash-filetrans":
        model = "qwen3-asr-flash-filetrans"
        model_label = "Qwen3-FileTrans"
        transcribe_func = _engine_transcribe_qwen3_asr_flash_filetrans
    else:
        model = "paraformer-v2"
        model_label = "Paraformer"
        transcribe_func = _engine_transcribe_paraformer_v2
    language = options.whisper.language or options.source_language or "en"
    base_url = _normalize_whisper_base_url(options.whisper.base_url)
    audio_bytes = _build_silent_wav_bytes(duration_seconds=0.3)
    probe_path = RUNTIME_JOBS_ROOT / f"whisper-probe-{uuid.uuid4().hex}.wav"
    try:
        probe_path.write_bytes(audio_bytes)
        whisper_options = EngineWhisperOptions(
            runtime="cloud",
            model=model,
            language=language,
            base_url=base_url,
            api_key=whisper_key,
        )
        transcribe_func(str(probe_path), whisper_options)
        return SubtitleConfigProbeResult(ok=True, message=f"{model_label} 云端连通性测试通过")
    except PipelineError as exc:
        raw_detail = str(exc.detail or "")
        subtask_code = ""
        if raw_detail:
            try:
                detail_payload = json.loads(raw_detail)
            except Exception:
                if "SUCCESS_WITH_NO_VALID_FRAGMENT" in raw_detail.upper():
                    subtask_code = "SUCCESS_WITH_NO_VALID_FRAGMENT"
            else:
                if isinstance(detail_payload, dict):
                    subtask_code = str(detail_payload.get("subtask_code") or "").strip().upper()
        if exc.code == "cloud_asr_failed" and subtask_code == "SUCCESS_WITH_NO_VALID_FRAGMENT":
            detail = f"stage={exc.stage}; code={exc.code}; detail={raw_detail}" if raw_detail else f"stage={exc.stage}; code={exc.code}"
            print("[DEBUG] Whisper probe treats SUCCESS_WITH_NO_VALID_FRAGMENT as connectivity success")
            return SubtitleConfigProbeResult(
                ok=True,
                message=f"{model_label} 云端连通性测试通过（静音探活，无有效语音片段）",
                detail=detail[:500],
            )
        detail = (
            f"stage={exc.stage}; code={exc.code}; detail={raw_detail}"
            if raw_detail
            else f"stage={exc.stage}; code={exc.code}"
        )
        return SubtitleConfigProbeResult(ok=False, message=exc.message or f"{model_label} 云端测试失败", detail=detail[:500])
    except Exception as exc:
        return SubtitleConfigProbeResult(ok=False, message=f"{model_label} 云端请求失败", detail=str(exc)[:500])
    finally:
        try:
            probe_path.unlink(missing_ok=True)
        except Exception:
            pass


def _test_whisper_local() -> SubtitleConfigProbeResult:
    try:
        from faster_whisper import WhisperModel  # type: ignore # noqa: F401
    except Exception as exc:
        return SubtitleConfigProbeResult(
            ok=False,
            message="本地 Whisper 依赖缺失，请安装 faster-whisper",
            detail=str(exc)[:500],
        )
    return SubtitleConfigProbeResult(ok=True, message="本地 Whisper 依赖可用")


def _probe_local_whisper_models() -> WhisperLocalModelsResponse:
    try:
        from faster_whisper.utils import download_model  # type: ignore
    except Exception:
        return WhisperLocalModelsResponse(
            status="degraded",
            dependency_ok=False,
            message="本地模型状态检测失败：faster-whisper 依赖不可用",
            models=[],
        )

    items: list[WhisperLocalModelStatus] = []
    for model_name in LOCAL_WHISPER_MODEL_CANDIDATES:
        cache_path = ""
        installed = False
        try:
            resolved = download_model(model_name, local_files_only=True)
            cache_path = str(resolved or "").strip()
            installed = bool(cache_path)
        except Exception:
            installed = False
        items.append(
            WhisperLocalModelStatus(
                model=model_name,
                installed=installed,
                cache_path=cache_path,
            )
        )

    installed_count = sum(1 for item in items if item.installed)
    return WhisperLocalModelsResponse(
        status="ok",
        dependency_ok=True,
        message=f"已检测本地模型缓存：{installed_count}/{len(items)}",
        models=items,
    )


def _probe_vl_flow_dependencies() -> tuple[bool, dict[str, bool]]:
    dependency_status: dict[str, bool] = {}
    for module_name in ("spacy", "pandas", "openpyxl"):
        try:
            importlib.import_module(module_name)
            dependency_status[module_name] = True
        except Exception:
            dependency_status[module_name] = False
    return all(dependency_status.values()), dependency_status


def _probe_binary_dependency(*names: str) -> bool:
    for item in names:
        safe = str(item or "").strip()
        if not safe:
            continue
        if shutil.which(safe):
            return True
    return False


def _test_whisper_config(options: SubtitleJobOptions) -> SubtitleConfigProbeResult:
    runtime = (options.whisper.runtime or "cloud").strip().lower()
    if runtime == "local":
        return _test_whisper_local()
    if runtime == "cloud":
        return _test_whisper_cloud(options)
    return SubtitleConfigProbeResult(ok=False, message=f"不支持的 whisper.runtime: {runtime}")


def _infer_reading_source_names(record, payload: dict) -> tuple[str, str]:
    fallback_video = f"{getattr(record, 'job_id', 'reading-source')}.mp4"
    raw_video = str(getattr(record, "video_path", "") or "").strip()
    video_name = Path(raw_video).name.strip() if raw_video else fallback_video
    if not video_name:
        video_name = fallback_video
    if "." not in video_name:
        video_name = f"{video_name}.mp4"
    stem = Path(video_name).stem.strip() or "reading-source"
    srt_name = f"{stem}.srt"

    subtitles = payload.get("subtitles") if isinstance(payload, dict) else None
    if isinstance(subtitles, list) and subtitles:
        first_text = str((subtitles[0] or {}).get("text") or "").strip()
        if first_text and len(first_text) > 180:
            srt_name = f"{stem}.txt"
    return video_name, srt_name


def _extract_summary_terms_from_result(payload: dict) -> dict:
    stats = payload.get("stats") if isinstance(payload, dict) else None
    if not isinstance(stats, dict):
        return {}
    summary_terms = stats.get("summary_terms")
    if not isinstance(summary_terms, dict):
        return {}
    return summary_terms


def _persist_reading_source_from_job_result(record, payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    subtitles = payload.get("subtitles")
    if not isinstance(subtitles, list) or not subtitles:
        return
    video_name, srt_name = _infer_reading_source_names(record, payload)
    summary_terms = _extract_summary_terms_from_result(payload)
    owner_user_id = str(getattr(record, "user_id", "") or "").strip() or "legacy"
    reading_store.upsert_source(
        user_id=owner_user_id,
        video_name=video_name,
        srt_name=srt_name,
        subtitles=[item for item in subtitles if isinstance(item, dict)],
        summary_terms=summary_terms,
    )
    print(
        f"[DEBUG] Reading source persisted video={video_name} srt={srt_name} "
        f"subtitles={len(subtitles)} job_id={getattr(record, 'job_id', '')}"
    )


def _sanitize_llm_options_payload(raw: dict | None) -> dict:
    payload = raw if isinstance(raw, dict) else {}
    normalized = {
        "base_url": str(payload.get("base_url") or "").strip() or DEFAULT_LLM_BASE_URL,
        "api_key": str(payload.get("api_key") or "").strip(),
        "model": str(payload.get("model") or "").strip() or DEFAULT_PROFILE_LLM_MODEL,
        "llm_support_json": bool(payload.get("llm_support_json", False)),
    }
    return LlmOptions.model_validate(normalized).model_dump()


def _is_llm_payload_ready(payload: dict | None) -> bool:
    safe_payload = _sanitize_llm_options_payload(payload if isinstance(payload, dict) else None)
    return bool(
        str(safe_payload.get("base_url") or "").strip()
        and str(safe_payload.get("model") or "").strip()
        and str(safe_payload.get("api_key") or "").strip()
    )


def _build_reading_llm_signature(payload: dict | None) -> str:
    safe_payload = _sanitize_llm_options_payload(payload if isinstance(payload, dict) else None)
    api_key = str(safe_payload.get("api_key") or "")
    api_key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16] if api_key else "missing"
    normalized = "|".join(
        [
            str(safe_payload.get("base_url") or "").strip().lower(),
            str(safe_payload.get("model") or "").strip(),
            "1" if bool(safe_payload.get("llm_support_json")) else "0",
            api_key_hash,
        ]
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _is_reading_cache_compatible(*, material: dict | None, llm_signature: str) -> tuple[bool, str]:
    safe_material = material if isinstance(material, dict) else {}
    config = safe_material.get("config") if isinstance(safe_material.get("config"), dict) else {}
    cached_strategy = str(config.get(READING_CACHE_QUALITY_STRATEGY_KEY) or "").strip()
    cached_signature = str(config.get(READING_CACHE_LLM_SIGNATURE_KEY) or "").strip()
    try:
        cached_retry_budget = int(config.get(READING_CACHE_QUALITY_RETRY_KEY) or -1)
    except Exception:
        cached_retry_budget = -1
    if cached_strategy != READING_QUALITY_STRATEGY_TAG:
        return False, "quality_strategy_mismatch"
    if cached_retry_budget != int(READING_QUALITY_RETRY_BUDGET):
        return False, "quality_retry_budget_mismatch"
    if cached_signature != str(llm_signature or "").strip():
        return False, "llm_signature_mismatch"
    return True, ""


def _normalize_profile_settings_payload(payload: dict | None) -> dict:
    data = dict(payload) if isinstance(payload, dict) else {}
    english_level = str(data.get("english_level") or "").strip().lower()
    if english_level not in {"junior", "senior", "cet4", "cet6", "kaoyan", "toefl", "sat"}:
        english_level = "cet4"
    llm_mode = str(data.get("llm_mode") or "").strip().lower()
    if llm_mode not in {"unified", "custom"}:
        llm_mode = "unified"
    llm_unified = _sanitize_llm_options_payload(data.get("llm_unified") if isinstance(data.get("llm_unified"), dict) else None)
    llm_listening = _sanitize_llm_options_payload(
        data.get("llm_listening") if isinstance(data.get("llm_listening"), dict) else None
    )
    llm_reading = _sanitize_llm_options_payload(data.get("llm_reading") if isinstance(data.get("llm_reading"), dict) else None)
    return {
        "english_level": english_level,
        "english_level_numeric": float(data.get("english_level_numeric") or 7.5),
        "english_level_cefr": str(data.get("english_level_cefr") or "").strip() or "B1",
        "llm_mode": llm_mode,
        "llm_unified": llm_unified,
        "llm_listening": llm_listening,
        "llm_reading": llm_reading,
        "updated_at": int(data.get("updated_at") or 0),
    }


def _to_public_llm_payload(raw: dict | None) -> dict:
    safe = _sanitize_llm_options_payload(raw if isinstance(raw, dict) else None)
    secret = str(safe.get("api_key") or "").strip()
    return {
        "base_url": str(safe.get("base_url") or "").strip() or DEFAULT_LLM_BASE_URL,
        "model": str(safe.get("model") or "").strip() or DEFAULT_PROFILE_LLM_MODEL,
        "llm_support_json": bool(safe.get("llm_support_json", False)),
        "has_api_key": bool(secret),
        "api_key_masked": mask_secret(secret) if secret else "",
    }


def _to_public_profile_settings_payload(payload: dict | None) -> dict:
    safe = _normalize_profile_settings_payload(payload if isinstance(payload, dict) else {})
    return {
        "english_level": str(safe.get("english_level") or "cet4"),
        "english_level_numeric": float(safe.get("english_level_numeric") or 7.5),
        "english_level_cefr": str(safe.get("english_level_cefr") or "B1"),
        "llm_mode": str(safe.get("llm_mode") or "unified"),
        "llm_unified": _to_public_llm_payload(safe.get("llm_unified") if isinstance(safe.get("llm_unified"), dict) else {}),
        "llm_listening": _to_public_llm_payload(
            safe.get("llm_listening") if isinstance(safe.get("llm_listening"), dict) else {}
        ),
        "llm_reading": _to_public_llm_payload(safe.get("llm_reading") if isinstance(safe.get("llm_reading"), dict) else {}),
        "updated_at": int(safe.get("updated_at") or 0),
    }


def _resolve_profile_llm_payload(*, profile: dict, scene: str) -> dict:
    safe_scene = str(scene or "").strip().lower()
    safe_profile = _normalize_profile_settings_payload(profile)
    llm_mode = str(safe_profile.get("llm_mode") or "unified")
    llm_unified = safe_profile.get("llm_unified") if isinstance(safe_profile.get("llm_unified"), dict) else {}
    llm_reading = safe_profile.get("llm_reading") if isinstance(safe_profile.get("llm_reading"), dict) else {}
    llm_listening = safe_profile.get("llm_listening") if isinstance(safe_profile.get("llm_listening"), dict) else {}

    if llm_mode == "custom":
        if safe_scene == "reading":
            return _sanitize_llm_options_payload(llm_reading)
        if safe_scene == "listening":
            return _sanitize_llm_options_payload(llm_listening)
    return _sanitize_llm_options_payload(llm_unified)


def _to_reading_material_response(material: dict, *, cached: bool) -> ReadingMaterialResponse:
    safe_material = material if isinstance(material, dict) else {}
    return ReadingMaterialResponse.model_validate(
        {
            "version_id": str(safe_material.get("version_id") or ""),
            "video_name": str(safe_material.get("video_name") or ""),
            "srt_name": str(safe_material.get("srt_name") or ""),
            "user_level": str(safe_material.get("user_level") or "cet4"),
            "scope": str(safe_material.get("scope") or "all"),
            "ratio_preset": str(safe_material.get("ratio_preset") or "long_term"),
            "difficulty_tier": str(safe_material.get("difficulty_tier") or "balanced"),
            "genre": str(safe_material.get("genre") or "news"),
            "i_plus_one_hit": bool(safe_material.get("i_plus_one_hit")),
            "pipeline_version": str(safe_material.get("pipeline_version") or READING_PIPELINE_VERSION),
            "config": safe_material.get("config") if isinstance(safe_material.get("config"), dict) else {},
            "difficulty_report": safe_material.get("difficulty_report")
            if isinstance(safe_material.get("difficulty_report"), dict)
            else {},
            "materials": safe_material.get("materials") if isinstance(safe_material.get("materials"), list) else [],
            "quiz": safe_material.get("quiz") if isinstance(safe_material.get("quiz"), dict) else {},
            "created_at": int(safe_material.get("created_at") or 0),
            "updated_at": int(safe_material.get("updated_at") or 0),
            "cached": bool(cached),
        }
    )


def _normalize_material_slot(raw: dict | None, *, kind: str) -> dict:
    data = raw if isinstance(raw, dict) else {}
    text = str(data.get("text") or "")
    word_count = int(data.get("word_count") or 0)
    target_word_count = int(data.get("target_word_count") or 0)
    return {
        "kind": kind,
        "text": text,
        "word_count": max(0, word_count),
        "target_word_count": max(0, target_word_count),
        "generated": bool(data.get("generated")) and bool(text.strip()),
    }


def _merge_material_slots(*, generated_materials: list[dict], previous_materials: list[dict]) -> list[dict]:
    generated_map: dict[str, dict] = {}
    previous_map: dict[str, dict] = {}
    for item in generated_materials or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind in {"intensive", "extensive"}:
            generated_map[kind] = item
    for item in previous_materials or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind in {"intensive", "extensive"}:
            previous_map[kind] = item

    merged: list[dict] = []
    for kind in ("intensive", "extensive"):
        if kind in generated_map:
            merged.append(_normalize_material_slot(generated_map[kind], kind=kind))
            continue
        if kind in previous_map:
            merged.append(_normalize_material_slot(previous_map[kind], kind=kind))
            continue
        merged.append(_normalize_material_slot({"generated": False}, kind=kind))
    return merged


def _find_short_question(*, version: dict, question_id: str) -> dict | None:
    safe_question_id = str(question_id or "").strip()
    if not safe_question_id:
        return None
    quiz = version.get("quiz") if isinstance(version, dict) else {}
    short_questions = quiz.get("short_questions") if isinstance(quiz, dict) else []
    for item in short_questions if isinstance(short_questions, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("question_id") or "").strip() == safe_question_id:
            return item
    return None


@app.middleware("http")
async def capture_unexpected_backend_errors(request: Request, call_next):
    target_file = DEFAULT_BACKEND_ERROR_FILE
    try:
        response = await call_next(request)
        if response.status_code >= 500:
            line = (
                f"[{request.method}] [status={response.status_code}] "
                f"[path={request.url.path}] [query={request.url.query}] "
                "Server returned 5xx response."
            )
            _append_runtime_error_report(line, file_name=target_file)
        return response
    except Exception as exc:
        line = (
            f"[{request.method}] [status=500] [path={request.url.path}] [query={request.url.query}] "
            f"{exc}\n{traceback.format_exc(limit=8)}"
        )
        _append_runtime_error_report(line, file_name=target_file)
        raise


@app.get("/api/v1/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    deps_ready, dep_detail = _probe_vl_flow_dependencies()
    ffmpeg_ready = _probe_binary_dependency("ffmpeg")
    ffprobe_ready = _probe_binary_dependency("ffprobe")
    ytdlp_ready = _probe_binary_dependency("yt-dlp", "yt_dlp")
    return HealthResponse(
        capabilities={
            "subtitle_jobs_conflict_409": True,
            "subtitle_job_status_whisper_fields": True,
            "subtitle_worker_watchdog": True,
            "subtitle_pipeline_v2": True,
            "subtitle_asr_fallback_chain": True,
            "subtitle_translation_refine_pass": True,
            "subtitle_perf_timing_metrics": True,
            "subtitle_whisper_local_models_probe": True,
            "subtitle_vl_flow_dependencies_ready": deps_ready,
            "subtitle_vl_dep_spacy": dep_detail.get("spacy", False),
            "subtitle_vl_dep_pandas": dep_detail.get("pandas", False),
            "subtitle_vl_dep_openpyxl": dep_detail.get("openpyxl", False),
            "subtitle_dep_ffmpeg": ffmpeg_ready,
            "subtitle_dep_ffprobe": ffprobe_ready,
            "subtitle_dep_ytdlp": ytdlp_ready,
            "reading_sources_api": True,
            "reading_material_generate_api": True,
            "profile_settings_api": True,
            "reading_history_api": True,
            "reading_versions_api": True,
            "reading_short_answer_api": True,
            "auth_required_for_business_apis": True,
        }
    )


@app.get("/api/v1/whisper/local-models", response_model=WhisperLocalModelsResponse)
def get_whisper_local_models(principal: AuthPrincipal = Depends(_require_principal)) -> WhisperLocalModelsResponse:
    _ = principal
    return _probe_local_whisper_models()


@app.post("/api/v1/subtitle-config/test", response_model=SubtitleConfigTestResponse)
def test_subtitle_config(
    payload: SubtitleJobOptions,
    principal: AuthPrincipal = Depends(_require_principal),
) -> SubtitleConfigTestResponse:
    _ = principal
    llm_result, llm_usage = _normalize_llm_probe_output(_test_llm_config(payload))
    whisper_result = _test_whisper_config(payload)
    if llm_result.ok and whisper_result.ok:
        status = "ok"
    elif llm_result.ok or whisper_result.ok:
        status = "partial"
    else:
        status = "failed"
    if llm_result.ok:
        try:
            _append_llm_cost_for_scene(
                scene="subtitle_config_probe",
                owner_id=f"probe_{uuid.uuid4().hex[:12]}",
                base_url=payload.llm.base_url,
                model=payload.llm.model,
                usage=llm_usage,
            )
        except Exception as exc:
            print(f"[DEBUG] Failed to append llm probe cost ledger: {exc}")
    return SubtitleConfigTestResponse(status=status, llm=llm_result, whisper=whisper_result)


@app.post("/api/v1/subtitle-config/test-llm", response_model=SubtitleConfigTestResponse)
def test_subtitle_config_llm(
    payload: SubtitleJobOptions,
    principal: AuthPrincipal = Depends(_require_principal),
) -> SubtitleConfigTestResponse:
    _ = principal
    llm_result, llm_usage = _normalize_llm_probe_output(_test_llm_config(payload))
    whisper_placeholder = SubtitleConfigProbeResult(ok=True, message="已跳过（本次仅测试 LLM）")
    status = "ok" if llm_result.ok else "failed"
    if llm_result.ok:
        try:
            _append_llm_cost_for_scene(
                scene="subtitle_config_probe",
                owner_id=f"probe_{uuid.uuid4().hex[:12]}",
                base_url=payload.llm.base_url,
                model=payload.llm.model,
                usage=llm_usage,
            )
        except Exception as exc:
            print(f"[DEBUG] Failed to append llm probe cost ledger: {exc}")
    return SubtitleConfigTestResponse(status=status, llm=llm_result, whisper=whisper_placeholder)


@app.post("/api/v1/subtitle-config/test-whisper", response_model=SubtitleConfigTestResponse)
def test_subtitle_config_whisper(
    payload: SubtitleJobOptions,
    principal: AuthPrincipal = Depends(_require_principal),
) -> SubtitleConfigTestResponse:
    _ = principal
    whisper_result = _test_whisper_config(payload)
    llm_placeholder = SubtitleConfigProbeResult(ok=True, message="已跳过（本次仅测试 Whisper）")
    status = "ok" if whisper_result.ok else "failed"
    return SubtitleConfigTestResponse(status=status, llm=llm_placeholder, whisper=whisper_result)


@app.post("/api/v1/browser-errors", response_model=BrowserErrorReportResponse)
def write_browser_error_report(payload: BrowserErrorReportRequest, response: Response) -> BrowserErrorReportResponse:
    _ = payload
    _mark_browser_errors_deprecated(response)
    raise HTTPException(
        status_code=410,
        detail={
            "code": "browser_errors_endpoint_removed",
            "message": "browser-errors 调试接口已提前下线，请使用后端日志定位问题",
        },
    )


@app.get("/api/v1/browser-errors/read", response_model=BrowserErrorReadResponse)
def read_browser_error_report(response: Response, file_name: str = "browser-error.log") -> BrowserErrorReadResponse:
    _ = file_name
    _mark_browser_errors_deprecated(response)
    raise HTTPException(
        status_code=410,
        detail={
            "code": "browser_errors_endpoint_removed",
            "message": "browser-errors 调试接口已提前下线，请使用后端日志定位问题",
        },
    )


@app.post("/api/v1/auth/register", response_model=AuthTokenResponse)
def register(payload: AuthRegisterRequest) -> AuthTokenResponse:
    try:
        token_payload = auth_service.register(username=payload.username, password=payload.password)
    except AuthError as exc:
        raise _auth_http_exception(exc) from exc
    return AuthTokenResponse.model_validate(token_payload)


@app.post("/api/v1/auth/login", response_model=AuthTokenResponse)
def login(payload: AuthLoginRequest) -> AuthTokenResponse:
    try:
        token_payload = auth_service.login(username=payload.username, password=payload.password)
    except AuthError as exc:
        raise _auth_http_exception(exc) from exc
    return AuthTokenResponse.model_validate(token_payload)


@app.post("/api/v1/auth/logout", response_model=AuthLogoutResponse)
def logout(principal: AuthPrincipal = Depends(_require_principal)) -> AuthLogoutResponse:
    auth_service.logout(principal)
    return AuthLogoutResponse(status="ok")


@app.get("/api/v1/auth/me", response_model=AuthUserResponse)
def get_auth_me(principal: AuthPrincipal = Depends(_require_principal)) -> AuthUserResponse:
    try:
        user_payload = auth_service.get_user_public(principal)
    except AuthError as exc:
        raise _auth_http_exception(exc) from exc
    return AuthUserResponse.model_validate(user_payload)


@app.get("/api/v1/wallet/quota", response_model=WalletQuotaResponse)
def get_wallet_quota(principal: AuthPrincipal = Depends(_require_principal)) -> WalletQuotaResponse:
    try:
        quota_payload = auth_service.get_wallet_quota(principal)
    except AuthError as exc:
        raise _auth_http_exception(exc) from exc
    return _to_wallet_quota_response(quota_payload)


@app.post("/api/v1/wallet/redeem", response_model=WalletRedeemResponse)
def redeem_wallet_code(
    payload: WalletRedeemRequest,
    principal: AuthPrincipal = Depends(_require_principal),
) -> WalletRedeemResponse:
    try:
        redeemed = auth_service.redeem_code(principal=principal, key=payload.key)
    except AuthError as exc:
        raise _auth_http_exception(exc) from exc
    return WalletRedeemResponse.model_validate(redeemed)


@app.get("/api/v1/wallet/packs", response_model=WalletPacksResponse)
def get_wallet_packs() -> WalletPacksResponse:
    return _to_wallet_packs_response()


@app.get("/api/v1/history-records", response_model=HistoryRecordsResponse)
def get_history_records(principal: AuthPrincipal = Depends(_require_principal)) -> HistoryRecordsResponse:
    records = history_store.list_records(user_id=principal.user_id)
    return HistoryRecordsResponse(records=records)


@app.put("/api/v1/history-records", response_model=HistoryRecordsSyncResponse)
def sync_history_records(
    payload: HistoryRecordsUpsertRequest,
    principal: AuthPrincipal = Depends(_require_principal),
) -> HistoryRecordsSyncResponse:
    normalized_records = [item.model_dump() for item in payload.records]
    saved_count = history_store.replace_all_records(normalized_records, user_id=principal.user_id)
    records = history_store.list_records(user_id=principal.user_id)
    return HistoryRecordsSyncResponse(saved_count=saved_count, records=records)


@app.get("/api/v1/reading/sources", response_model=ReadingSourcesResponse)
def list_reading_sources(principal: AuthPrincipal = Depends(_require_principal)) -> ReadingSourcesResponse:
    sources = reading_store.list_sources(user_id=principal.user_id, limit=300)
    return ReadingSourcesResponse.model_validate({"sources": sources})


@app.get("/api/v1/profile/settings", response_model=ProfileSettings)
def get_profile_settings_api(principal: AuthPrincipal = Depends(_require_principal)) -> ProfileSettings:
    settings = profile_store.get_profile_settings(user_id=principal.user_id)
    normalized = _to_public_profile_settings_payload(settings)
    return ProfileSettings.model_validate(normalized)


@app.put("/api/v1/profile/settings", response_model=ProfileSettings)
def update_profile_settings_api(
    payload: ProfileSettingsUpdateRequest,
    principal: AuthPrincipal = Depends(_require_principal),
) -> ProfileSettings:
    patch = payload.model_dump(exclude_none=True)
    for field in ("llm_unified", "llm_listening", "llm_reading"):
        llm_patch = patch.get(field)
        if isinstance(llm_patch, dict) and "api_key" in llm_patch:
            llm_patch.pop("api_key", None)
    updated = profile_store.upsert_profile_settings(patch, user_id=principal.user_id)
    normalized = _to_public_profile_settings_payload(updated)
    return ProfileSettings.model_validate(normalized)


@app.put("/api/v1/profile/keys", response_model=ProfileKeysUpdateResponse)
def update_profile_keys_api(
    payload: ProfileKeysUpdateRequest,
    principal: AuthPrincipal = Depends(_require_principal),
) -> ProfileKeysUpdateResponse:
    patch = payload.model_dump(exclude_none=True)
    if not patch:
        return ProfileKeysUpdateResponse(status="ok", updated_fields=[])
    profile_store.upsert_profile_api_keys(patch, user_id=principal.user_id)
    return ProfileKeysUpdateResponse(status="ok", updated_fields=sorted(list(patch.keys())))


@app.get("/api/v1/reading/history", response_model=ReadingHistoryResponse)
def get_reading_history(
    limit: int = 20,
    offset: int = 0,
    principal: AuthPrincipal = Depends(_require_principal),
) -> ReadingHistoryResponse:
    safe_limit = max(1, min(100, int(limit or 20)))
    safe_offset = max(0, int(offset or 0))
    items, has_more = reading_store.list_history(user_id=principal.user_id, limit=safe_limit, offset=safe_offset)
    normalized_items = [ReadingHistoryItem.model_validate(item).model_dump() for item in items]
    return ReadingHistoryResponse.model_validate(
        {
            "items": normalized_items,
            "offset": safe_offset,
            "limit": safe_limit,
            "has_more": bool(has_more),
        }
    )


@app.get("/api/v1/reading/versions/{version_id}", response_model=ReadingVersionResponse)
def get_reading_version(version_id: str, principal: AuthPrincipal = Depends(_require_principal)) -> ReadingVersionResponse:
    material = reading_store.get_version(user_id=principal.user_id, version_id=version_id)
    if not material:
        raise HTTPException(status_code=404, detail="Reading version not found")
    return ReadingVersionResponse.model_validate({"version": material})


@app.delete("/api/v1/reading/versions/{version_id}", response_model=DeleteOperationResponse)
def delete_reading_version(version_id: str, principal: AuthPrincipal = Depends(_require_principal)) -> DeleteOperationResponse:
    deleted_count = reading_store.delete_version(user_id=principal.user_id, version_id=version_id)
    if deleted_count <= 0:
        raise HTTPException(status_code=404, detail="Reading version not found")
    return DeleteOperationResponse(status="ok", deleted_count=deleted_count)


@app.post("/api/v1/reading/quiz/short-answers/submit", response_model=ReadingShortAnswerSubmitResponse)
def submit_reading_short_answer(
    payload: ReadingShortAnswerSubmitRequest,
    principal: AuthPrincipal = Depends(_require_principal),
) -> ReadingShortAnswerSubmitResponse:
    safe_version_id = str(payload.version_id or "").strip()
    safe_question_id = str(payload.question_id or "").strip()
    if not safe_version_id or not safe_question_id:
        raise HTTPException(status_code=400, detail="version_id and question_id are required")

    version = reading_store.get_version(user_id=principal.user_id, version_id=safe_version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Reading version not found")

    question = _find_short_question(version=version, question_id=safe_question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Short question not found")

    profile_settings = _normalize_profile_settings_payload(profile_store.get_profile_settings(user_id=principal.user_id))
    profile_llm_payload = _resolve_profile_llm_payload(profile=profile_settings, scene="reading")
    llm_payload = _build_oneapi_user_llm_payload(
        access_token=principal.access_token,
        raw_llm=profile_llm_payload,
    )
    result_payload = grade_short_answer(
        question=str(question.get("question") or ""),
        reference_answer=str(question.get("reference_answer") or ""),
        answer_text=payload.answer_text,
        llm_options_payload=llm_payload,
    )
    llm_usage = {}
    if isinstance(result_payload, dict):
        llm_usage = result_payload.pop("_llm_usage", {}) if isinstance(result_payload.get("_llm_usage"), dict) else {}
    saved = reading_store.save_short_answer_attempt(
        user_id=principal.user_id,
        version_id=safe_version_id,
        question_id=safe_question_id,
        answer_text=payload.answer_text,
        result_payload=result_payload,
    )
    if not saved:
        raise HTTPException(status_code=500, detail="Short answer save failed")
    print(
        f"[DEBUG] Reading short answer submitted version={safe_version_id} "
        f"question={safe_question_id} attempt={saved.get('attempt_id')}"
    )
    try:
        _append_llm_cost_for_scene(
            scene="reading_short_answer_grade",
            owner_id=str(saved.get("attempt_id") or safe_version_id),
            base_url=str(llm_payload.get("base_url") or ""),
            model=str(llm_payload.get("model") or ""),
            usage=llm_usage,
        )
    except Exception as exc:
        print(
            f"[DEBUG] Failed to append reading short answer llm cost "
            f"version={safe_version_id} question={safe_question_id}: {exc}"
        )
    return ReadingShortAnswerSubmitResponse.model_validate(saved)


@app.get("/api/v1/reading/quiz/short-answers/history", response_model=ReadingShortAnswerHistoryResponse)
def get_reading_short_answer_history(
    version_id: str = "",
    question_id: str = "",
    limit: int = 100,
    principal: AuthPrincipal = Depends(_require_principal),
) -> ReadingShortAnswerHistoryResponse:
    safe_version_id = str(version_id or "").strip()
    if not safe_version_id:
        raise HTTPException(status_code=400, detail="version_id is required")
    safe_limit = max(1, min(500, int(limit or 100)))
    items = reading_store.list_short_answer_attempts(
        user_id=principal.user_id,
        version_id=safe_version_id,
        question_id=str(question_id or "").strip(),
        limit=safe_limit,
    )
    return ReadingShortAnswerHistoryResponse.model_validate({"items": items})


@app.delete("/api/v1/reading/quiz/short-answers/history/group", response_model=DeleteOperationResponse)
def delete_reading_short_answer_group(
    payload: ReadingShortAnswerHistoryDeleteRequest,
    principal: AuthPrincipal = Depends(_require_principal),
) -> DeleteOperationResponse:
    deleted_count = reading_store.delete_short_answer_group(
        user_id=principal.user_id,
        version_id=payload.version_id,
        question_id=payload.question_id,
    )
    return DeleteOperationResponse(status="ok", deleted_count=deleted_count)


@app.get("/api/v1/reading/materials", response_model=ReadingMaterialResponse)
def get_reading_material(
    video_name: str = "",
    srt_name: str = "",
    user_level: str = "cet4",
    version_id: str = "",
    principal: AuthPrincipal = Depends(_require_principal),
) -> ReadingMaterialResponse:
    safe_version_id = (version_id or "").strip()
    safe_level = (user_level or "cet4").strip().lower() or "cet4"
    material = None
    if safe_version_id:
        material = reading_store.get_version(user_id=principal.user_id, version_id=safe_version_id)
    else:
        if not str(video_name or "").strip() or not str(srt_name or "").strip():
            raise HTTPException(status_code=400, detail="video_name and srt_name are required when version_id is absent")
        material = reading_store.get_material(
            user_id=principal.user_id,
            video_name=video_name,
            srt_name=srt_name,
            user_level=safe_level,
            pipeline_version=READING_PIPELINE_VERSION,
        )
    if not material:
        raise HTTPException(status_code=404, detail="Reading material not found")
    return _to_reading_material_response(material, cached=True)


@app.post("/api/v1/reading/materials/generate", response_model=ReadingMaterialResponse)
def generate_reading_material_api(
    payload: ReadingMaterialGenerateRequest,
    principal: AuthPrincipal = Depends(_require_principal),
) -> ReadingMaterialResponse:
    source = reading_store.get_source(user_id=principal.user_id, video_name=payload.video_name, srt_name=payload.srt_name)
    if not source:
        raise HTTPException(status_code=404, detail="Reading source not found")

    requested_llm = payload.llm.model_dump() if payload.llm else None
    llm_payload = _build_oneapi_user_llm_payload(
        access_token=principal.access_token,
        raw_llm=requested_llm if isinstance(requested_llm, dict) else None,
    )
    llm_signature = _build_reading_llm_signature(llm_payload)

    if not payload.force_regenerate:
        cached = reading_store.get_material(
            user_id=principal.user_id,
            video_name=payload.video_name,
            srt_name=payload.srt_name,
            user_level=payload.user_level,
            pipeline_version=READING_PIPELINE_VERSION,
            scope=payload.scope,
            ratio_preset=payload.ratio_preset,
            difficulty_tier=payload.difficulty_tier,
            genre=payload.genre,
        )
        if cached:
            compatible, reason = _is_reading_cache_compatible(material=cached, llm_signature=llm_signature)
            if compatible:
                print(
                    f"[DEBUG] Reading material cache hit video={payload.video_name} "
                    f"srt={payload.srt_name} level={payload.user_level} scope={payload.scope} "
                    f"ratio={payload.ratio_preset} tier={payload.difficulty_tier} genre={payload.genre} "
                    f"llm_sig={llm_signature[:8]}"
                )
                return _to_reading_material_response(cached, cached=True)
            print(
                f"[DEBUG] Reading material cache bypass video={payload.video_name} "
                f"srt={payload.srt_name} reason={reason} llm_sig={llm_signature[:8]}"
            )

    latest_version = None
    if payload.scope != "all":
        latest_version = reading_store.get_latest_version_by_source(
            user_id=principal.user_id,
            video_name=payload.video_name,
            srt_name=payload.srt_name,
        )
    print(
        f"[DEBUG] Reading material generate start video={payload.video_name} "
        f"srt={payload.srt_name} level={payload.user_level} scope={payload.scope} "
        f"ratio={payload.ratio_preset} tier={payload.difficulty_tier} genre={payload.genre} "
        f"force={payload.force_regenerate} llm_sig={llm_signature[:8]}"
    )
    try:
        generated = generate_reading_material(
            source=source,
            user_level=payload.user_level,
            scope=payload.scope,
            ratio_preset=payload.ratio_preset,
            difficulty_tier=payload.difficulty_tier,
            genre=payload.genre,
            llm_options_payload=llm_payload,
        )
    except ReadingPipelineError as exc:
        code = str(exc.code or "").strip() or "reading_generation_failed"
        status_code = 502 if code in {"reading_generation_quality_failed", "reading_quiz_generation_failed"} else 500
        raise HTTPException(
            status_code=status_code,
            detail={
                "code": code,
                "message": exc.message,
                "detail": exc.detail,
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "reading_generation_failed",
                "message": "Reading generation failed",
                "detail": str(exc)[:500],
            },
        ) from exc

    previous_materials = latest_version.get("materials") if isinstance(latest_version, dict) else []
    materials_payload = _merge_material_slots(
        generated_materials=generated.materials,
        previous_materials=previous_materials if isinstance(previous_materials, list) else [],
    )
    quiz_payload = generated.quiz if isinstance(generated.quiz, dict) else {}
    if payload.scope == "extensive" and isinstance(latest_version, dict):
        previous_quiz = latest_version.get("quiz")
        if isinstance(previous_quiz, dict):
            quiz_payload = previous_quiz

    config_payload = dict(generated.config if isinstance(generated.config, dict) else {})
    config_payload[READING_CACHE_QUALITY_STRATEGY_KEY] = READING_QUALITY_STRATEGY_TAG
    config_payload[READING_CACHE_QUALITY_RETRY_KEY] = int(READING_QUALITY_RETRY_BUDGET)
    config_payload[READING_CACHE_LLM_SIGNATURE_KEY] = llm_signature

    saved = reading_store.save_material(
        user_id=principal.user_id,
        source_id=int(source.get("id") or 0),
        user_level=payload.user_level,
        scope=payload.scope,
        ratio_preset=payload.ratio_preset,
        difficulty_tier=payload.difficulty_tier,
        genre=payload.genre,
        i_plus_one_hit=generated.i_plus_one_hit,
        config=config_payload,
        difficulty_report=generated.difficulty_report,
        materials=materials_payload,
        quiz=quiz_payload,
        pipeline_version=READING_PIPELINE_VERSION,
    )
    if not saved:
        raise HTTPException(status_code=500, detail="Reading material save failed")
    try:
        _append_llm_cost_for_scene(
            scene="reading_material_generate",
            owner_id=str(saved.get("version_id") or ""),
            base_url=str(llm_payload.get("base_url") or ""),
            model=str(llm_payload.get("model") or ""),
            usage=generated.llm_usage if isinstance(generated.llm_usage, dict) else {},
        )
    except Exception as exc:
        print(
            f"[DEBUG] Failed to append reading material llm cost "
            f"video={payload.video_name} srt={payload.srt_name}: {exc}"
        )

    print(
        f"[DEBUG] Reading material generated video={payload.video_name} "
        f"srt={payload.srt_name} level={payload.user_level} scope={payload.scope} "
        f"ratio={payload.ratio_preset} tier={payload.difficulty_tier} genre={payload.genre}"
    )
    return _to_reading_material_response(saved, cached=False)


@app.post("/api/v1/subtitle-jobs", response_model=JobCreateResponse)
async def create_subtitle_job(
    video_file: UploadFile = File(...),
    options_json: str = Form(default="{}"),
    principal: AuthPrincipal = Depends(_require_principal),
) -> JobCreateResponse:
    if not video_file.filename:
        raise HTTPException(status_code=400, detail="video_file filename is required")

    try:
        raw_options = json.loads(options_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid options_json: {exc}") from exc

    options = SubtitleJobOptions.model_validate(raw_options)
    options_dict = _validate_subtitle_job_options(options)
    options_dict = _inject_job_llm_options_from_principal(options=options_dict, principal=principal)

    capacity = job_manager.check_submit_capacity(user_id=principal.user_id)
    if not bool(capacity.get("ok")):
        await video_file.close()
        code = str(capacity.get("code") or "")
        status_code = 409 if code == "user_concurrency_limit" else 429
        raise HTTPException(status_code=status_code, detail=capacity)

    job_id = uuid.uuid4().hex
    work_dir = RUNTIME_JOBS_ROOT / job_id
    input_dir = work_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_filename(video_file.filename)
    video_path = input_dir / safe_name
    try:
        with video_path.open("wb") as stream:
            while True:
                chunk = await video_file.read(1024 * 1024)
                if not chunk:
                    break
                stream.write(chunk)
    finally:
        await video_file.close()

    record = job_manager.create_job(
        user_id=principal.user_id,
        video_path=str(video_path),
        options=options_dict,
        job_id=job_id,
        work_dir=str(work_dir),
        enqueue=False,
    )
    job_manager.enqueue(record.job_id)
    return JobCreateResponse(job_id=record.job_id, status="queued")


@app.post("/api/v1/subtitle-jobs/from-url", response_model=JobCreateResponse)
def create_subtitle_job_from_url(
    payload: SubtitleJobFromUrlRequest,
    principal: AuthPrincipal = Depends(_require_principal),
) -> JobCreateResponse:
    policy = evaluate_source_url_policy(payload.url)
    if not bool(policy.get("allowed")):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "source_url_not_allowed",
                "message": str(policy.get("reason") or "不允许的来源链接"),
                "source_url_policy": policy,
            },
        )
    try:
        safe_url = normalize_source_url(str(policy.get("normalized_url") or payload.url))
    except PipelineError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc

    options_dict = _validate_subtitle_job_options(payload.options)
    options_dict = _inject_job_llm_options_from_principal(options=options_dict, principal=principal)

    capacity = job_manager.check_submit_capacity(user_id=principal.user_id)
    if not bool(capacity.get("ok")):
        code = str(capacity.get("code") or "")
        status_code = 409 if code == "user_concurrency_limit" else 429
        raise HTTPException(status_code=status_code, detail=capacity)

    job_id = uuid.uuid4().hex
    work_dir = RUNTIME_JOBS_ROOT / job_id
    input_dir = work_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    record = job_manager.create_url_job(
        user_id=principal.user_id,
        source_url=safe_url,
        options=options_dict,
        job_id=job_id,
        work_dir=str(work_dir),
        enqueue=False,
    )
    job_manager.enqueue(record.job_id)
    return JobCreateResponse.model_validate(
        {
            "job_id": record.job_id,
            "status": "queued",
            "source_url_policy": policy,
        }
    )


@app.post("/api/v1/subtitle-jobs/resume-llm", response_model=JobCreateResponse)
def resume_subtitle_job(principal: AuthPrincipal = Depends(_require_principal)) -> JobCreateResponse:
    _ = principal
    raise HTTPException(status_code=410, detail=_deprecated_simplified_only_detail())


@app.post("/api/v1/subtitle-jobs/continue", response_model=JobCreateResponse)
def continue_subtitle_job(principal: AuthPrincipal = Depends(_require_principal)) -> JobCreateResponse:
    _ = principal
    raise HTTPException(status_code=410, detail=_deprecated_simplified_only_detail())


@app.post("/api/v1/subtitle-jobs/{job_id}/retry-alignment", response_model=JobCreateResponse)
def retry_subtitle_job_alignment(job_id: str, principal: AuthPrincipal = Depends(_require_principal)) -> JobCreateResponse:
    _ = job_id
    _ = principal
    raise HTTPException(status_code=410, detail=_deprecated_simplified_only_detail())


@app.get("/api/v1/subtitle-jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str, principal: AuthPrincipal = Depends(_require_principal)) -> JobStatusResponse:
    record = job_manager.get_status(job_id, user_id=principal.user_id)
    if not record:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse.model_validate(job_manager.serialize_status(record))


@app.get("/api/v1/subtitle-jobs/{job_id}/result")
def get_job_result(job_id: str, principal: AuthPrincipal = Depends(_require_principal)) -> dict:
    record = job_manager.get_status(job_id, user_id=principal.user_id)
    if not record:
        raise HTTPException(status_code=404, detail="Job not found")
    if record.status != "completed":
        raise HTTPException(status_code=409, detail=f"Job not completed yet, current status: {record.status}")

    payload = job_manager.consume_result(job_id, user_id=principal.user_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Result not found")
    if isinstance(payload, dict) and not isinstance(payload.get("diagnostics"), dict):
        sync_diagnostics = getattr(record, "sync_diagnostics", None)
        if isinstance(sync_diagnostics, dict) and sync_diagnostics:
            payload["diagnostics"] = dict(sync_diagnostics)

    try:
        _persist_reading_source_from_job_result(record, payload)
    except Exception as exc:
        print(f"[DEBUG] Failed to persist reading source from job result job_id={job_id}: {exc}")

    return payload


@app.get("/api/v1/subtitle-jobs/{job_id}/video")
def get_job_video(job_id: str, principal: AuthPrincipal = Depends(_require_principal)):
    record = job_manager.get_status(job_id, user_id=principal.user_id)
    if not record:
        raise HTTPException(status_code=404, detail="Job not found")
    if record.source_mode != "url":
        raise HTTPException(status_code=409, detail="Job is not url source mode")
    if record.status != "completed":
        raise HTTPException(status_code=409, detail=f"Job not completed yet, current status: {record.status}")

    video_path = Path(record.video_path or "").resolve()
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="Downloaded video not found")

    runtime_root = Path(job_manager.runtime_root).resolve()
    if runtime_root not in video_path.parents:
        raise HTTPException(status_code=400, detail="Invalid video path")

    media_type = mimetypes.guess_type(video_path.name)[0] or "application/octet-stream"
    return FileResponse(
        path=str(video_path),
        media_type=media_type,
        filename=video_path.name,
    )


@app.delete("/api/v1/subtitle-jobs/{job_id}")
def delete_job(job_id: str, principal: AuthPrincipal = Depends(_require_principal)) -> dict:
    payload = job_manager.delete_job(job_id, user_id=principal.user_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Job not found")
    return payload
