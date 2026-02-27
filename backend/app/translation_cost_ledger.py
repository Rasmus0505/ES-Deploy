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
    "job_id",
    "translation_provider_effective",
    "translation_model_effective",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_price_cny_per_million_tokens",
    "output_price_cny_per_million_tokens",
    "cost_cny",
    "price_source",
    "note",
]

_DEFAULT_MODEL_PRICES = {
    "qwen-mt-flash": (
        Decimal("0.7"),
        Decimal("1.95"),
    ),
}

_DEFAULT_PROVIDER_PRICES = {
    "dashscope_qwen_mt_flash": (
        Decimal("0.7"),
        Decimal("1.95"),
    ),
}

_WRITE_LOCK = threading.Lock()
_REPO_ROOT = Path(__file__).resolve().parents[2]
_COST_DIR = _REPO_ROOT / "成本管理"
_DEFAULT_LEDGER_PATH = _COST_DIR / "translation_cost_ledger.csv"
_DEFAULT_PRICE_CONFIG_PATH = _COST_DIR / "translation_price_config.json"
_ENV_INPUT_PRICE = "TRANSLATION_COST_INPUT_PRICE_CNY_PER_MILLION_TOKENS"
_ENV_OUTPUT_PRICE = "TRANSLATION_COST_OUTPUT_PRICE_CNY_PER_MILLION_TOKENS"


def _iso_utc(now: datetime | None = None) -> str:
    safe_now = now or datetime.now(timezone.utc)
    return safe_now.isoformat().replace("+00:00", "Z")


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


def _to_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_token_price_map(payload: Any) -> dict[str, tuple[Decimal, Decimal]]:
    if not isinstance(payload, Mapping):
        return {}
    normalized: dict[str, tuple[Decimal, Decimal]] = {}
    for raw_key, raw_value in payload.items():
        key = _to_text(raw_key)
        value = raw_value if isinstance(raw_value, Mapping) else {}
        input_price = _to_non_negative_decimal(value.get("input_cny_per_million_tokens"), fallback=Decimal("-1"))
        output_price = _to_non_negative_decimal(value.get("output_cny_per_million_tokens"), fallback=Decimal("-1"))
        if not key or input_price < 0 or output_price < 0:
            continue
        normalized[key] = (input_price, output_price)
    return normalized


def _load_price_config(config_path: Path) -> tuple[dict[str, tuple[Decimal, Decimal]], dict[str, tuple[Decimal, Decimal]]]:
    model_prices = dict(_DEFAULT_MODEL_PRICES)
    provider_prices = dict(_DEFAULT_PROVIDER_PRICES)
    if not config_path.exists():
        return model_prices, provider_prices
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[DEBUG] Failed to parse translation price config {config_path}: {exc}")
        return model_prices, provider_prices

    if isinstance(payload, Mapping):
        model_prices.update(_normalize_token_price_map(payload.get("model_prices_cny_per_million_tokens")))
        provider_prices.update(_normalize_token_price_map(payload.get("provider_prices_cny_per_million_tokens")))
    return model_prices, provider_prices


def _resolve_prices(
    *,
    model: str,
    provider: str,
    config_path: Path,
) -> tuple[Decimal, Decimal, str]:
    env_input = _to_decimal(os.getenv(_ENV_INPUT_PRICE, ""))
    env_output = _to_decimal(os.getenv(_ENV_OUTPUT_PRICE, ""))
    if env_input is not None and env_input >= 0 and env_output is not None and env_output >= 0:
        return env_input, env_output, f"env:{_ENV_INPUT_PRICE}+{_ENV_OUTPUT_PRICE}"

    model_prices, provider_prices = _load_price_config(config_path)
    if model and model in model_prices:
        input_price, output_price = model_prices[model]
        return input_price, output_price, f"config:model:{model}"
    if provider and provider in provider_prices:
        input_price, output_price = provider_prices[provider]
        return input_price, output_price, f"config:provider:{provider}"
    return Decimal("0"), Decimal("0"), "fallback:zero"


def _read_existing_job_ids(ledger_path: Path) -> set[str]:
    if not ledger_path.exists():
        return set()
    try:
        with ledger_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return {_to_text(row.get("job_id")) for row in reader if _to_text(row.get("job_id"))}
    except Exception as exc:
        print(f"[DEBUG] Failed to read translation ledger for dedupe {ledger_path}: {exc}")
        return set()


def _format_decimal(value: Decimal, digits: int) -> str:
    return f"{float(value):.{digits}f}"


def append_translation_cost_record(
    *,
    job_id: str,
    stats: Mapping[str, Any] | None,
    translation_provider_effective: str = "",
    translation_model_effective: str = "",
    ledger_path: Path | None = None,
    config_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, str] | None:
    safe_job_id = _to_text(job_id)
    if not safe_job_id:
        return None

    safe_stats: Mapping[str, Any] = stats if isinstance(stats, Mapping) else {}
    provider = _to_text(translation_provider_effective or safe_stats.get("translation_provider_effective"))
    model = _to_text(translation_model_effective or safe_stats.get("translation_model_effective"))
    prompt_tokens = _to_non_negative_int(safe_stats.get("translation_prompt_tokens"))
    completion_tokens = _to_non_negative_int(safe_stats.get("translation_completion_tokens"))
    total_tokens = _to_non_negative_int(safe_stats.get("translation_total_tokens"))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    if total_tokens <= 0:
        return None

    safe_ledger_path = Path(ledger_path) if ledger_path else _DEFAULT_LEDGER_PATH
    safe_config_path = Path(config_path) if config_path else _DEFAULT_PRICE_CONFIG_PATH
    input_price, output_price, price_source = _resolve_prices(
        model=model,
        provider=provider,
        config_path=safe_config_path,
    )
    prompt_cost = (Decimal(prompt_tokens) / Decimal("1000000")) * input_price
    completion_cost = (Decimal(completion_tokens) / Decimal("1000000")) * output_price
    cost_cny = prompt_cost + completion_cost

    notes: list[str] = []
    if not provider:
        notes.append("provider_missing")
    if not model:
        notes.append("model_missing")
    if total_tokens != (prompt_tokens + completion_tokens):
        notes.append("total_tokens_adjusted")
    note = ";".join(notes)

    row = {
        "recorded_at": _iso_utc(now),
        "job_id": safe_job_id,
        "translation_provider_effective": provider,
        "translation_model_effective": model,
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
        existing_job_ids = _read_existing_job_ids(safe_ledger_path)
        if safe_job_id in existing_job_ids:
            print(f"[DEBUG] Skip duplicate translation cost record job_id={safe_job_id}")
            return None

        header_required = not safe_ledger_path.exists() or safe_ledger_path.stat().st_size == 0
        with safe_ledger_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS)
            if header_required:
                writer.writeheader()
            writer.writerow(row)
        print(
            f"[DEBUG] Translation cost ledger appended job_id={safe_job_id} "
            f"prompt={row['prompt_tokens']} completion={row['completion_tokens']} cost={row['cost_cny']}"
        )
    return row
