from __future__ import annotations

from typing import Any

from .types import FlowConfig


def _read_option(options_like: Any, key: str, default: Any) -> Any:
    if isinstance(options_like, dict):
        return options_like.get(key, default)
    return getattr(options_like, key, default)


def build_flow_config(options_like: Any) -> FlowConfig:
    source_language = str(_read_option(options_like, "source_language", "en") or "en").strip() or "en"
    target_language = str(_read_option(options_like, "target_language", "zh") or "zh").strip() or "zh"
    return FlowConfig(
        source_language=source_language,
        target_language=target_language,
    )
