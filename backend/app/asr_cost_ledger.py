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
    "asr_provider_effective",
    "whisper_runtime",
    "whisper_model_effective",
    "billed_seconds",
    "unit_price_cny_per_sec",
    "cost_cny",
    "price_source",
    "note",
]

_DEFAULT_MODEL_PRICES = {
    "paraformer-v2": Decimal("0.00008"),
    "paraformer-realtime-v2": Decimal("0.00024"),
    "qwen3-asr-flash-filetrans": Decimal("0.00022"),
}

_DEFAULT_PROVIDER_PRICES = {
    "cloud_paraformer_v2": Decimal("0.00008"),
    "cloud_paraformer_realtime_v2": Decimal("0.00024"),
    "cloud_qwen3_asr_flash_filetrans": Decimal("0.00022"),
    "local_whisperx": Decimal("0"),
    "local_faster_whisper": Decimal("0"),
}

_WRITE_LOCK = threading.Lock()

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COST_DIR = _REPO_ROOT / "成本管理"
_DEFAULT_LEDGER_PATH = _COST_DIR / "asr_cost_ledger.csv"
_DEFAULT_PRICE_CONFIG_PATH = _COST_DIR / "asr_price_config.json"


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


def _to_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_price_map(payload: Any) -> dict[str, Decimal]:
    if not isinstance(payload, Mapping):
        return {}
    normalized: dict[str, Decimal] = {}
    for raw_key, raw_value in payload.items():
        key = _to_text(raw_key)
        if not key:
            continue
        price = _to_non_negative_decimal(raw_value, fallback=Decimal("-1"))
        if price < 0:
            continue
        normalized[key] = price
    return normalized


def _load_price_config(config_path: Path) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    model_prices = dict(_DEFAULT_MODEL_PRICES)
    provider_prices = dict(_DEFAULT_PROVIDER_PRICES)
    if not config_path.exists():
        return model_prices, provider_prices

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[DEBUG] Failed to parse ASR price config {config_path}: {exc}")
        return model_prices, provider_prices

    if isinstance(payload, Mapping):
        model_prices.update(_normalize_price_map(payload.get("model_prices_cny_per_sec")))
        provider_prices.update(_normalize_price_map(payload.get("provider_prices_cny_per_sec")))
    return model_prices, provider_prices


def _resolve_unit_price(
    *,
    model: str,
    provider: str,
    config_path: Path,
) -> tuple[Decimal, str]:
    env_price = _to_decimal(os.getenv("ASR_COST_UNIT_PRICE_CNY_PER_SEC", ""))
    if env_price is not None and env_price >= 0:
        return env_price, "env:ASR_COST_UNIT_PRICE_CNY_PER_SEC"

    model_prices, provider_prices = _load_price_config(config_path)
    if model and model in model_prices:
        return model_prices[model], f"config:model:{model}"
    if provider and provider in provider_prices:
        return provider_prices[provider], f"config:provider:{provider}"
    return Decimal("0"), "fallback:zero"


def _read_existing_job_ids(ledger_path: Path) -> set[str]:
    if not ledger_path.exists():
        return set()
    try:
        with ledger_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return {_to_text(row.get("job_id")) for row in reader if _to_text(row.get("job_id"))}
    except Exception as exc:
        print(f"[DEBUG] Failed to read ASR ledger for dedupe {ledger_path}: {exc}")
        return set()


def _format_decimal(value: Decimal, digits: int) -> str:
    return f"{float(value):.{digits}f}"


def append_asr_cost_record(
    *,
    job_id: str,
    stats: Mapping[str, Any] | None,
    whisper_runtime: str = "",
    whisper_model_effective: str = "",
    asr_provider_effective: str = "",
    ledger_path: Path | None = None,
    config_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, str] | None:
    safe_job_id = _to_text(job_id)
    if not safe_job_id:
        return None

    safe_stats: Mapping[str, Any] = stats if isinstance(stats, Mapping) else {}
    provider = _to_text(asr_provider_effective or safe_stats.get("asr_provider_effective"))
    runtime = _to_text(whisper_runtime or safe_stats.get("whisper_runtime"))
    model = _to_text(
        whisper_model_effective
        or safe_stats.get("whisper_model_effective")
        or safe_stats.get("whisper_model_requested")
    )
    billed_seconds = _to_non_negative_decimal(safe_stats.get("duration_sec"), fallback=Decimal("0"))
    safe_ledger_path = Path(ledger_path) if ledger_path else _DEFAULT_LEDGER_PATH
    safe_config_path = Path(config_path) if config_path else _DEFAULT_PRICE_CONFIG_PATH
    unit_price, price_source = _resolve_unit_price(model=model, provider=provider, config_path=safe_config_path)
    cost_cny = billed_seconds * unit_price

    notes: list[str] = []
    if billed_seconds <= 0:
        notes.append("duration_sec_missing_or_zero")
    if provider.startswith("local") and unit_price == 0:
        notes.append("local_asr_default_zero")
    if not provider:
        notes.append("provider_missing")
    note = ";".join(notes)

    row = {
        "recorded_at": _iso_utc(now),
        "job_id": safe_job_id,
        "asr_provider_effective": provider,
        "whisper_runtime": runtime,
        "whisper_model_effective": model,
        "billed_seconds": _format_decimal(billed_seconds, 6),
        "unit_price_cny_per_sec": _format_decimal(unit_price, 8),
        "cost_cny": _format_decimal(cost_cny, 8),
        "price_source": price_source,
        "note": note,
    }

    safe_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        existing_job_ids = _read_existing_job_ids(safe_ledger_path)
        if safe_job_id in existing_job_ids:
            print(f"[DEBUG] Skip duplicate ASR cost record job_id={safe_job_id}")
            return None

        header_required = not safe_ledger_path.exists() or safe_ledger_path.stat().st_size == 0
        with safe_ledger_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS)
            if header_required:
                writer.writeheader()
            writer.writerow(row)
        print(
            f"[DEBUG] ASR cost ledger appended job_id={safe_job_id} "
            f"seconds={row['billed_seconds']} unit={row['unit_price_cny_per_sec']} cost={row['cost_cny']}"
        )
    return row
