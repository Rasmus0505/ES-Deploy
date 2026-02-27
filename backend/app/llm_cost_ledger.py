from __future__ import annotations

import csv
import json
import os
import threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping


LEDGER_FIELDS = [
    "recorded_at",
    "scene",
    "owner_id",
    "provider_request_id",
    "llm_base_url",
    "llm_provider_effective",
    "llm_model_effective",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_price_cny_per_million_tokens",
    "output_price_cny_per_million_tokens",
    "cost_cny",
    "price_source",
    "note",
]

_DEFAULT_MODEL_TIER_PRICES = {
    "qwen3.5-plus": [
        (128_000, Decimal("0.8"), Decimal("4.8")),
        (256_000, Decimal("2"), Decimal("12")),
        (1_000_000, Decimal("4"), Decimal("24")),
    ]
}

_DEFAULT_PROVIDER_TIER_PRICES: dict[str, list[tuple[int, Decimal, Decimal]]] = {}

_WRITE_LOCK = threading.Lock()
_REPO_ROOT = Path(__file__).resolve().parents[2]
_COST_DIR = _REPO_ROOT / "成本管理"
_DEFAULT_LEDGER_PATH = _COST_DIR / "llm_cost_ledger.csv"
_DEFAULT_PRICE_CONFIG_PATH = _COST_DIR / "llm_price_config.json"
_ENV_INPUT_PRICE = "LLM_COST_INPUT_PRICE_CNY_PER_MILLION_TOKENS"
_ENV_OUTPUT_PRICE = "LLM_COST_OUTPUT_PRICE_CNY_PER_MILLION_TOKENS"


def _iso_utc(now: datetime | None = None) -> str:
    safe_now = now or datetime.now(timezone.utc)
    return safe_now.isoformat().replace("+00:00", "Z")


def _to_text(value: Any) -> str:
    return str(value or "").strip()


def _to_decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        return None
    if not number.is_finite():
        return None
    return number


def _to_non_negative_decimal(value: Any, fallback: Decimal = Decimal("0")) -> Decimal:
    parsed = _to_decimal(value)
    if parsed is None:
        return fallback
    return parsed if parsed >= 0 else fallback


def _to_non_negative_int(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        return 0
    return parsed if parsed > 0 else 0


def _normalize_tier_rows(payload: Any) -> list[tuple[int, Decimal, Decimal]]:
    if not isinstance(payload, list):
        return []
    rows: list[tuple[int, Decimal, Decimal]] = []
    for item in payload:
        row = item if isinstance(item, Mapping) else {}
        max_prompt_tokens = _to_non_negative_int(row.get("max_prompt_tokens"))
        input_price = _to_non_negative_decimal(row.get("input_cny_per_million_tokens"), fallback=Decimal("-1"))
        output_price = _to_non_negative_decimal(row.get("output_cny_per_million_tokens"), fallback=Decimal("-1"))
        if max_prompt_tokens <= 0 or input_price < 0 or output_price < 0:
            continue
        rows.append((max_prompt_tokens, input_price, output_price))
    rows.sort(key=lambda item: item[0])
    return rows


def _normalize_tier_price_map(payload: Any) -> dict[str, list[tuple[int, Decimal, Decimal]]]:
    if not isinstance(payload, Mapping):
        return {}
    normalized: dict[str, list[tuple[int, Decimal, Decimal]]] = {}
    for raw_key, raw_value in payload.items():
        key = _to_text(raw_key)
        tiers = _normalize_tier_rows(raw_value)
        if not key or not tiers:
            continue
        normalized[key] = tiers
    return normalized


def _load_price_config(
    config_path: Path,
) -> tuple[dict[str, list[tuple[int, Decimal, Decimal]]], dict[str, list[tuple[int, Decimal, Decimal]]]]:
    model_prices = dict(_DEFAULT_MODEL_TIER_PRICES)
    provider_prices = dict(_DEFAULT_PROVIDER_TIER_PRICES)
    if not config_path.exists():
        return model_prices, provider_prices
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[DEBUG] Failed to parse llm price config {config_path}: {exc}")
        return model_prices, provider_prices
    if isinstance(payload, Mapping):
        model_prices.update(_normalize_tier_price_map(payload.get("model_tier_prices_cny_per_million_tokens")))
        provider_prices.update(_normalize_tier_price_map(payload.get("provider_tier_prices_cny_per_million_tokens")))
    return model_prices, provider_prices


def _select_tier_prices(
    *,
    prompt_tokens: int,
    tiers: list[tuple[int, Decimal, Decimal]],
) -> tuple[Decimal, Decimal, str]:
    if not tiers:
        return Decimal("0"), Decimal("0"), ""
    safe_prompt_tokens = max(0, int(prompt_tokens or 0))
    for max_prompt_tokens, input_price, output_price in tiers:
        if safe_prompt_tokens <= max_prompt_tokens:
            return input_price, output_price, ""
    _max_prompt_tokens, input_price, output_price = tiers[-1]
    return input_price, output_price, "prompt_over_1m_clamped"


def _resolve_prices(
    *,
    prompt_tokens: int,
    model: str,
    provider: str,
    config_path: Path,
) -> tuple[Decimal, Decimal, str, str]:
    env_input = _to_decimal(os.getenv(_ENV_INPUT_PRICE, ""))
    env_output = _to_decimal(os.getenv(_ENV_OUTPUT_PRICE, ""))
    if env_input is not None and env_input >= 0 and env_output is not None and env_output >= 0:
        return env_input, env_output, f"env:{_ENV_INPUT_PRICE}+{_ENV_OUTPUT_PRICE}", ""

    model_prices, provider_prices = _load_price_config(config_path)
    if model and model in model_prices:
        input_price, output_price, note = _select_tier_prices(prompt_tokens=prompt_tokens, tiers=model_prices[model])
        return input_price, output_price, f"config:model:{model}", note
    if provider and provider in provider_prices:
        input_price, output_price, note = _select_tier_prices(prompt_tokens=prompt_tokens, tiers=provider_prices[provider])
        return input_price, output_price, f"config:provider:{provider}", note
    return Decimal("0"), Decimal("0"), "fallback:zero", ""


def _format_decimal(value: Decimal, digits: int) -> str:
    return f"{float(value):.{digits}f}"


def append_llm_cost_record(
    *,
    scene: str,
    owner_id: str = "",
    stats: Mapping[str, Any] | None,
    llm_base_url: str = "",
    llm_provider_effective: str = "",
    llm_model_effective: str = "",
    provider_request_id: str = "",
    ledger_path: Path | None = None,
    config_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, str] | None:
    safe_scene = _to_text(scene)
    if not safe_scene:
        return None

    safe_stats: Mapping[str, Any] = stats if isinstance(stats, Mapping) else {}
    safe_owner_id = _to_text(owner_id)
    safe_provider_request_id = _to_text(provider_request_id or safe_stats.get("provider_request_id"))
    safe_base_url = _to_text(llm_base_url or safe_stats.get("llm_base_url"))
    safe_provider = _to_text(
        llm_provider_effective or safe_stats.get("llm_provider_effective") or safe_stats.get("translation_provider_effective")
    )
    safe_model = _to_text(
        llm_model_effective or safe_stats.get("llm_model_effective") or safe_stats.get("translation_model_effective")
    )

    prompt_tokens = _to_non_negative_int(safe_stats.get("prompt_tokens") or safe_stats.get("translation_prompt_tokens"))
    completion_tokens = _to_non_negative_int(
        safe_stats.get("completion_tokens") or safe_stats.get("translation_completion_tokens")
    )
    total_tokens = _to_non_negative_int(safe_stats.get("total_tokens") or safe_stats.get("translation_total_tokens"))
    if prompt_tokens <= 0:
        return None
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    if total_tokens <= 0:
        return None

    safe_ledger_path = Path(ledger_path) if ledger_path else _DEFAULT_LEDGER_PATH
    safe_config_path = Path(config_path) if config_path else _DEFAULT_PRICE_CONFIG_PATH
    input_price, output_price, price_source, tier_note = _resolve_prices(
        prompt_tokens=prompt_tokens,
        model=safe_model,
        provider=safe_provider,
        config_path=safe_config_path,
    )
    prompt_cost = (Decimal(prompt_tokens) / Decimal("1000000")) * input_price
    completion_cost = (Decimal(completion_tokens) / Decimal("1000000")) * output_price
    cost_cny = prompt_cost + completion_cost

    notes: list[str] = []
    if tier_note:
        notes.append(tier_note)
    if not safe_provider:
        notes.append("provider_missing")
    if not safe_model:
        notes.append("model_missing")
    if total_tokens != (prompt_tokens + completion_tokens):
        notes.append("total_tokens_adjusted")
    note = ";".join(notes)

    row = {
        "recorded_at": _iso_utc(now),
        "scene": safe_scene,
        "owner_id": safe_owner_id,
        "provider_request_id": safe_provider_request_id,
        "llm_base_url": safe_base_url,
        "llm_provider_effective": safe_provider,
        "llm_model_effective": safe_model,
        "prompt_tokens": str(prompt_tokens),
        "completion_tokens": str(completion_tokens),
        "total_tokens": str(total_tokens),
        "input_price_cny_per_million_tokens": _format_decimal(input_price, 8),
        "output_price_cny_per_million_tokens": _format_decimal(output_price, 8),
        "cost_cny": _format_decimal(cost_cny, 8),
        "price_source": price_source,
        "note": note,
    }

    safe_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        header_required = not safe_ledger_path.exists() or safe_ledger_path.stat().st_size == 0
        with safe_ledger_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS)
            if header_required:
                writer.writeheader()
            writer.writerow(row)
    print(
        f"[DEBUG] LLM cost ledger appended scene={safe_scene} owner={safe_owner_id or '-'} "
        f"model={safe_model or '-'} prompt={prompt_tokens} completion={completion_tokens} cost={row['cost_cny']}"
    )
    return row
