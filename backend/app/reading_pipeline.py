from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

import requests
from openai import OpenAI

from app.provider_url_rules import (
    DEFAULT_LLM_BASE_URL,
    extract_responses_output_text as _extract_responses_output_text,
    infer_llm_protocol_candidates as _infer_llm_protocol_candidates,
    normalize_llm_base_url as _normalize_llm_base_url,
    should_fallback_protocol as _should_fallback_protocol,
)


PIPELINE_VERSION = "reading_v2_v2"
QUALITY_STRATEGY_TAG = "reading_quality_v2"
QUALITY_RETRY_BUDGET = 2

LEVEL_META: dict[str, tuple[float, str]] = {
    "junior": (3.5, "A2"),
    "senior": (5.5, "A2-B1"),
    "cet4": (7.5, "B1"),
    "cet6": (9.5, "B2"),
    "kaoyan": (10.5, "B2-C1"),
    "toefl": (11.5, "C1-C2"),
    "sat": (11.5, "C1-C2"),
}
RATIO_PRESETS: dict[str, tuple[float, float]] = {
    "high_energy": (0.7, 0.3),
    "long_term": (0.5, 0.5),
    "low_energy": (0.3, 0.7),
}
DIFFICULTY_OFFSETS: dict[str, float] = {
    "very_easy": -1.0,
    "easy": 0.0,
    "balanced": 1.0,
    "challenging": 2.0,
    "hard": 3.0,
}
GENRE_HINTS: dict[str, str] = {
    "news": "Keep the tone factual and concise.",
    "science": "Use explanatory style with clear cause-effect logic.",
    "story": "Use narrative flow with concrete scenes.",
    "workplace": "Use practical business communication context.",
}
CEFR_NUMERIC: dict[str, float] = {
    "A1": 2.0,
    "A2": 4.0,
    "A2-B1": 5.5,
    "B1": 7.5,
    "B2": 9.5,
    "B2-C1": 10.5,
    "C1": 11.0,
    "C1-C2": 11.5,
    "C2": 12.0,
}
CEFR_VOCAB: dict[str, set[str]] = {
    "A2": {"family", "school", "hobby", "travel", "daily", "simple", "practice", "lesson"},
    "B1": {"improve", "context", "strategy", "progress", "challenge", "effective", "support", "method"},
    "B2": {"analyze", "evaluate", "evidence", "impact", "approach", "significant", "complex", "sustain"},
    "C1": {"subtle", "coherent", "nuance", "constraint", "integrate", "inference", "framework", "sophisticated"},
    "C2": {"paradigm", "multifaceted", "epistemic", "granularity", "counterfactual", "abstraction", "comprehensive", "salient"},
}


@dataclass
class ReadingPipelineResult:
    config: dict[str, Any]
    difficulty_report: dict[str, Any]
    materials: list[dict[str, Any]]
    quiz: dict[str, Any]
    i_plus_one_hit: bool
    llm_usage: dict[str, Any] | None = None


class ReadingPipelineError(RuntimeError):
    def __init__(self, *, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = str(code or "").strip() or "reading_generation_failed"
        self.message = str(message or "").strip() or "Reading pipeline failed"
        self.detail = str(detail or "").strip()

    def __str__(self) -> str:
        if self.detail:
            return f"{self.message} ({self.code}): {self.detail}"
        return f"{self.message} ({self.code})"


def _safe_json_load(raw: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw or "{}")
        if isinstance(payload, dict):
            return payload
    except Exception:
        return None
    return None


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        numeric = float(value)
        if numeric == numeric:  # noqa: PLR0124
            return numeric
    except Exception:
        return fallback
    return fallback


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _normalize_text(value: str, max_chars: int = 12000) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    return cleaned[: max(500, int(max_chars))]


def _tokenize_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z']+", str(text or "").lower())


def _count_words(text: str) -> int:
    return len(_tokenize_words(text))


def _split_sentences(text: str) -> list[str]:
    source = str(text or "").strip()
    if not source:
        return []
    chunks = re.split(r"(?<=[.!?])\s+", source)
    output: list[str] = []
    for chunk in chunks:
        cleaned = re.sub(r"\s+", " ", chunk).strip()
        if cleaned:
            output.append(cleaned)
    return output


def compute_readability(text: str) -> dict[str, Any]:
    safe_text = str(text or "").strip()
    metrics: dict[str, Any] = {
        "word_count": _count_words(safe_text),
        "sentence_count": max(1, len(_split_sentences(safe_text))) if safe_text else 0,
        "flesch_reading_ease": 0.0,
        "flesch_kincaid_grade": 0.0,
        "gunning_fog": 0.0,
        "automated_readability_index": 0.0,
        "text_standard": "",
    }
    if not safe_text:
        return metrics
    try:
        import textstat  # type: ignore

        metrics["flesch_reading_ease"] = _safe_float(textstat.flesch_reading_ease(safe_text), 0.0)
        metrics["flesch_kincaid_grade"] = _safe_float(textstat.flesch_kincaid_grade(safe_text), 0.0)
        metrics["gunning_fog"] = _safe_float(textstat.gunning_fog(safe_text), 0.0)
        metrics["automated_readability_index"] = _safe_float(textstat.automated_readability_index(safe_text), 0.0)
        metrics["text_standard"] = str(textstat.text_standard(safe_text) or "").strip()
    except Exception:
        pass
    return metrics


def _estimate_cefr_score(text: str) -> dict[str, Any]:
    words = _tokenize_words(text)
    if not words:
        return {"label": "B1", "numeric": CEFR_NUMERIC["B1"], "used_fallback": True}
    unique_words = set(words)
    scores: dict[str, float] = {}
    for level, vocab in CEFR_VOCAB.items():
        if not vocab:
            continue
        scores[level] = len(unique_words.intersection(vocab)) / max(1, len(vocab))
    if not scores:
        return {"label": "B1", "numeric": CEFR_NUMERIC["B1"], "used_fallback": True}
    best = sorted(scores.items(), key=lambda item: item[1], reverse=True)[0][0]
    if best == "A2":
        label = "A2"
    elif best == "B1":
        label = "B1"
    elif best == "B2":
        label = "B2"
    elif best == "C1":
        label = "C1"
    else:
        label = "C1-C2"
    return {"label": label, "numeric": CEFR_NUMERIC.get(label, 7.5), "used_fallback": False}


def estimate_text_level(text: str) -> dict[str, Any]:
    readability = compute_readability(text)
    grade = _safe_float(readability.get("flesch_kincaid_grade"), 0.0)
    cefr_data = _estimate_cefr_score(text)
    cefr_numeric = _safe_float(cefr_data.get("numeric"), 7.5)
    if grade <= 0:
        combined = cefr_numeric
    else:
        combined = _clamp((grade * 0.65) + (cefr_numeric * 0.35), 1.0, 12.0)
    return {
        "score": round(combined, 2),
        "grade": round(grade, 2),
        "cefr_label": str(cefr_data.get("label") or "B1"),
        "cefr_numeric": round(cefr_numeric, 2),
        "used_cefr_fallback": bool(cefr_data.get("used_fallback")),
        "readability": readability,
    }


def _resolve_user_level(level: str) -> dict[str, Any]:
    safe = str(level or "").strip().lower()
    numeric, cefr = LEVEL_META.get(safe, LEVEL_META["cet4"])
    return {"level": safe or "cet4", "numeric": numeric, "cefr": cefr}


def recommend_ratio_preset(gap_to_user: float) -> str:
    if gap_to_user >= 3:
        return "high_energy"
    if gap_to_user >= 1:
        return "long_term"
    return "low_energy"


def _resolve_llm_config(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    base_url = str(data.get("base_url") or os.getenv("READING_LLM_BASE_URL") or DEFAULT_LLM_BASE_URL).strip()
    api_key = str(data.get("api_key") or os.getenv("READING_LLM_API_KEY") or "").strip()
    model = str(data.get("model") or os.getenv("READING_LLM_MODEL") or "gpt-5.2").strip() or "gpt-5.2"
    support_json = bool(data.get("llm_support_json", False))
    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "llm_support_json": support_json,
    }


def _safe_int(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        return 0
    return parsed if parsed > 0 else 0


def _infer_llm_provider(base_url: str) -> str:
    safe_base_url = _normalize_llm_base_url(str(base_url or "").strip())
    try:
        host = str(urlparse(safe_base_url).netloc or "").lower().strip()
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


def _build_llm_usage_seed(*, llm_options: dict[str, Any]) -> dict[str, Any]:
    base_url = _normalize_llm_base_url(str(llm_options.get("base_url") or "").strip())
    model = str(llm_options.get("model") or "").strip()
    return {
        "llm_base_url": base_url,
        "llm_provider_effective": _infer_llm_provider(base_url),
        "llm_model_effective": model,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "llm_request_count": 0,
        "provider_request_id": "",
    }


def _ensure_usage_collector(usage_collector: dict[str, Any] | None, *, llm_options: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(usage_collector, dict):
        return None
    seed = _build_llm_usage_seed(llm_options=llm_options)
    for key, value in seed.items():
        usage_collector.setdefault(key, value)
    return usage_collector


def _append_usage_metrics(
    usage_collector: dict[str, Any] | None,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    provider_request_id: str = "",
) -> None:
    if not isinstance(usage_collector, dict):
        return
    safe_prompt_tokens = _safe_int(prompt_tokens)
    safe_completion_tokens = _safe_int(completion_tokens)
    safe_total_tokens = _safe_int(total_tokens)
    if safe_total_tokens <= 0:
        safe_total_tokens = safe_prompt_tokens + safe_completion_tokens
    usage_collector["prompt_tokens"] = _safe_int(usage_collector.get("prompt_tokens")) + safe_prompt_tokens
    usage_collector["completion_tokens"] = _safe_int(usage_collector.get("completion_tokens")) + safe_completion_tokens
    usage_collector["total_tokens"] = _safe_int(usage_collector.get("total_tokens")) + safe_total_tokens
    usage_collector["llm_request_count"] = _safe_int(usage_collector.get("llm_request_count")) + 1
    safe_provider_request_id = str(provider_request_id or "").strip()
    if safe_provider_request_id:
        usage_collector["provider_request_id"] = safe_provider_request_id


def _extract_usage_metrics_from_payload(payload: dict[str, Any]) -> tuple[int, int, int, str]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt_tokens = _safe_int(usage.get("input_tokens") or usage.get("prompt_tokens"))
    completion_tokens = _safe_int(usage.get("output_tokens") or usage.get("completion_tokens"))
    total_tokens = _safe_int(usage.get("total_tokens"))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    provider_request_id = str(payload.get("id") or "").strip()
    return prompt_tokens, completion_tokens, total_tokens, provider_request_id


def _extract_usage_metrics_from_chat_response(response: Any) -> tuple[int, int, int, str]:
    usage = getattr(response, "usage", None)
    if isinstance(usage, dict):
        usage_obj: dict[str, Any] = usage
    else:
        usage_obj = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
        }
    prompt_tokens = _safe_int(usage_obj.get("prompt_tokens"))
    completion_tokens = _safe_int(usage_obj.get("completion_tokens"))
    total_tokens = _safe_int(usage_obj.get("total_tokens"))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    provider_request_id = str(getattr(response, "id", "") or "").strip()
    return prompt_tokens, completion_tokens, total_tokens, provider_request_id


def _call_llm_json(
    *,
    prompt: str,
    llm_options: dict[str, Any],
    usage_collector: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    _ensure_usage_collector(usage_collector, llm_options=llm_options)
    api_key = str(llm_options.get("api_key") or "").strip()
    if not api_key:
        return None
    raw_base_url = str(llm_options.get("base_url") or "").strip()
    model = str(llm_options.get("model") or "gpt-5.2").strip() or "gpt-5.2"
    support_json = bool(llm_options.get("llm_support_json"))
    normalized_base_url = _normalize_llm_base_url(raw_base_url)
    protocol_candidates = _infer_llm_protocol_candidates(raw_base_url, model)
    print(
        f"[DEBUG] Reading LLM protocol candidates={protocol_candidates} "
        f"base_url={normalized_base_url} model={model}"
    )
    failure_details: list[str] = []

    for protocol in protocol_candidates:
        if protocol == "responses":
            endpoint = f"{normalized_base_url.rstrip('/')}/responses"
            base_payload = {
                "model": model,
                "input": [
                    {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": "You are a strict JSON assistant."}],
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": prompt}],
                    },
                ],
            }
            payloads: list[dict[str, Any]] = []
            if support_json:
                payload_with_json = dict(base_payload)
                payload_with_json["text"] = {"format": {"type": "json_object"}}
                payloads.append(payload_with_json)
            payloads.append(base_payload)
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            print(f"[DEBUG] Reading LLM request protocol=responses endpoint={endpoint}")

            last_status: int | None = None
            last_error = ""
            for index, item in enumerate(payloads):
                if index > 0:
                    print("[DEBUG] Reading LLM retrying responses payload without JSON format hint")
                try:
                    response = requests.post(endpoint, headers=headers, json=item, timeout=120)
                except Exception as exc:
                    last_status = None
                    last_error = f"request_error={str(exc)[:420]}"
                    continue

                if int(response.status_code) >= 400:
                    last_status = int(response.status_code)
                    last_error = f"body={str(response.text or '')[:420]}"
                    continue

                try:
                    body = response.json()
                except Exception:
                    last_status = int(response.status_code)
                    last_error = "body_invalid_json"
                    continue
                if not isinstance(body, dict):
                    last_status = int(response.status_code)
                    last_error = "body_not_dict"
                    continue
                extracted = _extract_responses_output_text(body)
                if not extracted:
                    last_status = int(response.status_code)
                    last_error = "output_text_empty"
                    continue
                parsed = _safe_json_load(extracted)
                if isinstance(parsed, dict):
                    prompt_tokens, completion_tokens, total_tokens, provider_request_id = _extract_usage_metrics_from_payload(body)
                    _append_usage_metrics(
                        usage_collector,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        provider_request_id=provider_request_id,
                    )
                    print("[DEBUG] Reading LLM success protocol=responses")
                    return parsed
                last_status = int(response.status_code)
                last_error = "output_not_json"

            status_tag = str(last_status) if last_status is not None else "request_error"
            failure_detail = f"protocol=responses; status={status_tag}; detail={last_error[:420]}"
            failure_details.append(failure_detail)
            print(f"[DEBUG] Reading LLM failed {failure_detail}")
            if _should_fallback_protocol(last_status, last_error):
                print("[DEBUG] Reading LLM falling back from responses to next protocol")
                continue
            break

        print(f"[DEBUG] Reading LLM request protocol=chat.completions base_url={normalized_base_url}")
        try:
            client = OpenAI(api_key=api_key, base_url=normalized_base_url)
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a strict JSON assistant."},
                    {"role": "user", "content": prompt},
                ],
            }
            if support_json:
                kwargs["response_format"] = {"type": "json_object"}
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:
            error_detail = f"request_error={str(exc)[:420]}"
            failure_detail = f"protocol=chat.completions; status=request_error; detail={error_detail}"
            failure_details.append(failure_detail)
            print(f"[DEBUG] Reading LLM failed {failure_detail}")
            if _should_fallback_protocol(None, error_detail):
                print("[DEBUG] Reading LLM falling back from chat.completions to next protocol")
                continue
            break

        content = str(
            ((response.choices or [SimpleNamespace(message=SimpleNamespace(content=""))])[0].message.content) or ""
        ).strip()
        if not content:
            failure_details.append("protocol=chat.completions; status=200; detail=empty_content")
            print("[DEBUG] Reading LLM failed protocol=chat.completions; status=200; detail=empty_content")
            break
        parsed = _safe_json_load(content)
        if isinstance(parsed, dict):
            prompt_tokens, completion_tokens, total_tokens, provider_request_id = _extract_usage_metrics_from_chat_response(response)
            _append_usage_metrics(
                usage_collector,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                provider_request_id=provider_request_id,
            )
            print("[DEBUG] Reading LLM success protocol=chat.completions")
            return parsed
        failure_details.append("protocol=chat.completions; status=200; detail=output_not_json")
        print("[DEBUG] Reading LLM failed protocol=chat.completions; status=200; detail=output_not_json")
        break

    if failure_details:
        joined = " | ".join(failure_details)
        print(f"[DEBUG] Reading LLM request failed all protocols details={joined[:1200]}")
    return None


ANCHOR_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "before",
    "being",
    "between",
    "could",
    "every",
    "first",
    "from",
    "have",
    "into",
    "just",
    "many",
    "more",
    "most",
    "other",
    "over",
    "such",
    "than",
    "that",
    "their",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "under",
    "very",
    "what",
    "when",
    "which",
    "while",
    "with",
    "would",
}


def _normalize_anchor_token(value: str) -> str:
    token = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return re.sub(r"[^a-z0-9\s'-]", "", token).strip()


def _collect_semantic_anchors(source_text: str, summary_terms: dict[str, Any] | None) -> list[str]:
    anchors: list[str] = []
    if isinstance(summary_terms, dict):
        theme = _normalize_anchor_token(str(summary_terms.get("theme") or ""))
        if theme:
            anchors.append(theme)
        terms = summary_terms.get("terms")
        if isinstance(terms, list):
            for item in terms:
                if not isinstance(item, dict):
                    continue
                src = _normalize_anchor_token(str(item.get("src") or item.get("term") or ""))
                if src:
                    anchors.append(src)
    frequency: dict[str, int] = {}
    for token in _tokenize_words(source_text):
        if len(token) < 5 or token in ANCHOR_STOPWORDS:
            continue
        frequency[token] = frequency.get(token, 0) + 1
    for token, _count in sorted(frequency.items(), key=lambda item: (-item[1], -len(item[0]), item[0])):
        anchors.append(token)
        if len(anchors) >= 24:
            break
    deduped: list[str] = []
    seen: set[str] = set()
    for item in anchors:
        normalized = _normalize_anchor_token(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
        if len(deduped) >= 16:
            break
    return deduped


def _measure_anchor_coverage(text: str, anchors: list[str]) -> tuple[float, list[str], list[str]]:
    candidates = [_normalize_anchor_token(item) for item in anchors if _normalize_anchor_token(item)]
    if not candidates:
        return 1.0, [], []
    safe_text = str(text or "").lower()
    hit: list[str] = []
    miss: list[str] = []
    for item in candidates:
        if " " in item:
            matched = item in safe_text
        else:
            matched = bool(re.search(rf"\b{re.escape(item)}\b", safe_text))
        if matched:
            hit.append(item)
        else:
            miss.append(item)
    coverage = len(hit) / max(1, len(candidates))
    return coverage, hit, miss


def _build_material_prompt(
    *,
    kind: str,
    source_text: str,
    target_words: int,
    difficulty_tier: str,
    genre: str,
    anchors: list[str],
    revision_feedback: list[str],
) -> str:
    min_words = max(80, int(math.ceil(max(1, target_words) * 0.75)))
    max_words = int(max(min_words, math.floor(max(1, target_words) * 1.25)))
    min_sentences = 5 if kind == "intensive" else 6
    anchor_line = ", ".join(anchors[:10]) if anchors else "none"
    revision_line = (
        "\n".join(f"- {item}" for item in revision_feedback if str(item or "").strip())
        if revision_feedback
        else "- no previous feedback"
    )
    return (
        "Return JSON only with schema {\"text\":\"...\"}.\n"
        f"Task: generate one {kind} English reading material from the source.\n"
        f"Difficulty tier: {difficulty_tier}. Genre: {genre}. {GENRE_HINTS.get(genre, '')}\n"
        f"Length constraint: {min_words}-{max_words} words. Sentence minimum: {min_sentences}.\n"
        "Non-negotiable constraints:\n"
        "1) Keep source facts, causality, and stance consistent; do not invent new key facts.\n"
        "2) Rewrite in natural English suitable for learners, but preserve the source meaning.\n"
        "3) Keep paragraph coherent, avoid repetitive sentences, and do not output bullet lists.\n"
        "4) Use named entities and terminology from source when relevant.\n"
        f"Semantic anchors (include naturally when applicable): {anchor_line}\n"
        "If previous attempts failed, fix these issues first:\n"
        f"{revision_line}\n\n"
        "Source:\n"
        f"{source_text}\n"
    )


def _validate_generated_text_quality(
    *,
    kind: str,
    generated_text: str,
    target_words: int,
    anchors: list[str],
) -> dict[str, Any]:
    reasons: list[str] = []
    text = str(generated_text or "").strip()
    if not text:
        reasons.append("text_empty")
        return {"ok": False, "reasons": reasons, "metrics": {"word_count": 0}}

    words = _count_words(text)
    min_words = max(80, int(math.ceil(max(1, target_words) * 0.75)))
    max_words = int(max(min_words, math.floor(max(1, target_words) * 1.25)))
    if words < min_words or words > max_words:
        reasons.append(f"word_count_out_of_range({words},{min_words}-{max_words})")

    sentences = _split_sentences(text)
    min_sentences = 5 if kind == "intensive" else 6
    if len(sentences) < min_sentences:
        reasons.append(f"sentence_count_too_low({len(sentences)}<{min_sentences})")

    normalized_sentences = [
        re.sub(r"[^a-z0-9\s]", "", sentence.lower()).strip()
        for sentence in sentences
        if str(sentence or "").strip()
    ]
    unique_count = len(set(item for item in normalized_sentences if item))
    repeat_ratio = 0.0
    if normalized_sentences:
        repeat_ratio = (len(normalized_sentences) - unique_count) / len(normalized_sentences)
    if repeat_ratio >= 0.25:
        reasons.append(f"repeat_ratio_too_high({repeat_ratio:.3f})")

    anchor_coverage, hit_anchors, miss_anchors = _measure_anchor_coverage(text=text, anchors=anchors)
    if anchor_coverage < 0.25:
        reasons.append(f"anchor_coverage_too_low({anchor_coverage:.3f})")

    metrics = {
        "word_count": words,
        "min_words": min_words,
        "max_words": max_words,
        "sentence_count": len(sentences),
        "min_sentences": min_sentences,
        "repeat_ratio": round(repeat_ratio, 4),
        "anchor_coverage": round(anchor_coverage, 4),
        "anchor_hit": hit_anchors,
        "anchor_miss": miss_anchors[:8],
    }
    return {"ok": len(reasons) == 0, "reasons": reasons, "metrics": metrics}


def _generate_text_with_quality_gate(
    *,
    kind: str,
    source_text: str,
    target_words: int,
    difficulty_tier: str,
    genre: str,
    anchors: list[str],
    llm_options: dict[str, Any],
    usage_collector: dict[str, Any] | None = None,
    retry_budget: int = QUALITY_RETRY_BUDGET,
) -> str:
    revision_feedback: list[str] = []
    attempt_failures: list[str] = []
    safe_retry_budget = max(0, int(retry_budget))
    for attempt in range(safe_retry_budget + 1):
        prompt = _build_material_prompt(
            kind=kind,
            source_text=source_text,
            target_words=target_words,
            difficulty_tier=difficulty_tier,
            genre=genre,
            anchors=anchors,
            revision_feedback=revision_feedback,
        )
        try:
            llm_payload = _call_llm_json(prompt=prompt, llm_options=llm_options, usage_collector=usage_collector)
        except Exception as exc:
            llm_payload = None
            revision_feedback = [f"llm_request_error={str(exc)[:120]}"]
            attempt_failures.append(f"attempt={attempt + 1}:llm_request_error")
            continue
        generated = str((llm_payload or {}).get("text") or "").strip()
        quality = _validate_generated_text_quality(
            kind=kind,
            generated_text=generated,
            target_words=target_words,
            anchors=anchors,
        )
        if quality.get("ok"):
            print(
                f"[DEBUG] Reading text quality passed kind={kind} attempt={attempt + 1} "
                f"metrics={quality.get('metrics')}"
            )
            return generated
        reasons = quality.get("reasons") if isinstance(quality.get("reasons"), list) else []
        revision_feedback = [str(item) for item in reasons[:4]]
        metrics = quality.get("metrics") if isinstance(quality.get("metrics"), dict) else {}
        attempt_failures.append(f"attempt={attempt + 1}:reasons={revision_feedback};metrics={metrics}")
        print(
            f"[DEBUG] Reading text quality failed kind={kind} attempt={attempt + 1} "
            f"reasons={revision_feedback} metrics={metrics}"
        )
    detail = " | ".join(attempt_failures)[:1800]
    raise ReadingPipelineError(
        code="reading_generation_quality_failed",
        message="Reading text generation failed quality checks after retries",
        detail=detail,
    )


def _build_quiz_prompt(*, intensive_text: str, revision_feedback: list[str]) -> str:
    revision_line = (
        "\n".join(f"- {item}" for item in revision_feedback if str(item or "").strip())
        if revision_feedback
        else "- no previous feedback"
    )
    return (
        "Return JSON only with schema:\n"
        "{"
        "\"choice_questions\":[{\"question_id\":\"c1\",\"question\":\"...\",\"choices\":[\"A\",\"B\",\"C\",\"D\"],"
        "\"answer_index\":0,\"explanation\":\"...\"}],"
        "\"short_questions\":[{\"question_id\":\"s1\",\"question\":\"...\",\"reference_answer\":\"...\"}]"
        "}.\n"
        "Generate exactly 3 choice_questions and exactly 3 short_questions.\n"
        "Requirements:\n"
        "1) Questions must be grounded in the passage facts.\n"
        "2) Choice options must be plausible; only one correct answer per item.\n"
        "3) Keep all text in English.\n"
        "4) reference_answer must be concise and semantically tied to the passage.\n"
        "If previous attempts failed, fix these issues first:\n"
        f"{revision_line}\n\n"
        "Passage:\n"
        f"{intensive_text}\n"
    )


def _validate_quiz_payload(*, payload: dict[str, Any], intensive_text: str) -> dict[str, Any]:
    reasons: list[str] = []
    safe_payload = payload if isinstance(payload, dict) else {}
    choice_rows = safe_payload.get("choice_questions") if isinstance(safe_payload.get("choice_questions"), list) else []
    short_rows = safe_payload.get("short_questions") if isinstance(safe_payload.get("short_questions"), list) else []

    if len(choice_rows) != 3:
        reasons.append(f"choice_count_invalid({len(choice_rows)})")
    if len(short_rows) != 3:
        reasons.append(f"short_count_invalid({len(short_rows)})")

    normalized_choices: list[dict[str, Any]] = []
    choice_question_seen: set[str] = set()
    for index, row in enumerate(choice_rows[:3], start=1):
        if not isinstance(row, dict):
            reasons.append(f"choice_{index}_not_object")
            continue
        question = str(row.get("question") or "").strip()
        if not question:
            reasons.append(f"choice_{index}_question_empty")
        norm_question = re.sub(r"\s+", " ", question.lower()).strip()
        if norm_question in choice_question_seen:
            reasons.append(f"choice_{index}_question_duplicate")
        elif norm_question:
            choice_question_seen.add(norm_question)
        raw_choices = row.get("choices") if isinstance(row.get("choices"), list) else []
        options = [str(item or "").strip() for item in raw_choices[:4]]
        if len(options) != 4 or any(not item for item in options):
            reasons.append(f"choice_{index}_options_invalid")
        answer_index = int(_safe_float(row.get("answer_index"), -1))
        if answer_index < 0 or answer_index > 3:
            reasons.append(f"choice_{index}_answer_index_invalid")
        explanation = str(row.get("explanation") or "").strip()
        if not explanation:
            reasons.append(f"choice_{index}_explanation_empty")
        normalized_choices.append(
            {
                "question_id": f"c{index}",
                "question": question,
                "choices": options if len(options) == 4 else ["", "", "", ""],
                "answer_index": answer_index if 0 <= answer_index <= 3 else 0,
                "explanation": explanation,
            }
        )

    source_vocab = {item for item in _tokenize_words(intensive_text) if len(item) >= 4 and item not in ANCHOR_STOPWORDS}
    normalized_shorts: list[dict[str, Any]] = []
    short_question_seen: set[str] = set()
    for index, row in enumerate(short_rows[:3], start=1):
        if not isinstance(row, dict):
            reasons.append(f"short_{index}_not_object")
            continue
        question = str(row.get("question") or "").strip()
        if not question:
            reasons.append(f"short_{index}_question_empty")
        norm_question = re.sub(r"\s+", " ", question.lower()).strip()
        if norm_question in short_question_seen:
            reasons.append(f"short_{index}_question_duplicate")
        elif norm_question:
            short_question_seen.add(norm_question)
        reference_answer = str(row.get("reference_answer") or "").strip()
        if not reference_answer:
            reasons.append(f"short_{index}_reference_empty")
        ref_vocab = {item for item in _tokenize_words(reference_answer) if len(item) >= 4 and item not in ANCHOR_STOPWORDS}
        overlap_ratio = len(ref_vocab.intersection(source_vocab)) / max(1, len(ref_vocab)) if ref_vocab else 0.0
        if ref_vocab and overlap_ratio < 0.12:
            reasons.append(f"short_{index}_reference_unrelated({overlap_ratio:.3f})")
        normalized_shorts.append(
            {
                "question_id": f"s{index}",
                "question": question,
                "reference_answer": reference_answer,
            }
        )

    metrics = {
        "choice_count": len(choice_rows),
        "short_count": len(short_rows),
        "choice_validated": len(normalized_choices),
        "short_validated": len(normalized_shorts),
    }
    return {
        "ok": len(reasons) == 0 and len(normalized_choices) == 3 and len(normalized_shorts) == 3,
        "reasons": reasons,
        "metrics": metrics,
        "quiz": {"choice_questions": normalized_choices, "short_questions": normalized_shorts},
    }


def _generate_quiz_with_quality_gate(
    *,
    intensive_text: str,
    llm_options: dict[str, Any],
    usage_collector: dict[str, Any] | None = None,
    retry_budget: int = QUALITY_RETRY_BUDGET,
) -> dict[str, Any]:
    revision_feedback: list[str] = []
    attempt_failures: list[str] = []
    safe_retry_budget = max(0, int(retry_budget))
    for attempt in range(safe_retry_budget + 1):
        prompt = _build_quiz_prompt(intensive_text=intensive_text, revision_feedback=revision_feedback)
        try:
            llm_payload = _call_llm_json(prompt=prompt, llm_options=llm_options, usage_collector=usage_collector)
        except Exception as exc:
            llm_payload = None
            revision_feedback = [f"llm_request_error={str(exc)[:120]}"]
            attempt_failures.append(f"attempt={attempt + 1}:llm_request_error")
            continue
        safe_payload = llm_payload if isinstance(llm_payload, dict) else {}
        quality = _validate_quiz_payload(payload=safe_payload, intensive_text=intensive_text)
        if quality.get("ok"):
            print(
                f"[DEBUG] Reading quiz quality passed attempt={attempt + 1} "
                f"metrics={quality.get('metrics')}"
            )
            return quality.get("quiz") if isinstance(quality.get("quiz"), dict) else {"choice_questions": [], "short_questions": []}
        reasons = quality.get("reasons") if isinstance(quality.get("reasons"), list) else []
        metrics = quality.get("metrics") if isinstance(quality.get("metrics"), dict) else {}
        revision_feedback = [str(item) for item in reasons[:4]]
        attempt_failures.append(f"attempt={attempt + 1}:reasons={revision_feedback};metrics={metrics}")
        print(
            f"[DEBUG] Reading quiz quality failed attempt={attempt + 1} "
            f"reasons={revision_feedback} metrics={metrics}"
        )
    detail = " | ".join(attempt_failures)[:1800]
    raise ReadingPipelineError(
        code="reading_quiz_generation_failed",
        message="Reading quiz generation failed quality checks after retries",
        detail=detail,
    )


def generate_reading_material(
    *,
    source: dict[str, Any],
    user_level: str,
    scope: str,
    ratio_preset: str,
    difficulty_tier: str,
    genre: str,
    llm_options_payload: dict[str, Any] | None = None,
) -> ReadingPipelineResult:
    source_text = _normalize_text(str(source.get("source_text") or ""), 12000)
    summary_terms = source.get("summary_terms") if isinstance(source.get("summary_terms"), dict) else {}
    semantic_anchors = _collect_semantic_anchors(source_text=source_text, summary_terms=summary_terms)
    source_level = estimate_text_level(source_text)
    user_meta = _resolve_user_level(user_level)
    ratio_key = ratio_preset if ratio_preset in RATIO_PRESETS else "long_term"
    ratio_intensive, ratio_extensive = RATIO_PRESETS[ratio_key]
    safe_scope = scope if scope in {"all", "intensive", "extensive"} else "all"
    safe_tier = difficulty_tier if difficulty_tier in DIFFICULTY_OFFSETS else "balanced"
    safe_genre = genre if genre in {"news", "science", "story", "workplace"} else "news"

    source_word_count = max(120, _count_words(source_text))
    word_budget_total = int(_clamp(source_word_count * 0.7, 220.0, 1200.0))
    intensive_target = int(max(80, round(word_budget_total * ratio_intensive)))
    extensive_target = int(max(80, round(word_budget_total * ratio_extensive)))

    llm_options = _resolve_llm_config(llm_options_payload)
    llm_usage = _build_llm_usage_seed(llm_options=llm_options)
    materials: list[dict[str, Any]] = []
    intensive_text = ""
    extensive_text = ""
    if safe_scope in {"all", "intensive"}:
        intensive_text = _generate_text_with_quality_gate(
            kind="intensive",
            source_text=source_text,
            target_words=intensive_target,
            difficulty_tier=safe_tier,
            genre=safe_genre,
            anchors=semantic_anchors,
            llm_options=llm_options,
            usage_collector=llm_usage,
            retry_budget=QUALITY_RETRY_BUDGET,
        )
        materials.append(
            {
                "kind": "intensive",
                "text": intensive_text,
                "word_count": _count_words(intensive_text),
                "target_word_count": intensive_target,
                "generated": bool(intensive_text),
            }
        )
    if safe_scope in {"all", "extensive"}:
        extensive_text = _generate_text_with_quality_gate(
            kind="extensive",
            source_text=source_text,
            target_words=extensive_target,
            difficulty_tier=safe_tier,
            genre=safe_genre,
            anchors=semantic_anchors,
            llm_options=llm_options,
            usage_collector=llm_usage,
            retry_budget=QUALITY_RETRY_BUDGET,
        )
        materials.append(
            {
                "kind": "extensive",
                "text": extensive_text,
                "word_count": _count_words(extensive_text),
                "target_word_count": extensive_target,
                "generated": bool(extensive_text),
            }
        )

    generated_levels = [estimate_text_level(item.get("text") or "")["score"] for item in materials if str(item.get("text") or "").strip()]
    generated_level = float(sum(generated_levels) / max(1, len(generated_levels))) if generated_levels else 0.0
    gap_to_user = source_level["score"] - user_meta["numeric"]
    recommended_ratio = recommend_ratio_preset(gap_to_user)
    target_level = user_meta["numeric"] + DIFFICULTY_OFFSETS.get(safe_tier, 1.0)
    i_plus_one_hit = abs(generated_level - target_level) <= 1.0 if generated_level > 0 else False

    difficulty_report = {
        "source_score": round(float(source_level["score"]), 2),
        "source_level": round(float(source_level["score"]), 2),
        "generated_level": round(float(generated_level), 2),
        "target_level": round(float(target_level), 2),
        "gap_to_user": round(float(gap_to_user), 2),
        "recommended_ratio_preset": recommended_ratio,
        "hit_i_plus_one": i_plus_one_hit,
        "used_cefr_fallback": bool(source_level.get("used_cefr_fallback")),
        "detail": {
            "source_grade": source_level.get("grade"),
            "source_cefr_label": source_level.get("cefr_label"),
            "source_cefr_numeric": source_level.get("cefr_numeric"),
            "source_readability": source_level.get("readability"),
            "semantic_anchor_count": len(semantic_anchors),
            "quality_strategy": QUALITY_STRATEGY_TAG,
        },
    }
    config = {
        "scope": safe_scope,
        "ratio_preset": ratio_key,
        "difficulty_tier": safe_tier,
        "genre": safe_genre,
        "word_budget_total": word_budget_total,
        "quality_strategy": QUALITY_STRATEGY_TAG,
        "quality_retry_budget": QUALITY_RETRY_BUDGET,
    }
    quiz = (
        _generate_quiz_with_quality_gate(
            intensive_text=intensive_text,
            llm_options=llm_options,
            usage_collector=llm_usage,
            retry_budget=QUALITY_RETRY_BUDGET,
        )
        if intensive_text
        else {"choice_questions": [], "short_questions": []}
    )
    return ReadingPipelineResult(
        config=config,
        difficulty_report=difficulty_report,
        materials=materials,
        quiz=quiz,
        i_plus_one_hit=i_plus_one_hit,
        llm_usage=llm_usage,
    )


def grade_short_answer(
    *,
    question: str,
    reference_answer: str,
    answer_text: str,
    llm_options_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_answer = str(answer_text or "").strip()
    safe_reference = str(reference_answer or "").strip()
    safe_question = str(question or "").strip()
    if not safe_answer:
        dims = [
            {"name": "内容准确性", "score": 0.0, "max_score": 5.0, "comment": "未作答。"},
            {"name": "信息完整性", "score": 0.0, "max_score": 5.0, "comment": "未提供要点。"},
            {"name": "语言表达", "score": 0.0, "max_score": 5.0, "comment": "未提供可评估文本。"},
            {"name": "逻辑连贯性", "score": 0.0, "max_score": 5.0, "comment": "未提供可评估结构。"},
        ]
        return {
            "total_score": 0.0,
            "max_score": 20.0,
            "dimensions": dims,
            "overall_comment": "请先完成作答再提交。",
            "reference_answer": safe_reference,
        }

    llm_options = _resolve_llm_config(llm_options_payload)
    llm_usage = _build_llm_usage_seed(llm_options=llm_options)
    prompt = (
        "Return JSON only with schema: "
        "{\"dimensions\":[{\"name\":\"内容准确性|信息完整性|语言表达|逻辑连贯性\",\"score\":0-5,\"comment\":\"...\"}],"
        "\"overall_comment\":\"...\",\"reference_answer\":\"...\"}.\n"
        "Use Chinese for comments. Keep score to one decimal.\n\n"
        f"Question: {safe_question}\n"
        f"Reference answer: {safe_reference}\n"
        f"User answer: {safe_answer}\n"
    )
    llm_payload = None
    try:
        llm_payload = _call_llm_json(prompt=prompt, llm_options=llm_options, usage_collector=llm_usage)
    except Exception:
        llm_payload = None
    if isinstance(llm_payload, dict):
        dims = llm_payload.get("dimensions")
        if isinstance(dims, list) and dims:
            normalized_dims: list[dict[str, Any]] = []
            for item in dims[:4]:
                if not isinstance(item, dict):
                    continue
                normalized_dims.append(
                    {
                        "name": str(item.get("name") or "").strip() or "维度",
                        "score": round(_clamp(_safe_float(item.get("score"), 0.0), 0.0, 5.0), 1),
                        "max_score": 5.0,
                        "comment": str(item.get("comment") or "").strip() or "已评估。",
                    }
                )
            if normalized_dims:
                while len(normalized_dims) < 4:
                    normalized_dims.append({"name": f"维度{len(normalized_dims)+1}", "score": 0.0, "max_score": 5.0, "comment": "已评估。"})
                total = round(sum(_safe_float(item.get("score"), 0.0) for item in normalized_dims), 1)
                return {
                    "total_score": total,
                    "max_score": 20.0,
                    "dimensions": normalized_dims,
                    "overall_comment": str(llm_payload.get("overall_comment") or "").strip() or "已完成批改。",
                    "reference_answer": str(llm_payload.get("reference_answer") or "").strip() or safe_reference,
                    "_llm_usage": llm_usage,
                }

    answer_words = set(_tokenize_words(safe_answer))
    ref_words = set(_tokenize_words(safe_reference))
    overlap = len(answer_words.intersection(ref_words)) / max(1, len(ref_words)) if ref_words else 0.0
    content_score = round(_clamp(overlap * 8.0, 0.0, 5.0), 1)
    completeness_ratio = _count_words(safe_answer) / max(1, _count_words(safe_reference) or 40)
    completeness_score = round(_clamp(completeness_ratio * 3.2, 0.0, 5.0), 1)
    sentence_count = max(1, len(_split_sentences(safe_answer)))
    language_score = round(_clamp((min(1.0, _count_words(safe_answer) / 60.0) * 2.5) + (1.2 if sentence_count >= 2 else 0.8), 0.0, 5.0), 1)
    connectors = len(re.findall(r"\b(because|therefore|however|first|second|finally|so)\b", safe_answer.lower()))
    logic_score = round(_clamp((sentence_count * 0.8) + (connectors * 0.5), 0.0, 5.0), 1)
    dims = [
        {"name": "内容准确性", "score": content_score, "max_score": 5.0, "comment": "关注是否覆盖题干核心信息。"},
        {"name": "信息完整性", "score": completeness_score, "max_score": 5.0, "comment": "关注要点是否齐全。"},
        {"name": "语言表达", "score": language_score, "max_score": 5.0, "comment": "关注表达清晰度与句式质量。"},
        {"name": "逻辑连贯性", "score": logic_score, "max_score": 5.0, "comment": "关注句间衔接和论述顺序。"},
    ]
    total_score = round(sum(item["score"] for item in dims), 1)
    return {
        "total_score": total_score,
        "max_score": 20.0,
        "dimensions": dims,
        "overall_comment": "已完成快速批改。建议根据参考答案补全关键细节并优化句间衔接。",
        "reference_answer": safe_reference,
        "_llm_usage": llm_usage,
    }
