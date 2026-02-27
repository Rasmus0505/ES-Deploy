from __future__ import annotations

import json

from .prompts import get_summary_prompt
from .types import CancelGuard, FlowConfig, FlowError, JsonChatFn, SummaryTerms


def _normalize_term(item: dict) -> dict[str, str] | None:
    src = str((item or {}).get("src") or "").strip()
    tgt = str((item or {}).get("tgt") or "").strip()
    note = str((item or {}).get("note") or "").strip()
    if not src:
        return None
    return {
        "src": src,
        "tgt": tgt,
        "note": note,
    }


def extract_summary_terms(
    *,
    sentences: list[dict],
    config: FlowConfig,
    chat_json: JsonChatFn,
    cancel_guard: CancelGuard | None = None,
) -> SummaryTerms:
    if cancel_guard:
        cancel_guard()

    source_lines = [str((row or {}).get("text") or "").strip() for row in sentences or []]
    source_lines = [line for line in source_lines if line]
    merged = " ".join(source_lines)
    trimmed = merged[: max(200, int(config.summary_max_chars))]
    if not trimmed:
        return SummaryTerms(theme="", terms=[])

    prompt = get_summary_prompt(
        text=trimmed,
        source_language=config.source_language,
        target_language=config.target_language,
    )
    payload = chat_json(prompt)
    if not isinstance(payload, dict):
        raise FlowError(
            "summary_terms",
            "summary_invalid",
            "术语抽取返回格式错误",
            detail=str(payload)[:600],
        )

    raw_terms = payload.get("terms")
    if raw_terms is None:
        raw_terms = []
    if not isinstance(raw_terms, list):
        raise FlowError(
            "summary_terms",
            "summary_invalid",
            "术语抽取返回格式错误",
            detail=json.dumps(payload, ensure_ascii=False)[:600],
        )

    terms: list[dict[str, str]] = []
    for item in raw_terms:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_term(item)
        if normalized:
            terms.append(normalized)
        if len(terms) >= 15:
            break

    theme = str(payload.get("theme") or "").strip()
    return SummaryTerms(theme=theme, terms=terms)


def search_terms_in_text(text: str, terms: list[dict[str, str]]) -> list[dict[str, str]]:
    source = str(text or "").lower()
    matched: list[dict[str, str]] = []
    for term in terms or []:
        src = str((term or {}).get("src") or "").strip()
        if not src:
            continue
        if src.lower() in source:
            matched.append(
                {
                    "src": src,
                    "tgt": str((term or {}).get("tgt") or "").strip(),
                    "note": str((term or {}).get("note") or "").strip(),
                }
            )
    return matched
