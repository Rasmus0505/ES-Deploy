from __future__ import annotations

import json
import re
from typing import Any, Mapping

import requests

from vendor.videolingo_subtitle_core.engine import PipelineError
from vendor.videolingo_subtitle_core import engine as engine_module


QWEN_MT_FLASH_MODEL = "qwen-mt-flash"
QWEN_MT_FLASH_PROVIDER = "dashscope_qwen_mt_flash"
DEFAULT_QWEN_MT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_BATCH_KEY_PATTERN = re.compile(r"^id_(\d+)$")
_BATCH_LINE_PATTERN = re.compile(r"^(id_\d+)\s*[:：]\s*(.*)$")
_CONTEXT_OVERFLOW_HINTS = (
    "maximum context",
    "context length",
    "too long",
    "token",
    "length",
    "input is too long",
)
_MAX_SPLIT_RECURSION_DEPTH = 12


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        return 0
    return parsed if parsed > 0 else 0


def _normalize_base_url(value: str) -> str:
    safe = _safe_text(value)
    if not safe:
        safe = DEFAULT_QWEN_MT_BASE_URL
    if "://" not in safe:
        safe = f"https://{safe}"
    return safe.rstrip("/")


def _normalize_translation_language(value: str) -> str:
    safe = _safe_text(value).lower()
    if not safe:
        return "English"
    alias = {
        "en": "English",
        "zh": "Chinese",
        "ja": "Japanese",
        "ko": "Korean",
    }
    return alias.get(safe, _safe_text(value))


def _extract_json_payload(prompt: str) -> dict[str, str] | None:
    text = str(prompt or "")
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = text[start : end + 1]
    try:
        payload = json.loads(candidate)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if not payload:
        return None

    normalized: dict[str, str] = {}
    for raw_key, raw_value in payload.items():
        key = _safe_text(raw_key)
        if not key or not _BATCH_KEY_PATTERN.match(key):
            return None
        normalized[key] = _safe_text(raw_value)
    return normalized if normalized else None


class QwenMtBridgeSession:
    def __init__(self, options_payload: Mapping[str, Any] | None):
        options = options_payload if isinstance(options_payload, Mapping) else {}
        llm = options.get("llm") if isinstance(options.get("llm"), Mapping) else {}
        self._enabled = _safe_text(llm.get("model")).lower() == QWEN_MT_FLASH_MODEL
        self._source_language = _safe_text(options.get("source_language")) or "en"
        self._target_language = _safe_text(options.get("target_language")) or "zh"
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._request_count = 0
        self._patched_chat_json = False
        self._patched_build_translation_batches = False
        self._original_chat_json = None
        self._original_build_translation_batches = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def __enter__(self) -> "QwenMtBridgeSession":
        if not self._enabled:
            return self
        original = getattr(engine_module, "_chat_json", None)
        if not callable(original):
            self._enabled = False
            return self

        self._original_chat_json = original

        def wrapped(opts: Any, prompt: str) -> dict:
            return self._handle_chat_json(opts=opts, prompt=prompt)

        setattr(engine_module, "_chat_json", wrapped)
        self._patched_chat_json = True

        original_batches = getattr(engine_module, "_build_translation_batches", None)
        if callable(original_batches):
            self._original_build_translation_batches = original_batches

            def single_batch(
                texts: list[str],
                *,
                max_items: int,
                max_chars: int,
                min_items: int,
            ) -> list[tuple[int, int]]:
                _ = max_items
                _ = max_chars
                _ = min_items
                safe_texts = texts if isinstance(texts, list) else list(texts or [])
                if not safe_texts:
                    return []
                return [(0, len(safe_texts))]

            setattr(engine_module, "_build_translation_batches", single_batch)
            self._patched_build_translation_batches = True

        print("[DEBUG] qwen-mt bridge enabled")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._patched_chat_json and callable(self._original_chat_json):
            setattr(engine_module, "_chat_json", self._original_chat_json)
        if self._patched_build_translation_batches and callable(self._original_build_translation_batches):
            setattr(engine_module, "_build_translation_batches", self._original_build_translation_batches)
        if self._patched_chat_json or self._patched_build_translation_batches:
            print("[DEBUG] qwen-mt bridge restored")
        self._patched_chat_json = False
        self._patched_build_translation_batches = False

    def get_usage_stats(self) -> dict[str, Any]:
        return {
            "translation_mode_effective": "translation_model" if self._enabled else "llm_model",
            "translation_provider_effective": QWEN_MT_FLASH_PROVIDER if self._enabled else "",
            "translation_model_effective": QWEN_MT_FLASH_MODEL if self._enabled else "",
            "translation_prompt_tokens": int(self._prompt_tokens),
            "translation_completion_tokens": int(self._completion_tokens),
            "translation_total_tokens": int(self._total_tokens),
            "translation_request_count": int(self._request_count),
        }

    def _handle_chat_json(self, opts: Any, prompt: str) -> dict:
        if not callable(self._original_chat_json):
            raise PipelineError("llm", "llm_request_failed", "LLM 请求失败", detail="qwen_bridge_missing_original")
        model = _safe_text(getattr(opts, "model", ""))
        if model.lower() != QWEN_MT_FLASH_MODEL:
            return self._original_chat_json(opts, prompt)

        payload = _extract_json_payload(prompt)
        if not payload:
            return self._original_chat_json(opts, prompt)

        return self._translate_payload_with_fallback(opts=opts, payload=payload, depth=0)

    def _translate_payload_with_fallback(self, *, opts: Any, payload: Mapping[str, str], depth: int) -> dict[str, str]:
        ordered = sorted(
            payload.items(),
            key=lambda item: int(_BATCH_KEY_PATTERN.match(item[0]).group(1)),  # type: ignore[arg-type]
        )
        normalized_payload = {key: _safe_text(value) for key, value in ordered}
        try:
            return self._translate_payload_once(opts=opts, payload=normalized_payload)
        except PipelineError as exc:
            if not self._should_split_fallback(exc):
                raise
            if len(normalized_payload) <= 1:
                raise
            if depth >= _MAX_SPLIT_RECURSION_DEPTH:
                raise PipelineError(
                    "llm",
                    "llm_request_failed",
                    "翻译模型请求失败（分段回退超过最大深度）",
                    detail=f"fallback_depth={depth}; limit={_MAX_SPLIT_RECURSION_DEPTH}",
                ) from exc

            split_point = len(ordered) // 2
            left_payload = {key: value for key, value in ordered[:split_point]}
            right_payload = {key: value for key, value in ordered[split_point:]}
            print(
                f"[DEBUG] qwen-mt overflow fallback split depth={depth} "
                f"size={len(ordered)} left={len(left_payload)} right={len(right_payload)}"
            )

            translated = {}
            translated.update(self._translate_payload_with_fallback(opts=opts, payload=left_payload, depth=depth + 1))
            translated.update(self._translate_payload_with_fallback(opts=opts, payload=right_payload, depth=depth + 1))
            return translated

    def _should_split_fallback(self, exc: PipelineError) -> bool:
        if str(exc.code or "") != "llm_request_failed":
            return False
        detail = _safe_text(getattr(exc, "detail", ""))
        message = _safe_text(getattr(exc, "message", ""))
        merged = f"{message} {detail}".lower()
        if "http 400" not in merged and "http 413" not in merged:
            return False
        return any(hint in merged for hint in _CONTEXT_OVERFLOW_HINTS)

    def _translate_payload_once(self, *, opts: Any, payload: Mapping[str, str]) -> dict[str, str]:
        if not payload:
            return {}

        base_url = _normalize_base_url(_safe_text(getattr(opts, "base_url", "")))
        endpoint = f"{base_url}/chat/completions"
        api_key = _safe_text(getattr(opts, "api_key", ""))
        if not api_key:
            raise PipelineError("llm", "missing_llm_api_key", "缺少 LLM API Key")

        source_lang = _normalize_translation_language(self._source_language)
        target_lang = _normalize_translation_language(self._target_language)
        request_content = json.dumps(payload, ensure_ascii=False)
        body = {
            "model": QWEN_MT_FLASH_MODEL,
            "messages": [{"role": "user", "content": request_content}],
            "translation_options": {
                "source_lang": source_lang,
                "target_lang": target_lang,
            },
        }
        self._request_count += 1
        try:
            response = requests.post(
                endpoint,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                json=body,
                timeout=180,
            )
        except Exception as exc:
            raise PipelineError("llm", "llm_request_failed", "翻译模型请求失败", detail=str(exc)[:600]) from exc
        if int(response.status_code) >= 400:
            raise PipelineError(
                "llm",
                "llm_request_failed",
                f"翻译模型请求失败（HTTP {response.status_code}）",
                detail=str(response.text or "")[:1200],
            )
        try:
            payload_response = response.json()
        except Exception as exc:
            raise PipelineError("llm", "llm_invalid_json", "翻译模型返回非 JSON", detail=str(response.text or "")[:600]) from exc

        choices = payload_response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise PipelineError("llm", "llm_invalid_json", "翻译模型返回缺少 choices", detail=str(payload_response)[:600])
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        content = _safe_text(message.get("content") if isinstance(message, dict) else "")
        if not content:
            raise PipelineError("llm", "llm_invalid_json", "翻译模型返回空内容", detail=str(payload_response)[:600])

        parsed_translations = self._parse_translation_content(
            content=content,
            expected_keys=set(payload.keys()),
            raw_payload=payload_response,
        )

        usage = payload_response.get("usage") if isinstance(payload_response.get("usage"), dict) else {}
        prompt_tokens = _safe_int(usage.get("prompt_tokens"))
        completion_tokens = _safe_int(usage.get("completion_tokens"))
        total_tokens = _safe_int(usage.get("total_tokens"))
        if total_tokens <= 0:
            total_tokens = prompt_tokens + completion_tokens

        self._prompt_tokens += prompt_tokens
        self._completion_tokens += completion_tokens
        self._total_tokens += total_tokens
        return parsed_translations

    def _parse_translation_content(
        self,
        *,
        content: str,
        expected_keys: set[str],
        raw_payload: Mapping[str, Any],
    ) -> dict[str, str]:
        text = str(content or "").strip()
        if not text:
            raise PipelineError("llm", "llm_invalid_json", "翻译模型返回空内容", detail=str(raw_payload)[:600])

        parsed_json = self._parse_translation_json_object(text)
        if parsed_json:
            normalized_json = self._normalize_translation_mapping(parsed_json)
            if set(normalized_json.keys()) == expected_keys:
                return normalized_json

        line_mapping = self._parse_translation_lines(text)
        if line_mapping and set(line_mapping.keys()) == expected_keys:
            return line_mapping

        detail = json.dumps(
            {
                "expected_keys": sorted(expected_keys),
                "parsed_json_keys": sorted(parsed_json.keys()) if parsed_json else [],
                "line_keys": sorted(line_mapping.keys()) if line_mapping else [],
                "content_preview": text[:400],
            },
            ensure_ascii=False,
        )
        raise PipelineError("llm", "llm_invalid_json", "翻译模型返回结构不符合预期", detail=detail[:1200])

    def _parse_translation_json_object(self, content: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            return None
        candidate = content[start : end + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
        return None

    def _parse_translation_lines(self, content: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for raw_line in str(content or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            matched = _BATCH_LINE_PATTERN.match(line)
            if not matched:
                continue
            key = _safe_text(matched.group(1))
            value = _safe_text(matched.group(2))
            if not key:
                continue
            result[key] = value
        return result

    def _normalize_translation_mapping(self, payload: Mapping[str, Any]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_key, raw_value in payload.items():
            key = _safe_text(raw_key)
            if not key or not _BATCH_KEY_PATTERN.match(key):
                continue
            normalized[key] = _safe_text(raw_value)
        return normalized


def qwen_mt_bridge_session(options_payload: Mapping[str, Any] | None) -> QwenMtBridgeSession:
    return QwenMtBridgeSession(options_payload)
