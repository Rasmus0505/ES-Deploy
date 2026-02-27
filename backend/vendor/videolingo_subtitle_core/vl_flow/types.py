from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


JsonChatFn = Callable[[str], dict[str, Any]]
CancelGuard = Callable[[], None]
ProgressReporter = Callable[[dict[str, Any]], None]


class FlowError(RuntimeError):
    def __init__(self, stage: str, code: str, message: str, detail: str | None = None):
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.message = message
        self.detail = detail or ""


@dataclass
class SummaryTerms:
    theme: str = ""
    terms: list[dict[str, str]] = field(default_factory=list)


@dataclass
class FlowConfig:
    source_language: str = "en"
    target_language: str = "zh"
    max_split_length: int = 20
    meaning_split_rounds: int = 3
    subtitle_max_length: int = 75
    subtitle_target_multiplier: float = 1.2
    subtitle_split_rounds: int = 3
    translate_chunk_chars: int = 600
    translate_chunk_max_lines: int = 10
    translate_context_prev: int = 3
    translate_context_next: int = 2
    summary_max_chars: int = 1800
