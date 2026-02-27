from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse


DEFAULT_LLM_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_WHISPER_BASE_URL = "https://dashscope.aliyuncs.com"

LlmProtocol = Literal["responses", "chat"]
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


def normalize_llm_base_url(base_url: str) -> str:
    value = (base_url or "").strip()
    if not value:
        value = DEFAULT_LLM_BASE_URL
    if "://" not in value:
        value = f"https://{value}"
    normalized_lower = value.lower().rstrip("/")
    for suffix in ("/responses", "/chat/completions", "/completions"):
        if normalized_lower.endswith(suffix):
            value = value[: -len(suffix)]
            break
    return value.rstrip("/")


def infer_llm_protocol_candidates(base_url: str, model: str = "") -> list[LlmProtocol]:
    raw = (base_url or "").strip()
    model_lower = (model or "").strip().lower()
    if not raw and any(model_lower.startswith(prefix) for prefix in RESPONSES_PREFERRED_MODEL_PREFIXES):
        return ["responses", "chat"]

    normalized = raw.lower().rstrip("/")
    first: LlmProtocol = "chat"
    explicit_protocol: LlmProtocol | None = None
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

    second: LlmProtocol = "chat" if first == "responses" else "responses"
    return [first, second]


def should_use_responses_api(base_url: str, model: str = "") -> bool:
    candidates = infer_llm_protocol_candidates(base_url, model=model)
    return len(candidates) > 0 and candidates[0] == "responses"


def should_fallback_protocol(status_code: int | None, error_text: str) -> bool:
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


def normalize_whisper_base_url(base_url: str) -> str:
    value = (base_url or "").strip()
    if not value:
        return DEFAULT_WHISPER_BASE_URL
    if "://" not in value:
        value = f"https://{value}"
    return value.rstrip("/")


def extract_responses_output_text(payload: dict) -> str:
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
