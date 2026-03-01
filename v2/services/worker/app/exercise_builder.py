from __future__ import annotations

import re


def tokenize_words(sentence: str) -> list[str]:
    normalized = re.sub(r"[^A-Za-z0-9'\-\s]", ' ', sentence)
    return [part for part in normalized.split() if part]


def build_item_payload(*, text: str) -> tuple[list[str], list[str]]:
    words = tokenize_words(text)
    accepted = [word.lower().replace("'", '') for word in words]
    return words, accepted


def check_needs_review(start_ms: int, end_ms: int) -> bool:
    duration = max(0, int(end_ms) - int(start_ms))
    return duration > 15000 or duration < 800
